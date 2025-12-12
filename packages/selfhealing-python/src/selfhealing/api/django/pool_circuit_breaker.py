"""
Pool-aware Circuit Breaker Middleware for Django.

Pool이 고갈되면 즉시 503을 반환하여 시스템 멈춤을 방지합니다.
Pool 대기(block) 대신 Fail Fast 전략 사용.

핵심 원리:
1. 요청 도착 시 Pool 상태 체크 (non-blocking)
2. Pool 고갈 시 즉시 503 반환 (Pool 대기하지 않음!)
3. Circuit Breaker 상태로 관리하여 자동 복구
"""

import time
import threading
import logging
from typing import Optional
from django.http import JsonResponse
from django.db import connections
from django.conf import settings

logger = logging.getLogger(__name__)


class PoolCircuitBreaker:
    """
    Pool 상태 기반 Circuit Breaker.

    상태:
    - CLOSED: 정상 - 모든 요청 허용
    - OPEN: 고갈 - 모든 요청 즉시 거부 (503)
    - HALF_OPEN: 복구 테스트 중 - 일부 요청만 허용
    """

    # 싱글톤 인스턴스
    _instance = None
    _lock = threading.Lock()

    # 상태 상수
    CLOSED = "CLOSED"  # 정상
    OPEN = "OPEN"  # 차단 중
    HALF_OPEN = "HALF_OPEN"  # 복구 테스트 중

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._state = self.CLOSED
        self._state_lock = threading.Lock()

        # 설정값 (환경 변수로 오버라이드 가능)
        import os

        self._failure_threshold = int(os.getenv("POOL_CB_FAILURE_THRESHOLD", "3"))  # 3회 실패 시 OPEN
        self._success_threshold = int(os.getenv("POOL_CB_SUCCESS_THRESHOLD", "2"))  # 2회 성공 시 CLOSED
        self._recovery_timeout = int(os.getenv("POOL_CB_RECOVERY_TIMEOUT", "10"))  # 10초 후 HALF_OPEN
        self._half_open_max_requests = int(os.getenv("POOL_CB_HALF_OPEN_MAX", "3"))

        # 상태 추적
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._open_time = None
        self._half_open_requests = 0

        # 통계
        self._stats = {
            "total_requests": 0,
            "rejected_requests": 0,
            "pool_exhaustion_count": 0,
            "recovery_count": 0,
            "state_changes": [],
        }

        logger.info("[PoolCircuitBreaker] Initialized - Fail Fast enabled!")

    @property
    def state(self) -> str:
        """현재 상태"""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: str):
        """상태 변경"""
        with self._state_lock:
            if self._state != new_state:
                old_state = self._state
                self._state = new_state

                timestamp = time.time()
                self._stats["state_changes"].append(
                    {
                        "from": old_state,
                        "to": new_state,
                        "time": timestamp,
                    }
                )

                logger.warning(f"[PoolCircuitBreaker] State: {old_state} → {new_state}")

                if new_state == self.OPEN:
                    self._open_time = timestamp
                    self._stats["pool_exhaustion_count"] += 1
                elif new_state == self.CLOSED and old_state != self.CLOSED:
                    self._stats["recovery_count"] += 1

    def check_pool_status(self) -> dict:
        """Pool 상태 조회 (non-blocking)"""
        try:
            # django-db-connection-pool의 pool_container를 통해 접근
            try:
                from dj_db_conn_pool.core.mixins.core import pool_container

                has_pool = pool_container.has("default")
                logger.info(f"[PoolCircuitBreaker] pool_container.has('default') = {has_pool}")

                if has_pool:
                    pool = pool_container.get("default")
                    pool_size = pool.size()
                    checkedout = pool.checkedout()
                    checkedin = pool.checkedin()
                    overflow = pool.overflow()
                    max_overflow = getattr(pool, "_max_overflow", 2)
                    total_capacity = pool_size + max_overflow

                    logger.info(f"[PoolCircuitBreaker] Pool: checkedout={checkedout}/{total_capacity}, checkedin={checkedin}")

                    # Pool 고갈 판단:
                    # Pool 크기 3, max_overflow 0일 때:
                    # - checkedout >= 3 이면 완전 고갈
                    # - checkedin == 0 이면 사용 가능한 연결 없음
                    is_at_capacity = checkedout >= total_capacity
                    is_overflow_maxed = overflow >= max_overflow if max_overflow > 0 else True
                    is_no_available = checkedin == 0

                    # 더 적극적인 고갈 감지: 사용 가능 연결이 0이고 모든 연결이 사용 중
                    is_exhausted = is_no_available and checkedout >= pool_size

                    # 66% 이상 사용 시 near exhaustion (Pool 3개면 2개 사용 시)
                    is_near_exhaustion = checkedout >= total_capacity * 0.66

                    if is_exhausted:
                        logger.warning(
                            f"[PoolCircuitBreaker] EXHAUSTED! checkedout={checkedout}, checkedin={checkedin}, pool_size={pool_size}"
                        )

                    return {
                        "available": True,
                        "pool_size": pool_size,
                        "checkedout": checkedout,
                        "checkedin": checkedin,
                        "overflow": overflow,
                        "max_overflow": max_overflow,
                        "total_capacity": total_capacity,
                        "usage_percent": (checkedout / total_capacity * 100) if total_capacity > 0 else 0,
                        "is_exhausted": is_exhausted,
                        "is_near_exhaustion": is_near_exhaustion,
                        # 디버깅용
                        "_is_at_capacity": is_at_capacity,
                        "_is_overflow_maxed": is_overflow_maxed,
                        "_is_no_available": is_no_available,
                    }
                else:
                    # pool_container가 비어있음 - Django connection에서 직접 접근 시도
                    # 단, ensure_connection()은 호출하지 않음 (블로킹 방지)
                    logger.debug(f"[PoolCircuitBreaker] pool_container empty, trying direct access...")

                    conn = connections["default"]
                    # 이미 연결이 있는 경우에만 Pool 접근
                    if hasattr(conn, "connection") and conn.connection is not None:
                        raw_conn = conn.connection
                        if hasattr(raw_conn, "_pool"):
                            pool = raw_conn._pool
                            pool_size = pool.size()
                            checkedout = pool.checkedout()
                            checkedin = pool.checkedin()
                            overflow = pool.overflow()
                            max_overflow = getattr(pool, "_max_overflow", 0)
                            total_capacity = pool_size + max_overflow

                            is_exhausted = checkedin == 0 and checkedout >= pool_size
                            is_near_exhaustion = checkedout >= total_capacity * 0.66

                            logger.info(
                                f"[PoolCircuitBreaker] Direct access: checkedout={checkedout}/{total_capacity}, checkedin={checkedin}"
                            )

                            if is_exhausted:
                                logger.warning(
                                    f"[PoolCircuitBreaker] EXHAUSTED! checkedout={checkedout}, checkedin={checkedin}, pool_size={pool_size}"
                                )

                            return {
                                "available": True,
                                "pool_size": pool_size,
                                "checkedout": checkedout,
                                "checkedin": checkedin,
                                "overflow": overflow,
                                "max_overflow": max_overflow,
                                "total_capacity": total_capacity,
                                "usage_percent": (checkedout / total_capacity * 100) if total_capacity > 0 else 0,
                                "is_exhausted": is_exhausted,
                                "is_near_exhaustion": is_near_exhaustion,
                            }

                    # 연결이 아직 없음 - Pool도 없음 (정상, 첫 요청 전)
                    logger.debug(f"[PoolCircuitBreaker] No connection yet - pool not initialized")
                    return {
                        "available": False,
                        "reason": "Pool not initialized yet",
                        "is_exhausted": False,
                        "is_near_exhaustion": False,
                    }
            except ImportError as e:
                # django-db-connection-pool 미설치
                logger.warning(f"[PoolCircuitBreaker] dj_db_conn_pool not available: {e}")
                return {
                    "available": False,
                    "reason": "dj_db_conn_pool not installed",
                    "is_exhausted": False,
                    "is_near_exhaustion": False,
                }

            # Fallback: pool_container가 없고 import도 실패한 경우
            # 연결 시도 없이 바로 반환 (ensure_connection 제거!)
            logger.debug(f"[PoolCircuitBreaker] No pool_container available")
            return {
                "available": False,
                "reason": "No pool available",
                "is_exhausted": False,
                "is_near_exhaustion": False,
            }

        except Exception as e:
            logger.error(f"[PoolCircuitBreaker] Pool status check failed: {e}")
            return {
                "available": False,
                "reason": str(e),
            }

    def should_allow_request(self) -> tuple[bool, Optional[str]]:
        """
        요청 허용 여부 판단.

        Returns:
            (allow: bool, reason: Optional[str])
        """
        self._stats["total_requests"] += 1

        with self._state_lock:
            current_state = self._state

            if current_state == self.CLOSED:
                # 정상 상태 - Pool 상태 체크
                pool_status = self.check_pool_status()

                if pool_status.get("is_exhausted"):
                    # Pool 고갈 감지! 즉시 OPEN으로 전환
                    logger.error(
                        f"[PoolCircuitBreaker] Pool EXHAUSTED! "
                        f"checkedout={pool_status.get('checkedout')}/{pool_status.get('total_capacity')}"
                    )
                    self._set_state(self.OPEN)
                    self._stats["rejected_requests"] += 1
                    self._stats["pool_exhaustion_count"] += 1
                    return (False, "Pool exhausted - Circuit OPEN")

                elif pool_status.get("is_near_exhaustion"):
                    # 80% 이상 사용 중 - 경고 및 failure count 증가
                    self._failure_count += 1
                    usage = pool_status.get("usage_percent", 0)
                    logger.warning(
                        f"[PoolCircuitBreaker] Pool usage HIGH: {usage:.1f}% "
                        f"Failures: {self._failure_count}/{self._failure_threshold}"
                    )

                    # Threshold 도달 시 OPEN
                    if self._failure_count >= self._failure_threshold:
                        self._set_state(self.OPEN)
                        self._stats["rejected_requests"] += 1
                        return (False, f"Pool near exhaustion ({usage:.1f}%) - Circuit OPEN")

                    # 90% 이상이면 50% 확률로 거부 (부하 분산)
                    if usage >= 90:
                        import random

                        if random.random() < 0.5:
                            self._stats["rejected_requests"] += 1
                            return (False, f"Pool critical ({usage:.1f}%) - load shedding")

                    return (True, None)

                else:
                    # 정상 - failure count 리셋
                    self._failure_count = 0
                    return (True, None)

            elif current_state == self.OPEN:
                # 차단 상태 - 복구 timeout 체크
                if self._open_time:
                    elapsed = time.time() - self._open_time
                    if elapsed >= self._recovery_timeout:
                        # Recovery timeout 경과 - HALF_OPEN으로 전환
                        self._set_state(self.HALF_OPEN)
                        self._half_open_requests = 0
                        self._success_count = 0
                        # 첫 번째 요청 허용
                        self._half_open_requests += 1
                        return (True, "Testing recovery (HALF_OPEN)")

                # 여전히 차단
                self._stats["rejected_requests"] += 1
                remaining = self._recovery_timeout - (time.time() - (self._open_time or 0))
                return (False, f"Circuit OPEN - retry in {remaining:.1f}s")

            elif current_state == self.HALF_OPEN:
                # 복구 테스트 중
                if self._half_open_requests < self._half_open_max_requests:
                    self._half_open_requests += 1
                    return (True, "Testing recovery (HALF_OPEN)")
                else:
                    # 테스트 요청 수 초과 - 대기
                    self._stats["rejected_requests"] += 1
                    return (False, "HALF_OPEN test in progress - wait")

        return (True, None)

    def record_success(self):
        """요청 성공 기록"""
        with self._state_lock:
            if self._state == self.HALF_OPEN:
                self._success_count += 1
                logger.info(f"[PoolCircuitBreaker] HALF_OPEN success: " f"{self._success_count}/{self._success_threshold}")

                if self._success_count >= self._success_threshold:
                    # 충분히 성공 - 복구 완료!
                    self._set_state(self.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("[PoolCircuitBreaker] 🎉 RECOVERED! Circuit CLOSED")

            elif self._state == self.CLOSED:
                # 정상 상태에서 성공 - 실패 카운터 리셋
                self._failure_count = 0

    def record_failure(self):
        """요청 실패 기록"""
        with self._state_lock:
            self._last_failure_time = time.time()

            if self._state == self.HALF_OPEN:
                # 복구 테스트 실패 - 다시 OPEN
                self._set_state(self.OPEN)
                logger.warning("[PoolCircuitBreaker] Recovery failed - back to OPEN")

            elif self._state == self.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._set_state(self.OPEN)

    def get_stats(self) -> dict:
        """통계 반환"""
        return {
            "state": self._state,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "stats": self._stats.copy(),
            "pool_status": self.check_pool_status(),
        }

    def reset(self):
        """상태 초기화 (테스트용)"""
        with self._state_lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._open_time = None
            self._half_open_requests = 0
            logger.info("[PoolCircuitBreaker] Reset to CLOSED")


# 전역 인스턴스
pool_circuit_breaker = PoolCircuitBreaker()


class PoolCircuitBreakerMiddleware:
    """
    Django Middleware: Pool 고갈 시 즉시 503 반환.

    사용법:
    MIDDLEWARE = [
        ...
        'selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware',
        ...
    ]
    """

    # Circuit Breaker 적용 제외 경로
    EXCLUDED_PATHS = [
        "/health/",
        "/api/self-healing/health/",
        "/api/self-healing/circuit-breaker/",  # CB 관리 API는 제외
        "/admin/",
        "/static/",
        "/media/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response
        self._request_count = 0
        self._log_interval = 50  # 50 요청마다 Pool 상태 로깅
        logger.info("[PoolCircuitBreakerMiddleware] Initialized - Fail Fast enabled!")

    def __call__(self, request):
        # 제외 경로 체크
        path = request.path
        for excluded in self.EXCLUDED_PATHS:
            if path.startswith(excluded):
                return self.get_response(request)

        # 주기적 Pool 상태 로깅
        self._request_count += 1
        if self._request_count % self._log_interval == 0:
            pool_status = pool_circuit_breaker.check_pool_status()
            logger.info(
                f"[PoolCircuitBreakerMiddleware] Pool status (every {self._log_interval} reqs): "
                f"checkedout={pool_status.get('checkedout', '?')}/{pool_status.get('total_capacity', '?')} "
                f"({pool_status.get('usage_percent', 0):.1f}%) "
                f"exhausted={pool_status.get('is_exhausted', False)}"
            )

        # Circuit Breaker 체크
        cb = pool_circuit_breaker
        allow, reason = cb.should_allow_request()

        if not allow:
            # 즉시 거부! (Fail Fast)
            logger.warning(f"[PoolCircuitBreakerMiddleware] Rejected: {path} - {reason}")
            return JsonResponse(
                {
                    "error": "Service temporarily unavailable",
                    "reason": reason,
                    "circuit_state": cb.state,
                    "retry_after": cb._recovery_timeout,
                },
                status=503,
                headers={"Retry-After": str(cb._recovery_timeout)},
            )

        # 요청 처리
        try:
            response = self.get_response(request)

            # 성공 여부 판단
            if response.status_code < 500:
                cb.record_success()
            else:
                # 500 에러 - Pool 고갈 여부 확인
                try:
                    # 현재 Pool 상태 확인
                    pool_status = cb.check_pool_status()
                    if pool_status.get("is_exhausted", False):
                        # Pool 고갈 상태! 즉시 OPEN
                        logger.error(
                            f"[PoolCircuitBreakerMiddleware] 500 error with Pool exhaustion! "
                            f"Path: {path}, Pool: {pool_status}"
                        )
                        cb._failure_count = cb._failure_threshold  # 즉시 threshold 도달
                        cb.record_failure()
                    else:
                        cb.record_failure()
                except Exception:
                    cb.record_failure()

            return response

        except Exception as e:
            # Pool Timeout 또는 DB 연결 오류 감지
            error_str = str(e).lower()

            # Pool 고갈 관련 예외 패턴
            pool_exhaustion_patterns = [
                "timeout",
                "queuepool limit",
                "pool exhausted",
                "connection pool",
                "too many connections",
                "can't get connection",
                "no connections available",
            ]

            is_pool_exhaustion = any(p in error_str for p in pool_exhaustion_patterns)

            if is_pool_exhaustion:
                # Pool 고갈 예외! 즉시 OPEN 전환
                logger.error(f"[PoolCircuitBreakerMiddleware] Pool exhaustion detected! {e}")
                cb._failure_count = cb._failure_threshold  # 즉시 threshold 도달
                cb.record_failure()

                # Pool 상태 로깅
                pool_status = cb.check_pool_status()
                logger.error(
                    f"[PoolCircuitBreakerMiddleware] Pool status at exhaustion: "
                    f"checkedout={pool_status.get('checkedout', '?')}/{pool_status.get('total_capacity', '?')} "
                    f"overflow={pool_status.get('overflow', '?')}"
                )

                # 503 반환 (재시도 유도)
                return JsonResponse(
                    {
                        "error": "Database pool exhausted",
                        "reason": str(e),
                        "circuit_state": cb.state,
                        "retry_after": cb._recovery_timeout,
                    },
                    status=503,
                    headers={"Retry-After": str(cb._recovery_timeout)},
                )
            else:
                # 일반 오류
                cb.record_failure()
                logger.error(f"[PoolCircuitBreakerMiddleware] Request failed: {e}")
                raise


# Circuit Breaker 상태 조회 API
def circuit_breaker_status(request):
    """Circuit Breaker 상태 조회 API"""
    cb = pool_circuit_breaker
    stats = cb.get_stats()

    return JsonResponse(
        {
            "circuit_breaker": {
                "state": stats["state"],
                "failure_count": stats["failure_count"],
                "success_count": stats["success_count"],
                "failure_threshold": cb._failure_threshold,
                "success_threshold": cb._success_threshold,
                "recovery_timeout_seconds": cb._recovery_timeout,
            },
            "pool": stats["pool_status"],
            "statistics": stats["stats"],
        }
    )


def circuit_breaker_reset(request):
    """Circuit Breaker 리셋 API (관리용)"""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    cb = pool_circuit_breaker
    cb.reset()

    return JsonResponse(
        {
            "message": "Circuit Breaker reset to CLOSED",
            "state": cb.state,
        }
    )

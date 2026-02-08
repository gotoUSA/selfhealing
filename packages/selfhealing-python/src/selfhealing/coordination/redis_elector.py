"""
Redis 기반 Leader Election 구현.

Redis SETNX + EXPIRE를 사용한 분산 리더 선출.
Fencing Token, 리전 우선순위, Self-Fencing, 비동기 콜백 지원.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from selfhealing.coordination.base import (
    LeaderElector,
    LeaderInfo,
    LeadershipState,
)
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
)

if TYPE_CHECKING:
    from selfhealing.coordination.metrics import LeaderElectorMetrics

logger = logging.getLogger(__name__)


class RedisLeaderElector(LeaderElector):
    """
    Redis 기반 Leader Elector.

    알고리즘:
    1. SET NX EX로 리더 키 획득 시도
    2. 획득 성공 → 리더 (Fencing Token 증가)
    3. 획득 실패 → 팔로워
    4. 리더는 주기적으로 EXPIRE 갱신
    5. 리더 키 만료 → 재선출
    """

    # Lua 스크립트: 리더 획득 + Fencing Token 증가
    LUA_ACQUIRE_WITH_FENCING = """
    local leader_key = KEYS[1]
    local fencing_key = KEYS[2]
    local value = ARGV[1]
    local ttl = tonumber(ARGV[2])

    local acquired = redis.call("set", leader_key, value, "NX", "EX", ttl)
    if acquired then
        local token = redis.call("incr", fencing_key)
        return token
    else
        return 0
    end
    """

    # Lua 스크립트: 우선순위 기반 조건부 획득
    LUA_ACQUIRE_WITH_PRIORITY = """
    local leader_key = KEYS[1]
    local fencing_key = KEYS[2]
    local new_value = ARGV[1]
    local ttl = tonumber(ARGV[2])
    local new_priority = tonumber(ARGV[3])

    local current = redis.call("get", leader_key)
    if current == false then
        redis.call("set", leader_key, new_value, "EX", ttl)
        local token = redis.call("incr", fencing_key)
        return token
    end

    local current_data = cjson.decode(current)
    local current_priority = current_data.region_priority or 100

    if new_priority < current_priority then
        redis.call("set", leader_key, new_value, "EX", ttl)
        local token = redis.call("incr", fencing_key)
        return token
    end

    return 0
    """

    # Lua 스크립트: 조건부 키 삭제 (자신의 리더십만 반납)
    LUA_RELEASE = """
    local leader_key = KEYS[1]
    local node_id = ARGV[1]

    local current = redis.call("get", leader_key)
    if current then
        local current_data = cjson.decode(current)
        if current_data.node_id == node_id then
            return redis.call("del", leader_key)
        end
    end
    return 0
    """

    # Lua 스크립트: 조건부 TTL 갱신 (자신의 리더십만 갱신)
    LUA_RENEW = """
    local leader_key = KEYS[1]
    local node_id = ARGV[1]
    local ttl = tonumber(ARGV[2])

    local current = redis.call("get", leader_key)
    if current then
        local current_data = cjson.decode(current)
        if current_data.node_id == node_id then
            return redis.call("expire", leader_key, ttl)
        end
    end
    return 0
    """

    def __init__(
        self,
        resource_name: str,
        settings: LeaderElectionSettings | None = None,
        redis_client: Any | None = None,
    ):
        """
        Redis Leader Elector 초기화.

        Args:
            resource_name: 리소스 이름 (예: "dlq-consumer", "scheduler")
            settings: 설정 (None이면 기본 설정 사용)
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._resource_name = resource_name
        self._settings = settings or get_leader_election_settings()
        self._redis = redis_client

        self._node_id = self._settings.get_node_id()
        self._leader_key = f"{self._settings.redis_key_prefix}{resource_name}"
        self._fencing_key = f"{self._settings.redis_key_prefix}fencing_token:{resource_name}"

        self._lock = threading.RLock()
        self._state = LeadershipState.NOT_STARTED
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Fencing Token
        self._fencing_token: int = 0

        # 콜백
        self._on_become_callbacks: list[Callable[[], None]] = []
        self._on_lose_callbacks: list[Callable[[], None]] = []

        # 비동기 콜백 실행용 스레드 풀
        self._callback_executor: ThreadPoolExecutor | None = None

        # Lua 스크립트
        self._acquire_script: Any = None
        self._release_script: Any = None
        self._renew_script: Any = None

        # 메트릭
        self._metrics: LeaderElectorMetrics | None = None

    def _get_redis(self) -> Any:
        """Redis 클라이언트 반환 (lazy initialization)."""
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    def _get_callback_executor(self) -> ThreadPoolExecutor:
        """콜백 실행용 스레드 풀 (lazy initialization)."""
        if self._callback_executor is None:
            self._callback_executor = ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix=f"LeaderCallback-{self._resource_name}",
            )
        return self._callback_executor

    def _get_metrics(self) -> "LeaderElectorMetrics | None":
        """메트릭 헬퍼 반환 (lazy initialization)."""
        if self._metrics is None:
            try:
                from selfhealing.coordination.metrics import LeaderElectorMetrics

                self._metrics = LeaderElectorMetrics(self._resource_name, self._node_id)
            except ImportError:
                pass
        return self._metrics

    @property
    def resource_name(self) -> str:
        """리소스 이름."""
        return self._resource_name

    @property
    def state(self) -> LeadershipState:
        """현재 리더십 상태."""
        with self._lock:
            return self._state

    def is_leader(self) -> bool:
        """현재 리더인지 확인."""
        with self._lock:
            return self._state == LeadershipState.LEADER

    def get_fencing_token(self) -> int:
        """현재 Fencing Token 반환."""
        return self._fencing_token

    def is_lease_valid(self) -> bool:
        """현재 Lease가 유효한지 확인 (Self-Fencing)."""
        if not self.is_leader():
            return False

        try:
            leader = self.get_leader()
            return leader is not None and leader.is_self
        except Exception:
            return False

    def get_leader(self) -> LeaderInfo | None:
        """현재 리더 정보 조회."""
        try:
            redis = self._get_redis()
            value = redis.get(self._leader_key)
            if not value:
                return None

            ttl = redis.ttl(self._leader_key)
            if ttl < 0:
                return None

            data = json.loads(value)
            elected_at = datetime.fromisoformat(data["elected_at"])
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

            return LeaderInfo(
                node_id=data["node_id"],
                elected_at=elected_at,
                lease_expires_at=expires_at,
                fencing_token=data.get("fencing_token", 0),
                region_priority=data.get("region_priority", 100),
                is_self=(data["node_id"] == self._node_id),
            )
        except Exception as e:
            logger.error(f"[LeaderElector] 리더 정보 조회 실패: {e}")
            return None

    def _try_acquire(self) -> bool:
        """리더 획득 시도 (Fencing Token 및 우선순위 포함)."""
        try:
            redis = self._get_redis()

            value = json.dumps(
                {
                    "node_id": self._node_id,
                    "elected_at": datetime.now(timezone.utc).isoformat(),
                    "region_priority": self._settings.region_priority,
                }
            )

            # 우선순위 기반 획득 사용
            if self._acquire_script is None:
                self._acquire_script = redis.register_script(self.LUA_ACQUIRE_WITH_PRIORITY)

            result = self._acquire_script(
                keys=[self._leader_key, self._fencing_key],
                args=[value, self._settings.lease_ttl_seconds, self._settings.region_priority],
            )

            if result > 0:
                self._fencing_token = int(result)
                logger.info(
                    f"[LeaderElector] 리더 획득 성공 " f"(resource={self._resource_name}, fencing_token={self._fencing_token})"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"[LeaderElector] 리더 획득 실패: {e}")
            return False

    def _renew_lease(self) -> bool:
        """Lease 갱신."""
        try:
            redis = self._get_redis()

            if self._renew_script is None:
                self._renew_script = redis.register_script(self.LUA_RENEW)

            result = self._renew_script(
                keys=[self._leader_key],
                args=[self._node_id, self._settings.lease_ttl_seconds],
            )

            if result == 1:
                # 메트릭: Lease 만료 시간 갱신
                metrics = self._get_metrics()
                if metrics:
                    expire_ts = time.time() + self._settings.lease_ttl_seconds
                    metrics.set_lease_expire_timestamp(expire_ts)
                return True
            return False
        except Exception as e:
            logger.error(f"[LeaderElector] Lease 갱신 실패: {e}")
            metrics = self._get_metrics()
            if metrics:
                metrics.record_renew_error(type(e).__name__)
            return False

    def _release_leadership(self) -> None:
        """리더십 반납."""
        try:
            redis = self._get_redis()

            if self._release_script is None:
                self._release_script = redis.register_script(self.LUA_RELEASE)

            self._release_script(
                keys=[self._leader_key],
                args=[self._node_id],
            )
            logger.info(f"[LeaderElector] 리더십 반납 완료 (resource={self._resource_name})")
        except Exception as e:
            logger.error(f"[LeaderElector] 리더십 반납 실패: {e}")

    def _safe_callback(self, callback: Callable[[], None], callback_type: str) -> None:
        """안전한 콜백 실행 (예외 격리)."""
        try:
            callback()
        except Exception as e:
            logger.error(f"[LeaderElector] {callback_type} 콜백 오류: {e}")

    def _become_leader(self) -> None:
        """리더 됨 (비동기 콜백 실행)."""
        with self._lock:
            if self._state == LeadershipState.LEADER:
                return
            self._state = LeadershipState.LEADER

        logger.info(f"[LeaderElector] 리더가 되었습니다 (resource={self._resource_name})")

        # 메트릭 업데이트
        metrics = self._get_metrics()
        if metrics:
            metrics.set_leader(True)
            metrics.record_election()

        # 콜백을 별도 스레드에서 실행 (논블로킹)
        executor = self._get_callback_executor()
        for callback in self._on_become_callbacks:
            executor.submit(self._safe_callback, callback, "on_become_leader")

        # Recovery Audit 기록
        self._record_leadership_event("leader_elected")

    def _lose_leader(self, reason: str = "normal") -> None:
        """리더십 상실 (비동기 콜백 실행)."""
        with self._lock:
            if self._state != LeadershipState.LEADER:
                return
            self._state = LeadershipState.FOLLOWER

        logger.info(f"[LeaderElector] 리더십을 잃었습니다 " f"(resource={self._resource_name}, reason={reason})")

        # 메트릭 업데이트
        metrics = self._get_metrics()
        if metrics:
            metrics.set_leader(False)
            metrics.record_leadership_end()

        # 콜백을 별도 스레드에서 실행 (논블로킹)
        executor = self._get_callback_executor()
        for callback in self._on_lose_callbacks:
            executor.submit(self._safe_callback, callback, "on_lose_leader")

        # Recovery Audit 기록
        event_type = "leader_stepped_down" if reason == "self_fencing" else "leader_lost"
        self._record_leadership_event(event_type)

    def _record_leadership_event(
        self,
        event_type: str,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        """리더십 이벤트를 Recovery Audit에 기록."""
        try:
            from selfhealing.services.coordination.recovery_audit import (
                RecoveryAuditEventType,
                get_recovery_audit_recorder,
            )

            # RecoveryAuditEventType에 해당 타입이 있는지 확인
            try:
                audit_event_type = RecoveryAuditEventType(event_type)
            except ValueError:
                # 없으면 기록하지 않음
                return

            recorder = get_recovery_audit_recorder()
            recorder.record_recovery_event(
                event_type=audit_event_type,
                session_id=f"leader-{self._resource_name}-{self._node_id}",
                namespace=self._resource_name,
                executed_by=self._node_id,
                success=success,
                error_message=error_message,
                metadata={
                    "fencing_token": self._fencing_token,
                    "region_priority": self._settings.region_priority,
                },
            )
        except (ImportError, Exception) as e:
            logger.debug(f"[LeaderElector] Audit 기록 실패: {e}")

    def _run_loop(self) -> None:
        """리더 선출 메인 루프."""
        from selfhealing.utils.jitter import calculate_jitter

        consecutive_failures = 0

        while self._running:
            try:
                with self._lock:
                    current_state = self._state

                if current_state == LeadershipState.LEADER:
                    # 리더: Lease 갱신
                    if self._renew_lease():
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1

                        # Self-Fencing: 즉시 리더십 포기
                        if self._settings.self_fencing_enabled:
                            logger.warning(f"[LeaderElector] Lease 갱신 실패, 리더십 포기 " f"(self_fencing_enabled=True)")
                            self._lose_leader(reason="self_fencing")
                            consecutive_failures = 0
                        elif (
                            self._settings.max_retry_attempts > 0 and consecutive_failures >= self._settings.max_retry_attempts
                        ):
                            self._lose_leader(reason="max_retry_exceeded")
                            consecutive_failures = 0

                    self._stop_event.wait(self._settings.get_effective_renew_interval())
                    if self._stop_event.is_set():
                        break

                else:
                    # 팔로워: 리더 획득 시도
                    if self._try_acquire():
                        self._become_leader()
                        consecutive_failures = 0
                    else:
                        with self._lock:
                            if self._state not in (
                                LeadershipState.STOPPING,
                                LeadershipState.STOPPED,
                            ):
                                self._state = LeadershipState.FOLLOWER

                    # Jitter 적용 (Thundering Herd 방지)
                    jitter = calculate_jitter(
                        max_delay_seconds=self._settings.retry_interval_seconds * self._settings.retry_jitter_factor,
                        min_delay_seconds=0,
                    )
                    self._stop_event.wait(self._settings.retry_interval_seconds + jitter)
                    if self._stop_event.is_set():
                        break

            except Exception as e:
                logger.error(f"[LeaderElector] 선출 루프 오류: {e}")
                self._stop_event.wait(self._settings.retry_interval_seconds)
                if self._stop_event.is_set():
                    break

    def start(self) -> None:
        """리더 선출 프로세스 시작."""
        if not self._settings.enabled:
            logger.info(f"[LeaderElector] 비활성화됨 (resource={self._resource_name})")
            return

        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        with self._lock:
            self._state = LeadershipState.FOLLOWER

        self._worker = threading.Thread(
            target=self._run_loop,
            name=f"LeaderElector-{self._resource_name}",
            daemon=True,
        )
        self._worker.start()
        logger.info(f"[LeaderElector] 시작됨 (resource={self._resource_name})")

    def stop(self) -> None:
        """리더 선출 프로세스 중지."""
        was_leader = False
        with self._lock:
            was_leader = self._state == LeadershipState.LEADER
            self._state = LeadershipState.STOPPING

        self._running = False
        self._stop_event.set()

        # 리더십 반납 (이전 상태가 LEADER였던 경우)
        if was_leader:
            self._release_leadership()
            # _lose_leader는 _state를 확인하므로 직접 콜백 실행
            logger.info(f"[LeaderElector] 리더십을 잃었습니다 " f"(resource={self._resource_name}, reason=shutdown)")

            # 메트릭 업데이트
            metrics = self._get_metrics()
            if metrics:
                metrics.set_leader(False)
                metrics.record_leadership_end()

            # 콜백을 별도 스레드에서 실행 (논블로킹)
            executor = self._get_callback_executor()
            for callback in self._on_lose_callbacks:
                executor.submit(self._safe_callback, callback, "on_lose_leader")

            # Recovery Audit 기록
            self._record_leadership_event("leader_stepped_down")

        # 워커 스레드 종료 대기
        if self._worker:
            self._worker.join(timeout=2.0)

        # 콜백 스레드 풀 종료 (콜백이 완료될 때까지 대기)
        if self._callback_executor:
            self._callback_executor.shutdown(wait=True, cancel_futures=False)
            self._callback_executor = None

        with self._lock:
            self._state = LeadershipState.STOPPED

        logger.info(f"[LeaderElector] 중지됨 (resource={self._resource_name})")

    def on_become_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더가 되었을 때 콜백 등록 (데코레이터)."""
        self._on_become_callbacks.append(callback)
        return callback

    def on_lose_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """리더십을 잃었을 때 콜백 등록 (데코레이터)."""
        self._on_lose_callbacks.append(callback)
        return callback

"""
Chaos Middleware - HELLMODE 테스트용 DB 타임아웃 주입기

================================================================================
목적:
- X-DB-Lock-Timeout 헤더를 감지하면 DB 세션에 lock_timeout 설정
- X-DB-Statement-Timeout 헤더를 감지하면 statement_timeout 설정
- X-Chaos-Mode 헤더로 다양한 장애 시나리오 주입
================================================================================

사용법:
    # 요청 헤더에 추가
    X-DB-Lock-Timeout: 100       # 100ms 락 타임아웃
    X-DB-Statement-Timeout: 500  # 500ms 쿼리 타임아웃
    X-Chaos-Mode: deadlock       # 데드락 유발 모드
    X-Chaos-Mode: pool-starve    # 커넥션 풀 고갈
    X-Chaos-Mode: slow-query     # 의도적 느린 쿼리
    X-Transaction-Isolation: serializable  # 격리 수준 강화

보안:
    - X-Test-Mode: hellmode 또는 chaos-monkey 헤더가 있어야만 작동
    - 프로덕션에서는 절대 활성화하면 안 됨

================================================================================
"""

import logging
import random
import threading
import time
from typing import Callable, Optional

from django.conf import settings
from django.db import connection, connections
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)


class ChaosMiddleware:
    """
    HELLMODE 테스트용 Chaos 주입 미들웨어
    
    시스템을 강제로 부러뜨려서 Self-Healing 능력을 검증합니다.
    """
    
    # 허용된 테스트 모드
    ALLOWED_TEST_MODES = {"hellmode", "chaos-monkey", "stress-test"}
    
    # 최소/최대 타임아웃 (HELLMODE: 1ms까지 허용!)
    MIN_TIMEOUT_MS = 1  # 🔥 극단적 축소
    MAX_TIMEOUT_MS = 60000  # 60초
    
    # 🔥 HELLMODE: 랜덤 pg_sleep 주입 설정
    RANDOM_SLEEP_ENABLED = True
    RANDOM_SLEEP_PROBABILITY = 0.15  # 15% 확률
    RANDOM_SLEEP_MIN_MS = 100  # 100ms
    RANDOM_SLEEP_MAX_MS = 300  # 300ms
    
    # 커넥션 풀 고갈용 락
    _pool_starvation_lock = threading.Lock()
    _held_connections = []
    
    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._enabled = getattr(settings, 'CHAOS_MIDDLEWARE_ENABLED', False)
        
        # DEBUG 모드에서만 자동 활성화 (프로덕션 보호)
        if settings.DEBUG:
            self._enabled = True
            logger.warning("🔥 ChaosMiddleware ENABLED (DEBUG mode)")
    
    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 테스트 모드 체크
        test_mode = request.headers.get("X-Test-Mode", "").lower()
        if not self._enabled or test_mode not in self.ALLOWED_TEST_MODES:
            return self.get_response(request)
        
        # Chaos 설정 적용
        chaos_applied = []
        
        try:
            # 1. DB Lock Timeout 주입
            lock_timeout = self._get_timeout_value(request, "X-DB-Lock-Timeout")
            if lock_timeout:
                self._set_db_lock_timeout(lock_timeout)
                chaos_applied.append(f"lock_timeout={lock_timeout}ms")
            
            # 2. Statement Timeout 주입
            stmt_timeout = self._get_timeout_value(request, "X-DB-Statement-Timeout")
            if stmt_timeout:
                self._set_db_statement_timeout(stmt_timeout)
                chaos_applied.append(f"statement_timeout={stmt_timeout}ms")
            
            # 3. Transaction Isolation 변경
            isolation = request.headers.get("X-Transaction-Isolation", "").lower()
            if isolation in ("serializable", "repeatable read", "read committed"):
                self._set_transaction_isolation(isolation)
                chaos_applied.append(f"isolation={isolation}")
            
            # 4. Chaos Mode 처리
            chaos_mode = request.headers.get("X-Chaos-Mode", "").lower()
            if chaos_mode:
                result = self._apply_chaos_mode(request, chaos_mode)
                if result:
                    chaos_applied.append(f"chaos={chaos_mode}")
                    if isinstance(result, HttpResponse):
                        return result
            
            # 5. 🔥 HELLMODE: 랜덤 pg_sleep 주입 (락 점유 시간 강제 연장)
            if self.RANDOM_SLEEP_ENABLED and test_mode == "hellmode":
                if random.random() < self.RANDOM_SLEEP_PROBABILITY:
                    sleep_ms = random.randint(self.RANDOM_SLEEP_MIN_MS, self.RANDOM_SLEEP_MAX_MS)
                    self._inject_random_sleep(sleep_ms)
                    chaos_applied.append(f"pg_sleep={sleep_ms}ms")
            
            # 로깅
            if chaos_applied:
                logger.warning(f"🔥 CHAOS INJECTED: {', '.join(chaos_applied)}")
            
            # 요청 처리
            response = self.get_response(request)
            
            return response
            
        except Exception as e:
            logger.error(f"🔥 CHAOS ERROR: {e}")
            # Chaos로 인한 에러는 의도적이므로 503 반환
            return JsonResponse(
                {
                    "error": "chaos_induced_failure",
                    "message": str(e),
                    "chaos_applied": chaos_applied,
                },
                status=503,
            )
        
        finally:
            # DB 세션 설정 복원 (다음 요청에 영향 주지 않도록)
            self._reset_db_settings()
    
    def _get_timeout_value(self, request: HttpRequest, header_name: str) -> Optional[int]:
        """헤더에서 타임아웃 값 추출 (ms)"""
        value = request.headers.get(header_name)
        if not value:
            return None
        
        try:
            timeout = int(value)
            # 범위 제한
            return max(self.MIN_TIMEOUT_MS, min(timeout, self.MAX_TIMEOUT_MS))
        except (ValueError, TypeError):
            return None
    
    def _set_db_lock_timeout(self, timeout_ms: int):
        """DB 세션에 lock_timeout 설정"""
        with connection.cursor() as cursor:
            # PostgreSQL 문법
            cursor.execute(f"SET lock_timeout = '{timeout_ms}ms'")
            logger.debug(f"SET lock_timeout = '{timeout_ms}ms'")
    
    def _set_db_statement_timeout(self, timeout_ms: int):
        """DB 세션에 statement_timeout 설정"""
        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")
            logger.debug(f"SET statement_timeout = '{timeout_ms}ms'")
    
    def _set_transaction_isolation(self, level: str):
        """트랜잭션 격리 수준 설정"""
        level_map = {
            "serializable": "SERIALIZABLE",
            "repeatable read": "REPEATABLE READ",
            "read committed": "READ COMMITTED",
        }
        pg_level = level_map.get(level, "READ COMMITTED")
        
        with connection.cursor() as cursor:
            cursor.execute(f"SET default_transaction_isolation = '{pg_level}'")
            logger.debug(f"SET isolation = '{pg_level}'")
    
    def _inject_random_sleep(self, sleep_ms: int):
        """
        🔥 HELLMODE: pg_sleep으로 트랜잭션 락 점유 시간 강제 연장
        
        이 함수는 현재 DB 커넥션에서 pg_sleep을 실행하여
        다음 요청들이 lock_timeout에 걸리도록 유도합니다.
        """
        try:
            sleep_sec = sleep_ms / 1000.0
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT pg_sleep({sleep_sec})")
            logger.warning(f"🔥 pg_sleep({sleep_sec}s) executed - lock contention induced")
        except Exception as e:
            logger.debug(f"pg_sleep failed: {e}")
    
    def _reset_db_settings(self):
        """DB 세션 설정 복원"""
        try:
            with connection.cursor() as cursor:
                # 기본값으로 복원
                cursor.execute("SET lock_timeout = '0'")  # 0 = 무제한
                cursor.execute("SET statement_timeout = '0'")
                cursor.execute("SET default_transaction_isolation = 'READ COMMITTED'")
        except Exception:
            pass  # 복원 실패는 무시
    
    def _apply_chaos_mode(self, request: HttpRequest, mode: str) -> Optional[HttpResponse]:
        """특수 Chaos 모드 적용"""
        
        if mode == "deadlock":
            return self._chaos_deadlock(request)
        
        elif mode == "pool-starve":
            return self._chaos_pool_starvation(request)
        
        elif mode == "slow-query":
            return self._chaos_slow_query(request)
        
        elif mode == "connection-poison":
            return self._chaos_connection_poison(request)
        
        elif mode == "random-failure":
            return self._chaos_random_failure(request)
        
        return None
    
    def _chaos_deadlock(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        데드락 유발 모드
        
        A 테이블 잠금 → B 대기 / B 테이블 잠금 → A 대기
        동시 실행 시 데드락 발생
        """
        pattern = request.headers.get("X-Deadlock-Pattern", "order_product")
        
        try:
            with connection.cursor() as cursor:
                if "order" in pattern:
                    # Order 먼저, Product 나중
                    cursor.execute("""
                        SELECT id FROM shopping_order 
                        WHERE id = 1 
                        FOR UPDATE NOWAIT
                    """)
                    time.sleep(0.5)  # 다른 요청이 락 잡을 시간
                    cursor.execute("""
                        SELECT id FROM shopping_product 
                        WHERE id = 1 
                        FOR UPDATE
                    """)
                else:
                    # Product 먼저, Order 나중
                    cursor.execute("""
                        SELECT id FROM shopping_product 
                        WHERE id = 1 
                        FOR UPDATE NOWAIT
                    """)
                    time.sleep(0.5)
                    cursor.execute("""
                        SELECT id FROM shopping_order 
                        WHERE id = 1 
                        FOR UPDATE
                    """)
        except Exception as e:
            # 데드락 또는 락 타임아웃 = 성공!
            logger.warning(f"🔥 DEADLOCK CHAOS: {e}")
            return JsonResponse(
                {"error": "deadlock_detected", "message": str(e)},
                status=423,  # Locked
            )
        
        return None
    
    def _chaos_pool_starvation(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        커넥션 풀 고갈 모드
        
        커넥션을 잡고 놓지 않아서 다른 요청이 커넥션을 얻지 못하게 함
        """
        hold_seconds = int(request.headers.get("X-Pool-Hold-Seconds", "5"))
        
        with self._pool_starvation_lock:
            # 커넥션 획득 (풀에서)
            conn = connections['default']
            conn.ensure_connection()
            
            # 장시간 점유
            logger.warning(f"🔥 POOL STARVATION: Holding connection for {hold_seconds}s")
            time.sleep(hold_seconds)
        
        return None
    
    def _chaos_slow_query(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        의도적으로 느린 쿼리 실행
        """
        delay_seconds = int(request.headers.get("X-Slow-Query-Seconds", "3"))
        
        with connection.cursor() as cursor:
            # pg_sleep으로 의도적 지연
            cursor.execute(f"SELECT pg_sleep({delay_seconds})")
            logger.warning(f"🔥 SLOW QUERY: {delay_seconds}s delay")
        
        return None
    
    def _chaos_connection_poison(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        커넥션 오염 - 랜덤하게 커넥션을 끊어버림
        """
        if random.random() < 0.3:  # 30% 확률
            connection.close()
            logger.warning("🔥 CONNECTION POISONED: Forcibly closed")
            return JsonResponse(
                {"error": "connection_terminated", "chaos": True},
                status=503,
            )
        return None
    
    def _chaos_random_failure(self, request: HttpRequest) -> Optional[HttpResponse]:
        """
        랜덤 실패 - 일정 확률로 요청 실패
        """
        failure_rate = float(request.headers.get("X-Failure-Rate", "0.2"))
        
        if random.random() < failure_rate:
            logger.warning(f"🔥 RANDOM FAILURE: {failure_rate*100}% chance hit")
            return JsonResponse(
                {"error": "chaos_random_failure", "rate": failure_rate},
                status=503,
            )
        return None


class ConnectionPoolLimiterMiddleware:
    """
    커넥션 풀 제한 미들웨어
    
    테스트용으로 동시 커넥션 수를 인위적으로 제한합니다.
    """
    
    _active_connections = 0
    _max_connections = 5  # HELLMODE: 5개로 제한
    _lock = threading.Lock()
    _waiting = threading.Condition(_lock)
    
    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._enabled = getattr(settings, 'POOL_LIMITER_ENABLED', False)
    
    def __call__(self, request: HttpRequest) -> HttpResponse:
        test_mode = request.headers.get("X-Test-Mode", "").lower()
        
        if not self._enabled or test_mode not in {"hellmode", "pool-limit"}:
            return self.get_response(request)
        
        # 커스텀 제한 적용
        max_conn = int(request.headers.get("X-Max-Connections", str(self._max_connections)))
        
        with self._lock:
            # 대기
            wait_start = time.time()
            while self._active_connections >= max_conn:
                # 최대 5초 대기
                if time.time() - wait_start > 5:
                    logger.warning(f"🔥 POOL EXHAUSTED: {self._active_connections}/{max_conn}")
                    return JsonResponse(
                        {
                            "error": "connection_pool_exhausted",
                            "active": self._active_connections,
                            "max": max_conn,
                        },
                        status=503,
                    )
                self._waiting.wait(timeout=1)
            
            self._active_connections += 1
        
        try:
            return self.get_response(request)
        finally:
            with self._lock:
                self._active_connections -= 1
                self._waiting.notify_all()

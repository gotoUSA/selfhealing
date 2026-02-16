"""
Canary + Stale Cache Integration

HALF_OPEN 상태에서 Canary 비율(10%→30%→60%)의 요청만 백엔드로 보내고,
나머지 요청은 즉시 Stale Cache를 반환합니다.

결과:
- 90%의 사용자는 에러 없이 서비스 이용 (약간 오래된 데이터)
- 10%의 요청으로 백엔드 안정성 검증
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from selfhealing.services.circuit_breaker.canary_recovery import (
    CanaryRecoveryManager,
    CanaryRecoveryStage,
    get_canary_recovery_manager,
)
from selfhealing.services.circuit_breaker.config import CircuitState

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Stale Cache Configuration
# =============================================================================


@dataclass
class CanaryWithStaleCacheConfig:
    """
    Canary Recovery + Stale Cache 결합 설정.

    Attributes:
        enabled: 기능 활성화 여부
        stale_cache_max_age_seconds: Stale Cache 최대 허용 시간 (기본 5분)
        non_canary_action: Canary 비율에서 제외된 요청 처리 방법
        stale_cache_miss_action: Stale Cache 없을 때 처리 방법
        add_stale_indicator: 응답에 Stale 여부 표시
        stale_header_name: Stale 표시 헤더 이름
        default_stale_value: Stale Cache Miss 시 기본값 (옵션)
    """

    enabled: bool = True

    # Stale Cache 설정
    stale_cache_max_age_seconds: int = 300  # 5분

    # Canary 비율에서 제외된 요청 처리
    non_canary_action: str = "stale_cache"  # "stale_cache" | "reject" | "queue"

    # Stale Cache 없을 때 fallback
    stale_cache_miss_action: str = "reject"  # "reject" | "default_value" | "allow"

    # 응답에 Stale 여부 표시
    add_stale_indicator: bool = True
    stale_header_name: str = "X-Stale-Response"

    # Stale Cache Miss 시 기본값
    default_stale_value: Any | None = None


# =============================================================================
# Stale Cache Entry
# =============================================================================


@dataclass
class StaleCacheEntry(Generic[T]):
    """
    Stale Cache 엔트리.

    Attributes:
        key: 캐시 키
        value: 캐시된 값
        cached_at: 캐시 시점
        service_id: 서비스 ID
        ttl_seconds: 원래 TTL
    """

    key: str
    value: T
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    service_id: str = ""
    ttl_seconds: int = 300  # 기본 5분

    def age_seconds(self) -> float:
        """캐시 나이 (초)."""
        return (datetime.now(timezone.utc) - self.cached_at).total_seconds()

    def is_stale(self) -> bool:
        """TTL 초과 여부 (stale 상태인지)."""
        return self.age_seconds() > self.ttl_seconds

    def is_expired(self, max_stale_age: int) -> bool:
        """최대 stale 허용 시간 초과 여부."""
        return self.age_seconds() > (self.ttl_seconds + max_stale_age)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "key": self.key,
            "value": str(self.value)[:100],  # 값은 요약
            "cached_at": self.cached_at.isoformat(),
            "service_id": self.service_id,
            "ttl_seconds": self.ttl_seconds,
            "age_seconds": self.age_seconds(),
            "is_stale": self.is_stale(),
        }


# =============================================================================
# Canary with Stale Decision
# =============================================================================


@dataclass
class CanaryWithStaleDecision:
    """
    Canary + Stale Cache 통합 결정 결과.

    Attributes:
        allow_backend: 백엔드 호출 허용 여부
        use_stale: Stale Cache 사용 여부
        stale_data: 캐시된 데이터
        stale_age_seconds: Stale 데이터 나이
        is_canary_request: Canary 요청 여부
        current_stage: 현재 Canary 단계
        traffic_percent: 현재 단계 트래픽 비율
        reason: 결정 사유
        reject: 거부 여부 (Stale도 없음)
        cb_state: Circuit Breaker 상태
    """

    allow_backend: bool = False
    use_stale: bool = False
    stale_data: Any | None = None
    stale_age_seconds: float = 0.0
    is_canary_request: bool = False
    current_stage: CanaryRecoveryStage | None = None
    traffic_percent: float = 0.0
    reason: str = ""
    reject: bool = False
    cb_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "allow_backend": self.allow_backend,
            "use_stale": self.use_stale,
            "stale_data": str(self.stale_data)[:100] if self.stale_data else None,
            "stale_age_seconds": self.stale_age_seconds,
            "is_canary_request": self.is_canary_request,
            "current_stage": self.current_stage.value if self.current_stage else None,
            "traffic_percent": self.traffic_percent,
            "reason": self.reason,
            "reject": self.reject,
            "cb_state": self.cb_state,
        }


# =============================================================================
# Stale Cache Store
# =============================================================================


class StaleCacheStore:
    """
    Stale Cache 저장소.

    간단한 인메모리 캐시로 구현. 실제 운영에서는 Redis 등으로 교체 가능.
    """

    def __init__(self, max_entries: int = 10000):
        """
        초기화.

        Args:
            max_entries: 최대 캐시 엔트리 수
        """
        self._cache: dict[str, StaleCacheEntry] = {}
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "stale_hits": 0,
            "expired": 0,
            "sets": 0,
        }

    def get(
        self,
        key: str,
        max_stale_age: int = 300,
    ) -> StaleCacheEntry | None:
        """
        캐시 조회.

        Args:
            key: 캐시 키
            max_stale_age: 최대 stale 허용 시간 (초)

        Returns:
            StaleCacheEntry or None
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._stats["misses"] += 1
                return None

            # 만료 확인
            if entry.is_expired(max_stale_age):
                self._stats["expired"] += 1
                del self._cache[key]
                return None

            # Stale 여부 체크
            if entry.is_stale():
                self._stats["stale_hits"] += 1
            else:
                self._stats["hits"] += 1

            return entry

    def set(
        self,
        key: str,
        value: Any,
        service_id: str = "",
        ttl_seconds: int = 300,
    ) -> StaleCacheEntry:
        """
        캐시 저장.

        Args:
            key: 캐시 키
            value: 캐시할 값
            service_id: 서비스 ID
            ttl_seconds: TTL (초)

        Returns:
            생성된 StaleCacheEntry

        Warning:
            인메모리 캐시이므로 value의 참조(Reference)가 그대로 저장된다.
            저장 후 원본 객체를 수정하면 캐시 데이터도 오염된다.
            호출자는 다음 중 하나를 준수해야 한다:
            1. 저장할 객체를 불변(Immutable)으로 취급
            2. 저장 전 copy.copy() 또는 copy.deepcopy()로 사본 전달
        """
        with self._lock:
            # 용량 초과 시 오래된 항목 제거
            if len(self._cache) >= self._max_entries:
                self._evict_oldest()

            entry = StaleCacheEntry(
                key=key,
                value=value,
                service_id=service_id,
                ttl_seconds=ttl_seconds,
            )
            self._cache[key] = entry
            self._stats["sets"] += 1

            return entry

    def delete(self, key: str) -> bool:
        """캐시 삭제."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def _evict_oldest(self) -> None:
        """가장 오래된 항목 제거."""
        if not self._cache:
            return

        oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].cached_at)
        del self._cache[oldest_key]

    def clear(self) -> int:
        """전체 캐시 삭제."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def get_stats(self) -> dict[str, Any]:
        """캐시 통계."""
        with self._lock:
            return {
                **self._stats,
                "size": len(self._cache),
                "max_entries": self._max_entries,
            }


# =============================================================================
# Canary with Stale Cache Service
# =============================================================================


class CanaryWithStaleCacheService:
    """
    Canary Recovery + Stale Cache 통합 서비스.

    HALF_OPEN 상태에서 Canary 비율의 요청만 백엔드로 보내고,
    나머지는 Stale Cache를 반환하여 사용자 에러를 최소화합니다.

    Usage:
        service = CanaryWithStaleCacheService()

        # CB 상태 확인 + Canary 결정
        decision = service.should_allow_with_fallback(
            service_id="payment-api",
            cache_key="payment:user123",
            cb_state="half_open",
        )

        if decision.allow_backend:
            try:
                result = call_backend()
                # 성공 시 캐시 업데이트
                service.update_cache("payment:user123", result)
                service.record_success("payment-api")
            except Exception as e:
                service.record_failure("payment-api")
                raise
        elif decision.use_stale:
            # Stale Cache 반환
            return decision.stale_data
        else:
            # 거부
            raise ServiceUnavailable()
    """

    _instance: CanaryWithStaleCacheService | None = None
    _lock: threading.Lock = threading.Lock()

    @staticmethod
    def build_stale_cache_key(domain: str, identifier: str) -> str:
        """
        Stale Cache Key 생성 규칙 중앙화.

        should_allow_with_fallback(), update_cache(), FallbackPolicy cache_fn에서
        동일한 키를 사용하도록 보장한다.

        Args:
            domain: 서비스 도메인 (예: "payment", "product")
            identifier: 리소스 식별자 (예: "user123", "order456")

        Returns:
            정규화된 캐시 키 (예: "payment:user123")
        """
        return f"{domain}:{identifier}"

    def __new__(cls) -> CanaryWithStaleCacheService:
        """싱글톤 패턴."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        config: CanaryWithStaleCacheConfig | None = None,
        canary_manager: CanaryRecoveryManager | None = None,
        cache_store: StaleCacheStore | None = None,
    ):
        """
        초기화.

        Args:
            config: Stale Cache 설정
            canary_manager: Canary Recovery 매니저
            cache_store: Stale Cache 저장소
        """
        if getattr(self, "_initialized", False):
            return

        self._config = config or CanaryWithStaleCacheConfig()
        self._canary_manager = canary_manager or get_canary_recovery_manager()
        self._cache = cache_store or StaleCacheStore()
        self._stats = {
            "canary_allowed": 0,
            "stale_served": 0,
            "rejected": 0,
            "backend_success": 0,
            "backend_failure": 0,
        }
        self._stats_lock = threading.Lock()

        self._initialized = True

    # =========================================================================
    # Configuration
    # =========================================================================

    def set_config(self, config: CanaryWithStaleCacheConfig) -> None:
        """설정 업데이트."""
        self._config = config

    def get_config(self) -> CanaryWithStaleCacheConfig:
        """현재 설정 조회."""
        return self._config

    # =========================================================================
    # Main Decision Logic
    # =========================================================================

    def should_allow_with_fallback(
        self,
        service_id: str,
        cache_key: str,
        cb_state: str,
    ) -> CanaryWithStaleDecision:
        """
        Canary + Stale Cache 통합 결정.

        Args:
            service_id: 서비스 ID
            cache_key: 캐시 키
            cb_state: 현재 Circuit Breaker 상태

        Returns:
            CanaryWithStaleDecision
        """
        if not self._config.enabled:
            return CanaryWithStaleDecision(
                allow_backend=True,
                cb_state=cb_state,
                reason="canary+stale disabled",
            )

        # 1. CLOSED: 정상 허용
        if cb_state == CircuitState.CLOSED or cb_state == "closed":
            return CanaryWithStaleDecision(
                allow_backend=True,
                cb_state=cb_state,
                reason="CB is CLOSED - normal flow",
            )

        # 2. OPEN: Stale Cache 사용
        if cb_state == CircuitState.OPEN or cb_state == "open":
            return self._handle_open_state(service_id, cache_key, cb_state)

        # 3. HALF_OPEN: Canary 비율 적용
        if cb_state == CircuitState.HALF_OPEN or cb_state == "half_open":
            return self._handle_half_open_state(service_id, cache_key, cb_state)

        # 알 수 없는 상태
        return CanaryWithStaleDecision(
            allow_backend=True,
            cb_state=cb_state,
            reason=f"unknown CB state: {cb_state}",
        )

    def _handle_open_state(
        self,
        service_id: str,
        cache_key: str,
        cb_state: str,
    ) -> CanaryWithStaleDecision:
        """
        OPEN 상태 처리 - Stale Cache 반환.
        """
        stale_entry = self._get_stale_cache(cache_key)

        if stale_entry is not None:
            with self._stats_lock:
                self._stats["stale_served"] += 1

            return CanaryWithStaleDecision(
                allow_backend=False,
                use_stale=True,
                stale_data=stale_entry.value,
                stale_age_seconds=stale_entry.age_seconds(),
                cb_state=cb_state,
                reason="CB is OPEN - returning stale cache",
            )

        # Stale Cache 없음 - 설정에 따라 처리
        return self._handle_stale_cache_miss(service_id, cache_key, cb_state)

    def _handle_half_open_state(
        self,
        service_id: str,
        cache_key: str,
        cb_state: str,
    ) -> CanaryWithStaleDecision:
        """
        HALF_OPEN 상태 처리 - Canary 비율 적용.
        """
        # Canary 결정 요청
        canary_decision = self._canary_manager.should_allow_request(service_id)

        if canary_decision.allow_backend:
            # Canary 요청 - 백엔드 호출 허용
            with self._stats_lock:
                self._stats["canary_allowed"] += 1

            return CanaryWithStaleDecision(
                allow_backend=True,
                is_canary_request=canary_decision.is_canary_request,
                current_stage=canary_decision.current_stage,
                traffic_percent=canary_decision.traffic_percent,
                cb_state=cb_state,
                reason=canary_decision.reason,
            )
        else:
            # Non-Canary 요청 - Stale Cache 사용
            stale_entry = self._get_stale_cache(cache_key)

            if stale_entry is not None:
                with self._stats_lock:
                    self._stats["stale_served"] += 1

                return CanaryWithStaleDecision(
                    allow_backend=False,
                    use_stale=True,
                    stale_data=stale_entry.value,
                    stale_age_seconds=stale_entry.age_seconds(),
                    is_canary_request=False,
                    current_stage=canary_decision.current_stage,
                    traffic_percent=canary_decision.traffic_percent,
                    cb_state=cb_state,
                    reason=f"non-canary request, using stale cache (age={stale_entry.age_seconds():.1f}s)",
                )

            # Stale Cache 없음
            return self._handle_stale_cache_miss(
                service_id,
                cache_key,
                cb_state,
                current_stage=canary_decision.current_stage,
                traffic_percent=canary_decision.traffic_percent,
            )

    def _handle_stale_cache_miss(
        self,
        service_id: str,
        cache_key: str,
        cb_state: str,
        current_stage: CanaryRecoveryStage | None = None,
        traffic_percent: float = 0.0,
    ) -> CanaryWithStaleDecision:
        """
        Stale Cache Miss 처리.
        """
        action = self._config.stale_cache_miss_action

        if action == "default_value" and self._config.default_stale_value is not None:
            return CanaryWithStaleDecision(
                allow_backend=False,
                use_stale=True,
                stale_data=self._config.default_stale_value,
                stale_age_seconds=0,
                current_stage=current_stage,
                traffic_percent=traffic_percent,
                cb_state=cb_state,
                reason="stale cache miss, using default value",
            )

        if action == "allow":
            return CanaryWithStaleDecision(
                allow_backend=True,
                current_stage=current_stage,
                traffic_percent=traffic_percent,
                cb_state=cb_state,
                reason="stale cache miss, allowing backend call",
            )

        # reject (기본)
        with self._stats_lock:
            self._stats["rejected"] += 1

        return CanaryWithStaleDecision(
            allow_backend=False,
            use_stale=False,
            reject=True,
            current_stage=current_stage,
            traffic_percent=traffic_percent,
            cb_state=cb_state,
            reason="stale cache miss, rejecting request",
        )

    def _get_stale_cache(self, cache_key: str) -> StaleCacheEntry | None:
        """Stale Cache 조회."""
        return self._cache.get(
            key=cache_key,
            max_stale_age=self._config.stale_cache_max_age_seconds,
        )

    # =========================================================================
    # Cache Management
    # =========================================================================

    def update_cache(
        self,
        cache_key: str,
        value: Any,
        service_id: str = "",
        ttl_seconds: int | None = None,
    ) -> StaleCacheEntry:
        """
        캐시 업데이트 (성공한 백엔드 응답 저장).

        Args:
            cache_key: 캐시 키
            value: 캐시할 값
            service_id: 서비스 ID
            ttl_seconds: TTL (없으면 설정값 사용)

        Returns:
            생성된 StaleCacheEntry
        """
        ttl = ttl_seconds or self._config.stale_cache_max_age_seconds
        return self._cache.set(
            key=cache_key,
            value=value,
            service_id=service_id,
            ttl_seconds=ttl,
        )

    def invalidate_cache(self, cache_key: str) -> bool:
        """캐시 무효화."""
        return self._cache.delete(cache_key)

    def clear_cache(self) -> int:
        """전체 캐시 삭제."""
        return self._cache.clear()

    # =========================================================================
    # Metrics Recording (Canary 연동)
    # =========================================================================

    def record_success(
        self,
        service_id: str,
        cache_key: str | None = None,
        response_data: Any = None,
    ) -> None:
        """
        백엔드 호출 성공 기록.

        cache_key와 response_data가 모두 전달되면 update_cache()를 자동 호출하여
        Stale Cache를 갱신한다. 캐시 저장 실패는 suppress하여 원본 성공 결과에
        영향을 주지 않는다.

        Args:
            service_id: 서비스 ID
            cache_key: Stale Cache 키 (전달 시 자동 캐시 저장)
            response_data: 캐시에 저장할 응답 데이터 (cache_key와 함께 전달)
        """
        with self._stats_lock:
            self._stats["backend_success"] += 1

        # Canary 매니저에도 성공 기록
        self._canary_manager.record_success(service_id)

        # 캐시 자동 저장 — 누락 방지
        if cache_key is not None and response_data is not None:
            try:
                self.update_cache(cache_key, response_data, service_id=service_id)
            except Exception as e:
                logger.warning("Auto cache update failed (suppressed): %s", e)

    def record_failure(self, service_id: str) -> None:
        """
        백엔드 호출 실패 기록.

        Args:
            service_id: 서비스 ID
        """
        with self._stats_lock:
            self._stats["backend_failure"] += 1

        # Canary 매니저에도 실패 기록
        self._canary_manager.record_failure(service_id)

    # =========================================================================
    # Response Wrapping
    # =========================================================================

    def wrap_response(
        self,
        response: Any,
        decision: CanaryWithStaleDecision,
    ) -> Any:
        """
        응답에 Stale 표시 추가.

        HTTP 응답이면 헤더 추가, 아니면 그대로 반환.

        Args:
            response: 원본 응답
            decision: Canary+Stale 결정 결과

        Returns:
            Stale 표시가 추가된 응답
        """
        if not self._config.add_stale_indicator:
            return response

        if not decision.use_stale:
            return response

        # HTTP 응답 스타일 헤더 추가 (Django Response 등)
        if hasattr(response, "__setitem__"):
            response[self._config.stale_header_name] = "true"
            response["X-Stale-Age"] = str(int(decision.stale_age_seconds))
        elif hasattr(response, "headers"):
            response.headers[self._config.stale_header_name] = "true"
            response.headers["X-Stale-Age"] = str(int(decision.stale_age_seconds))

        return response

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """통합 통계."""
        with self._stats_lock:
            return {
                **self._stats,
                "cache_stats": self._cache.get_stats(),
                "canary_states": self._canary_manager.get_all_recovery_states(),
            }

    def reset_stats(self) -> None:
        """통계 초기화."""
        with self._stats_lock:
            for key in self._stats:
                self._stats[key] = 0


# =============================================================================
# Module-level Singleton Functions
# =============================================================================


_service_instance: CanaryWithStaleCacheService | None = None
_service_lock = threading.Lock()


def get_canary_stale_cache_service() -> CanaryWithStaleCacheService:
    """싱글톤 인스턴스 반환."""
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = CanaryWithStaleCacheService()
    return _service_instance


def reset_canary_stale_cache_service() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _service_instance
    with _service_lock:
        _service_instance = None
        CanaryWithStaleCacheService._instance = None


# =============================================================================
# Convenience Functions
# =============================================================================


def should_allow_with_fallback(
    service_id: str,
    cache_key: str,
    cb_state: str,
) -> CanaryWithStaleDecision:
    """Canary + Stale Cache 통합 결정."""
    return get_canary_stale_cache_service().should_allow_with_fallback(
        service_id=service_id,
        cache_key=cache_key,
        cb_state=cb_state,
    )


def update_stale_cache(
    cache_key: str,
    value: Any,
    service_id: str = "",
    ttl_seconds: int | None = None,
) -> StaleCacheEntry:
    """Stale Cache 업데이트."""
    return get_canary_stale_cache_service().update_cache(
        cache_key=cache_key,
        value=value,
        service_id=service_id,
        ttl_seconds=ttl_seconds,
    )


def record_canary_success(service_id: str) -> None:
    """Canary 성공 기록."""
    get_canary_stale_cache_service().record_success(service_id)


def record_canary_failure(service_id: str) -> None:
    """Canary 실패 기록."""
    get_canary_stale_cache_service().record_failure(service_id)


def build_stale_cache_key(domain: str, identifier: str) -> str:
    """Stale Cache Key 생성."""
    return CanaryWithStaleCacheService.build_stale_cache_key(domain, identifier)

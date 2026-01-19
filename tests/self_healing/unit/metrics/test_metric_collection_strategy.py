"""
Tests for Metric Collection Strategy Implementation.

Tests for:
- MetricSourceAdapter (base, django, redis)
- Event handlers and decorators
- Jitter and Reconciler
- DriftThresholdConfig
"""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

import pytest

# =============================================================================
# MetricSourceAdapter Tests
# =============================================================================


class TestNullMetricSourceAdapter:
    """NullMetricSourceAdapter 테스트."""

    def test_get_dlq_pending_count_returns_zero(self):
        """대기 중인 DLQ 수가 0을 반환하는지 확인."""
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        assert adapter.get_dlq_pending_count("payment") == 0
        assert adapter.get_dlq_pending_count("point") == 0

    def test_get_dlq_count_by_status_returns_zero(self):
        """상태별 DLQ 수가 0을 반환하는지 확인."""
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        assert adapter.get_dlq_count_by_status("pending") == 0
        assert adapter.get_dlq_count_by_status("resolved") == 0

    def test_get_circuit_breaker_state_returns_closed(self):
        """CB 상태가 closed를 반환하는지 확인."""
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        assert adapter.get_circuit_breaker_state("toss_payment") == "closed"

    def test_get_retry_success_rate_returns_zero(self):
        """재시도 성공률이 0을 반환하는지 확인."""
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        assert adapter.get_retry_success_rate("payment") == 0.0


class TestDjangoMetricSourceAdapter:
    """DjangoMetricSourceAdapter 테스트."""

    def test_init_without_models(self):
        """모델 없이 초기화 가능."""
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter

        adapter = DjangoMetricSourceAdapter()
        assert adapter.dlq_model is None
        assert adapter.cb_model is None

    def test_get_dlq_pending_count_without_model(self):
        """모델 없이 호출 시 0 반환."""
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter

        adapter = DjangoMetricSourceAdapter()
        assert adapter.get_dlq_pending_count("payment") == 0

    def test_get_dlq_pending_count_with_mock_model(self):
        """Mock 모델로 DLQ 수 조회."""
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter

        mock_model = Mock()
        mock_model.objects.filter.return_value.count.return_value = 5

        adapter = DjangoMetricSourceAdapter(dlq_model=mock_model)
        result = adapter.get_dlq_pending_count("payment")

        assert result == 5
        mock_model.objects.filter.assert_called_once_with(
            domain="payment",
            status="pending",
        )


class TestRedisMetricSourceAdapter:
    """RedisMetricSourceAdapter 테스트."""

    def test_get_dlq_pending_count(self):
        """Redis에서 DLQ 수 조회."""
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

        mock_redis = Mock()
        mock_redis.get.return_value = "10"

        adapter = RedisMetricSourceAdapter(redis_client=mock_redis)
        result = adapter.get_dlq_pending_count("payment")

        assert result == 10
        mock_redis.get.assert_called_once_with("sh:metrics:dlq:pending:payment")

    def test_get_dlq_pending_count_none_value(self):
        """Redis에 값이 없을 때 0 반환."""
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

        mock_redis = Mock()
        mock_redis.get.return_value = None

        adapter = RedisMetricSourceAdapter(redis_client=mock_redis)
        assert adapter.get_dlq_pending_count("payment") == 0

    def test_increment_dlq_pending(self):
        """DLQ 대기 수 증가."""
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

        mock_redis = Mock()
        mock_redis.incr.return_value = 5

        adapter = RedisMetricSourceAdapter(redis_client=mock_redis)
        result = adapter.increment_dlq_pending("payment")

        assert result == 5
        mock_redis.incr.assert_called_once()

    def test_decrement_dlq_pending(self):
        """DLQ 대기 수 감소."""
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter

        mock_redis = Mock()
        mock_redis.decr.return_value = 3

        adapter = RedisMetricSourceAdapter(redis_client=mock_redis)
        result = adapter.decrement_dlq_pending("payment")

        assert result == 3
        mock_redis.decr.assert_called_once()


class TestMetricAdapterFactory:
    """메트릭 어댑터 팩토리 테스트."""

    def test_get_metric_adapter_default_null(self):
        """기본 어댑터는 NullMetricSourceAdapter."""
        from selfhealing.adapters.metrics.factory import get_metric_adapter, reset_adapter
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        reset_adapter()

        with patch.dict("os.environ", {"SELFHEALING_METRICS_ADAPTER_TYPE": "null"}):
            adapter = get_metric_adapter()
            assert isinstance(adapter, NullMetricSourceAdapter)

        reset_adapter()

    def test_configure_adapter(self):
        """어댑터 직접 설정."""
        from selfhealing.adapters.metrics.factory import (
            configure_adapter,
            get_metric_adapter,
            reset_adapter,
        )

        reset_adapter()

        mock_adapter = Mock()
        configure_adapter(mock_adapter)

        assert get_metric_adapter() == mock_adapter

        reset_adapter()


# =============================================================================
# Event Handler Tests
# =============================================================================


class TestDLQMetricEventHandler:
    """DLQMetricEventHandler 테스트."""

    def test_on_item_created(self):
        """DLQ 생성 이벤트 처리."""
        from selfhealing.metrics.event_handlers import DLQMetricEventHandler

        # 메트릭이 없어도 오류 없이 실행
        DLQMetricEventHandler.on_item_created("payment", "PG_TIMEOUT")

    def test_on_item_resolved(self):
        """DLQ 해결 이벤트 처리."""
        from selfhealing.metrics.event_handlers import DLQMetricEventHandler

        DLQMetricEventHandler.on_item_resolved(
            domain="payment",
            resolution_type="auto_replay",
            duration_seconds=30.5,
        )

    def test_on_item_failed(self):
        """DLQ 실패 이벤트 처리."""
        from selfhealing.metrics.event_handlers import DLQMetricEventHandler

        DLQMetricEventHandler.on_item_failed(
            domain="payment",
            failure_type="PG_TIMEOUT",
            attempt_count=3,
        )


class TestCircuitBreakerEventHandler:
    """CircuitBreakerEventHandler 테스트."""

    def test_on_state_changed(self):
        """CB 상태 변경 이벤트 처리."""
        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler

        CircuitBreakerEventHandler.on_state_changed(
            service="toss_payment",
            from_state="closed",
            to_state="open",
        )

    def test_on_failure(self):
        """CB 실패 이벤트 처리."""
        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler

        CircuitBreakerEventHandler.on_failure("toss_payment")


# =============================================================================
# Decorator Tests
# =============================================================================


class TestMetricDecorators:
    """메트릭 데코레이터 테스트."""

    def test_track_dlq_creation(self):
        """track_dlq_creation 데코레이터."""
        from selfhealing.metrics.decorators import track_dlq_creation

        @track_dlq_creation(domain="payment")
        def create_dlq(failure_type: str, payload: dict):
            return {"id": 123, "failure_type": failure_type}

        result = create_dlq(failure_type="PG_TIMEOUT", payload={"order_id": "1"})
        assert result["id"] == 123

    def test_track_dlq_resolution(self):
        """track_dlq_resolution 데코레이터."""
        from selfhealing.metrics.decorators import track_dlq_resolution

        @track_dlq_resolution(domain="payment")
        def resolve_dlq(item, resolution_type: str = "auto"):
            return {"resolved": True}

        result = resolve_dlq({"id": 123}, resolution_type="manual")
        assert result["resolved"] is True

    def test_track_replay(self):
        """track_replay 데코레이터."""
        from selfhealing.metrics.decorators import track_replay

        @track_replay(domain="payment")
        def replay_item(item):
            return True

        result = replay_item({"id": 123})
        assert result is True


# =============================================================================
# Jitter Tests
# =============================================================================


class TestJitter:
    """Jitter 유틸리티 테스트."""

    def test_calculate_jitter(self):
        """Jitter 계산."""
        from selfhealing.metrics.jitter import calculate_jitter

        for _ in range(10):
            jitter = calculate_jitter(max_delay_seconds=10.0)
            assert 0 <= jitter <= 10.0

    def test_calculate_jitter_with_min(self):
        """최소값 포함 Jitter 계산."""
        from selfhealing.metrics.jitter import calculate_jitter

        for _ in range(10):
            jitter = calculate_jitter(max_delay_seconds=10.0, min_delay_seconds=5.0)
            assert 5.0 <= jitter <= 10.0

    def test_jitter_config_from_env(self):
        """환경 변수에서 JitterConfig 로드."""
        from selfhealing.metrics.jitter import JitterConfig

        with patch.dict(
            "os.environ",
            {
                "SELFHEALING_METRICS_JITTER_ENABLED": "true",
                "SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS": "30.0",
            },
        ):
            config = JitterConfig.from_env()
            assert config.enabled is True
            assert config.max_delay_seconds == 30.0

    def test_jitter_config_disabled(self):
        """Jitter 비활성화 시 0 반환."""
        from selfhealing.metrics.jitter import JitterConfig

        config = JitterConfig(enabled=False)
        assert config.get_delay() == 0.0

    def test_with_jitter_decorator(self):
        """with_jitter 데코레이터."""
        from selfhealing.metrics.jitter import with_jitter

        call_count = 0

        @with_jitter(max_delay_seconds=0.01)  # 매우 짧은 지연
        def test_func():
            nonlocal call_count
            call_count += 1
            return "done"

        result = test_func()
        assert result == "done"
        assert call_count == 1


# =============================================================================
# Reconciler Tests
# =============================================================================


class TestMetricReconciler:
    """MetricReconciler 테스트."""

    def test_sync_all_gauges(self):
        """모든 Gauge 동기화."""
        from selfhealing.metrics.reconciler import MetricReconciler
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(adapter=adapter)

        result = reconciler.sync_all_gauges()

        assert result.synced_at is not None
        assert isinstance(result.dlq_pending, dict)
        assert isinstance(result.circuit_breaker_states, dict)

    def test_sync_domain_gauges(self):
        """특정 도메인 Gauge 동기화."""
        from selfhealing.metrics.reconciler import MetricReconciler
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(adapter=adapter)

        result = reconciler.sync_domain_gauges("payment")

        assert result["domain"] == "payment"
        assert result["dlq_pending"] == 0
        assert result["retry_rate"] == 0.0

    def test_last_sync_time(self):
        """마지막 동기화 시간 확인."""
        from selfhealing.metrics.reconciler import MetricReconciler
        from selfhealing.adapters.metrics.base import NullMetricSourceAdapter

        adapter = NullMetricSourceAdapter()
        reconciler = MetricReconciler(adapter=adapter)

        assert reconciler.last_sync_time is None

        reconciler.sync_all_gauges()

        assert reconciler.last_sync_time is not None
        assert isinstance(reconciler.last_sync_time, datetime)


class TestDriftSeverity:
    """Drift 심각도 테스트."""

    def test_drift_severity_constants(self):
        """Drift 심각도 상수."""
        from selfhealing.metrics.reconciler import DriftSeverity

        assert DriftSeverity.NORMAL == "normal"
        assert DriftSeverity.WARNING == "warning"
        assert DriftSeverity.CRITICAL == "critical"
        assert DriftSeverity.INCIDENT == "incident"


# =============================================================================
# DriftThresholdConfig Tests
# =============================================================================


class TestDriftThresholdConfig:
    """DriftThresholdConfig 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        config = DriftThresholdConfig()

        assert config.warning_threshold == 0.05
        assert config.critical_threshold == 0.20
        assert config.incident_threshold == 0.50
        assert config.alert_enabled is True
        assert config.incident_auto_create is True

    def test_validation_success(self):
        """유효한 임계값."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        config = DriftThresholdConfig(
            warning_threshold=0.10,
            critical_threshold=0.30,
            incident_threshold=0.60,
        )

        assert config.warning_threshold == 0.10

    def test_validation_failure(self):
        """유효하지 않은 임계값."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        with pytest.raises(ValueError):
            # warning > critical은 유효하지 않음
            DriftThresholdConfig(
                warning_threshold=0.30,
                critical_threshold=0.20,
            )

    def test_to_dict(self):
        """딕셔너리 변환."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        config = DriftThresholdConfig()
        data = config.to_dict()

        assert "warning_threshold" in data
        assert "critical_threshold" in data
        assert "incident_threshold" in data

    def test_from_dict(self):
        """딕셔너리에서 생성."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        data = {
            "warning_threshold": 0.10,
            "critical_threshold": 0.25,
            "incident_threshold": 0.55,
        }

        config = DriftThresholdConfig.from_dict(data)

        assert config.warning_threshold == 0.10
        assert config.critical_threshold == 0.25
        assert config.incident_threshold == 0.55

    def test_from_env(self):
        """환경 변수에서 생성."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        with patch.dict(
            "os.environ",
            {
                "SELFHEALING_DRIFT_WARNING_THRESHOLD": "0.08",
                "SELFHEALING_DRIFT_CRITICAL_THRESHOLD": "0.25",
            },
        ):
            config = DriftThresholdConfig.from_env()
            assert config.warning_threshold == 0.08
            assert config.critical_threshold == 0.25

    def test_update(self):
        """설정 업데이트."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        original = DriftThresholdConfig()
        updated = original.update(
            actor_id="admin",
            warning_threshold=0.08,
        )

        assert original.warning_threshold == 0.05  # 원본 불변
        assert updated.warning_threshold == 0.08
        assert updated.updated_by == "admin"
        assert updated.updated_at is not None

    def test_get_threshold_percent_display(self):
        """퍼센트 표시."""
        from selfhealing.models.drift_config import DriftThresholdConfig

        config = DriftThresholdConfig()
        display = config.get_threshold_percent_display()

        assert display["warning"] == "5.0%"
        assert display["critical"] == "20.0%"
        assert display["incident"] == "50.0%"


# =============================================================================
# Time Utilities Tests
# =============================================================================


class TestTimeUtilities:
    """시간 유틸리티 테스트."""

    def test_utc_now(self):
        """UTC 현재 시간."""
        from selfhealing.utils.time import utc_now

        now = utc_now()

        assert now.tzinfo is not None
        assert now.tzinfo == timezone.utc

    def test_ensure_aware(self):
        """naive datetime을 aware로 변환."""
        from selfhealing.utils.time import ensure_aware

        naive = datetime(2024, 1, 15, 10, 30, 0)
        aware = ensure_aware(naive)

        assert aware.tzinfo == timezone.utc

    def test_ensure_aware_already_aware(self):
        """이미 aware인 경우 그대로."""
        from selfhealing.utils.time import ensure_aware

        original = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = ensure_aware(original)

        assert result == original

    def test_to_iso_string(self):
        """ISO 문자열 변환."""
        from selfhealing.utils.time import to_iso_string

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        iso = to_iso_string(dt)

        assert "2024-01-15" in iso
        assert "+00:00" in iso or "Z" in iso

    def test_to_iso_string_none(self):
        """None 입력."""
        from selfhealing.utils.time import to_iso_string

        assert to_iso_string(None) is None

    def test_elapsed_seconds(self):
        """경과 시간 계산."""
        from selfhealing.utils.time import elapsed_seconds, utc_now

        start = utc_now() - timedelta(seconds=30)
        elapsed = elapsed_seconds(start)

        assert 29 <= elapsed <= 31

    def test_is_expired(self):
        """만료 여부 확인."""
        from selfhealing.utils.time import is_expired, utc_now

        old_time = utc_now() - timedelta(hours=2)
        assert is_expired(old_time, ttl_seconds=3600) is True

        recent_time = utc_now() - timedelta(minutes=5)
        assert is_expired(recent_time, ttl_seconds=3600) is False

    def test_format_duration(self):
        """시간 포맷팅."""
        from selfhealing.utils.time import format_duration

        assert format_duration(45) == "45s"
        assert format_duration(125) == "2m 5s"
        assert format_duration(3665) == "1h 1m 5s"
        assert format_duration(90000) == "1d 1h"


# =============================================================================
# Reliability Tests
# =============================================================================


class TestMetricReliability:
    """메트릭 신뢰도 테스트."""

    def test_get_metric_reliability(self):
        """메트릭 신뢰도 조회."""
        from selfhealing.metrics.reliability import (
            get_metric_reliability,
            MetricReliability,
        )

        assert get_metric_reliability("dlq_items_total") == MetricReliability.EXACT
        assert get_metric_reliability("dlq_pending_count") == MetricReliability.EVENTUAL
        assert get_metric_reliability("unknown_metric") == MetricReliability.APPROXIMATE

    def test_metric_reliability_enum(self):
        """MetricReliability 열거형."""
        from selfhealing.metrics.reliability import MetricReliability

        assert MetricReliability.EXACT.value == "exact"
        assert MetricReliability.EVENTUAL.value == "eventual"
        assert MetricReliability.APPROXIMATE.value == "approx"


# =============================================================================
# Config Tests
# =============================================================================


class TestMetricCollectionSettings:
    """MetricCollectionSettings 테스트."""

    def test_get_metric_collection_settings(self):
        """설정 로드."""
        from selfhealing.config import get_metric_collection_settings

        # 캐시 클리어
        get_metric_collection_settings.cache_clear()

        settings = get_metric_collection_settings()

        assert settings.sync_on_startup is True
        assert settings.jitter_enabled is True
        assert settings.drift_detection_enabled is True

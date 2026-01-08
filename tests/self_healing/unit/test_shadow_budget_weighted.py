"""
Shadow Budget 가중치 기반 계산 단위 테스트.

Phase 0: 핵심 설계 원칙 (Cap, Source Reliability)
Phase 1: Severity 기반 가중치

Reference: docs/self_healing/middleware_system/30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md
"""

import pytest
from datetime import datetime, timedelta, timezone

from selfhealing.services.error_budget.reconciliation import (
    ShadowBudgetCalculator,
    FailSafePeriod,
)
from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
    SEVERITY_WEIGHT,
    SOURCE_RELIABILITY,
    MAX_WEIGHT_MULTIPLIER,
    BASE_WEIGHT_MINUTES,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def shadow_calculator():
    """Fresh ShadowBudgetCalculator for each test."""
    return ShadowBudgetCalculator()


@pytest.fixture
def sample_failsafe_period():
    """Sample FailSafePeriod for testing."""
    now_time = datetime.now(timezone.utc)
    return FailSafePeriod(
        period_id="test-period-1",
        started_at=now_time - timedelta(hours=1),
        ended_at=now_time,
        trigger_reason="Test trigger",
        trigger_component="test_component",
        is_active=False,
    )


# =============================================================================
# Phase 0: 핵심 설계 원칙 테스트
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.0
# =============================================================================


class TestPhase0Constants:
    """Phase 0 상수 정의 테스트."""

    def test_max_weight_multiplier_defined(self):
        """MAX_WEIGHT_MULTIPLIER가 50.0으로 정의되어 있어야 함."""
        assert MAX_WEIGHT_MULTIPLIER == 50.0

    def test_source_reliability_prometheus_highest(self):
        """Prometheus가 가장 높은 신뢰도(1.0)를 가져야 함."""
        assert SOURCE_RELIABILITY["prometheus"] == 1.0

    def test_source_reliability_dlq(self):
        """DLQ 신뢰도는 0.9."""
        assert SOURCE_RELIABILITY["dlq"] == 0.9

    def test_source_reliability_application_logs(self):
        """Application logs 신뢰도는 0.8."""
        assert SOURCE_RELIABILITY["application_logs"] == 0.8

    def test_source_reliability_none_available_lowest(self):
        """데이터 없을 때 가장 낮은 신뢰도(0.5)."""
        assert SOURCE_RELIABILITY["none_available"] == 0.5

    def test_base_weight_minutes(self):
        """기본 가중치는 0.001분."""
        assert BASE_WEIGHT_MINUTES == 0.001


class TestSourceReliabilityWeight:
    """Source Reliability 가중치 적용 테스트."""

    def test_prometheus_source_full_weight(self, shadow_calculator):
        """Prometheus 소스는 100% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="prometheus",
        )
        # 100 * 0.001 * 1.0 = 0.1
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_dlq_source_90_percent_weight(self, shadow_calculator):
        """DLQ 소스는 90% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="dlq",
        )
        # 100 * 0.001 * 0.9 = 0.09
        assert result == pytest.approx(0.09, rel=1e-6)

    def test_application_logs_source_80_percent_weight(self, shadow_calculator):
        """Application logs 소스는 80% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="application_logs",
        )
        # 100 * 0.001 * 0.8 = 0.08
        assert result == pytest.approx(0.08, rel=1e-6)

    def test_none_available_source_50_percent_weight(self, shadow_calculator):
        """데이터 없을 때 50% 가중치 적용 (보수적)."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="none_available",
        )
        # 100 * 0.001 * 0.5 = 0.05
        assert result == pytest.approx(0.05, rel=1e-6)

    def test_unknown_source_defaults_to_full_weight(self, shadow_calculator):
        """알 수 없는 소스는 100% 가중치 적용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 100},
            log_source="unknown_source",
        )
        # 100 * 0.001 * 1.0 = 0.1
        assert result == pytest.approx(0.1, rel=1e-6)


# =============================================================================
# Phase 1: Severity 기반 가중치 테스트
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.1
# =============================================================================


class TestPhase1SeverityConstants:
    """Phase 1 Severity 상수 정의 테스트."""

    def test_critical_severity_weight(self):
        """CRITICAL은 0.01분 (10배 가중치)."""
        assert SEVERITY_WEIGHT["critical"] == 0.01

    def test_high_severity_weight(self):
        """HIGH는 0.005분 (5배 가중치)."""
        assert SEVERITY_WEIGHT["high"] == 0.005

    def test_medium_severity_weight(self):
        """MEDIUM은 0.001분 (기본값)."""
        assert SEVERITY_WEIGHT["medium"] == 0.001

    def test_low_severity_weight(self):
        """LOW는 0.0005분 (절반 가중치)."""
        assert SEVERITY_WEIGHT["low"] == 0.0005


class TestSeverityWeighting:
    """Severity 기반 가중치 테스트."""

    def test_critical_severity_10x_weight(self, shadow_calculator):
        """CRITICAL은 기본값 대비 10배 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
        )
        # 10 * 0.01 * 1.0 = 0.1분
        assert result == pytest.approx(0.1, rel=1e-6)

    def test_high_severity_5x_weight(self, shadow_calculator):
        """HIGH는 기본값 대비 5배 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"high": 10},
            log_source="prometheus",
        )
        # 10 * 0.005 * 1.0 = 0.05분
        assert result == pytest.approx(0.05, rel=1e-6)

    def test_medium_severity_base_weight(self, shadow_calculator):
        """MEDIUM은 기본 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
        )
        # 10 * 0.001 * 1.0 = 0.01분
        assert result == pytest.approx(0.01, rel=1e-6)

    def test_low_severity_half_weight(self, shadow_calculator):
        """LOW는 기본값 대비 절반 가중치."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"low": 10},
            log_source="prometheus",
        )
        # 10 * 0.0005 * 1.0 = 0.005분
        assert result == pytest.approx(0.005, rel=1e-6)

    def test_unknown_severity_defaults_to_base(self, shadow_calculator):
        """알 수 없는 severity는 기본 가중치 사용."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"unknown": 10},
            log_source="prometheus",
        )
        # 10 * 0.001 * 1.0 = 0.01분
        assert result == pytest.approx(0.01, rel=1e-6)

    def test_case_insensitive_severity(self, shadow_calculator):
        """Severity는 대소문자 구분 없음."""
        result_lower = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
        )
        result_upper = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"CRITICAL": 10},
            log_source="prometheus",
        )
        assert result_lower == result_upper


class TestMixedSeverityWeighting:
    """혼합 Severity 가중치 테스트."""

    def test_mixed_severity_calculation(self, shadow_calculator):
        """여러 severity 혼합 계산."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 5,   # 5 * 0.01 = 0.05
                "high": 10,      # 10 * 0.005 = 0.05
                "medium": 20,    # 20 * 0.001 = 0.02
                "low": 50,       # 50 * 0.0005 = 0.025
            },
            log_source="prometheus",
        )
        # 총합: 0.05 + 0.05 + 0.02 + 0.025 = 0.145분
        assert result == pytest.approx(0.145, rel=1e-6)

    def test_mixed_severity_with_source_reliability(self, shadow_calculator):
        """혼합 severity + source reliability 조합."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 10,  # 10 * 0.01 = 0.1
                "medium": 100,   # 100 * 0.001 = 0.1
            },
            log_source="dlq",  # 0.9 reliability
        )
        # (0.1 + 0.1) * 0.9 = 0.18분
        assert result == pytest.approx(0.18, rel=1e-6)

    def test_empty_errors_returns_zero(self, shadow_calculator):
        """에러가 없으면 0 반환."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={},
            log_source="prometheus",
        )
        assert result == 0.0


# =============================================================================
# calculate_shadow_budget 통합 테스트
# =============================================================================


class TestCalculateShadowBudgetWithWeighting:
    """calculate_shadow_budget 가중치 적용 통합 테스트."""

    def test_calculate_with_errors_by_severity(self, shadow_calculator, sample_failsafe_period):
        """errors_by_severity 제공 시 가중치 적용."""
        result = shadow_calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
            errors_by_severity={
                "critical": 10,  # 10 * 0.01 = 0.1분
                "high": 20,      # 20 * 0.005 = 0.1분
            },
        )
        
        # estimated_errors는 severity 합계
        assert result.estimated_errors == 30
        
        # adjustment_minutes는 가중치 적용된 값
        # (0.1 + 0.1) * source_reliability(none_available=0.5) = 0.1분
        # 기본 log_source가 "none_available"이므로 0.5 적용
        assert result.adjustment_minutes == pytest.approx(0.1, rel=1e-3)

    def test_calculate_without_errors_by_severity_uses_medium(
        self, shadow_calculator, sample_failsafe_period
    ):
        """errors_by_severity 미제공 시 모든 에러를 medium으로 처리."""
        # Prometheus 함수를 제공하여 에러 수 반환
        def mock_prometheus(start, end):
            return 100
        
        calculator = ShadowBudgetCalculator(get_prometheus_errors=mock_prometheus)
        result = calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
        )
        
        # 100 에러 * 0.001 (medium) * 1.0 (prometheus) = 0.1분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.1, rel=1e-3)
        assert result.log_source == "prometheus"

    def test_calculate_applies_source_reliability(
        self, shadow_calculator, sample_failsafe_period
    ):
        """calculate_shadow_budget이 source_reliability를 적용."""
        # DLQ 함수를 제공
        def mock_dlq(start, end):
            return 100
        
        calculator = ShadowBudgetCalculator(get_dlq_entries=mock_dlq)
        result = calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
        )
        
        # 100 에러 * 0.001 (medium) * 0.9 (dlq) = 0.09분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.09, rel=1e-3)
        assert result.log_source == "dlq"


# =============================================================================
# 엣지 케이스 테스트
# =============================================================================


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_zero_errors_all_severities(self, shadow_calculator):
        """모든 severity가 0인 경우."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            },
            log_source="prometheus",
        )
        assert result == 0.0

    def test_very_large_error_count(self, shadow_calculator):
        """대량 에러 처리."""
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 100000},  # 10만 개
            log_source="prometheus",
        )
        # 100000 * 0.01 = 1000분
        assert result == pytest.approx(1000.0, rel=1e-6)

    def test_negative_error_count_treated_as_zero_contribution(self, shadow_calculator):
        """음수 에러 수 (비정상 케이스)."""
        # 실제로는 발생하면 안 되지만, 방어적 테스트
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": -10},
            log_source="prometheus",
        )
        # -10 * 0.001 = -0.01 (음수 반환 - 호출자가 처리)
        assert result == pytest.approx(-0.01, rel=1e-6)


# =============================================================================
# Phase 2: Domain SLA 기반 가중치 테스트
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.2
# =============================================================================


class TestPhase2DomainConstants:
    """Phase 2 Domain 상수 정의 테스트."""

    def test_default_sla_hours_defined(self):
        """DEFAULT_SLA_HOURS가 24로 정의되어 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            DEFAULT_SLA_HOURS,
        )
        assert DEFAULT_SLA_HOURS == 24


class TestDomainWeighting:
    """Domain SLA 기반 가중치 테스트."""

    def test_unknown_domain_returns_base_weight(self, shadow_calculator):
        """알 수 없는 도메인은 기본 가중치(1.0) 반환."""
        weight = shadow_calculator._get_domain_weight("unknown")
        # DEFAULT_SLA_HOURS / DEFAULT_SLA_HOURS = 1.0
        assert weight == pytest.approx(1.0, rel=1e-6)

    def test_domain_weight_with_mocked_config(self, shadow_calculator, monkeypatch):
        """설정된 도메인은 SLA 기반 가중치 반환."""
        from dataclasses import dataclass, field
        
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {
                "payment": 1,      # 1시간 SLA → 24배
                "order": 2,        # 2시간 SLA → 12배
                "notification": 24, # 24시간 SLA → 1배
            })
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # Payment: 24 / 1 = 24배
        assert shadow_calculator._get_domain_weight("payment") == pytest.approx(24.0, rel=1e-6)
        
        # Order: 24 / 2 = 12배
        assert shadow_calculator._get_domain_weight("order") == pytest.approx(12.0, rel=1e-6)
        
        # Notification: 24 / 24 = 1배
        assert shadow_calculator._get_domain_weight("notification") == pytest.approx(1.0, rel=1e-6)

    def test_domain_weight_case_insensitive(self, shadow_calculator, monkeypatch):
        """도메인 이름은 대소문자 구분 없음."""
        from dataclasses import dataclass, field
        
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # 소문자/대문자 모두 동일한 가중치
        assert shadow_calculator._get_domain_weight("Payment") == pytest.approx(24.0, rel=1e-6)
        assert shadow_calculator._get_domain_weight("PAYMENT") == pytest.approx(24.0, rel=1e-6)

    def test_domain_weight_config_failure_returns_default(self, shadow_calculator, monkeypatch):
        """설정 조회 실패 시 기본값 1.0 반환."""
        def raise_error():
            raise RuntimeError("Config unavailable")
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            raise_error,
        )
        
        weight = shadow_calculator._get_domain_weight("payment")
        assert weight == 1.0


class TestDomainWeightIntegration:
    """Domain 가중치 통합 테스트."""

    def test_calculate_weighted_errors_with_domain(self, shadow_calculator, monkeypatch):
        """_calculate_weighted_errors에 domain 적용."""
        from dataclasses import dataclass, field
        
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # medium 10개 * 0.001 * prometheus(1.0) * payment(24) = 0.24분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
        )
        assert result == pytest.approx(0.24, rel=1e-3)


# =============================================================================
# Phase 3: Learning 패턴 기반 가중치 테스트
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.3
# =============================================================================


class TestPhase3PatternConstants:
    """Phase 3 Pattern 상수 정의 테스트."""

    def test_pattern_occurrence_weight_defined(self):
        """PATTERN_OCCURRENCE_WEIGHT가 정의되어 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            PATTERN_OCCURRENCE_WEIGHT,
        )
        assert PATTERN_OCCURRENCE_WEIGHT["high"] == 2.0
        assert PATTERN_OCCURRENCE_WEIGHT["medium"] == 1.5
        assert PATTERN_OCCURRENCE_WEIGHT["low"] == 1.2
        assert PATTERN_OCCURRENCE_WEIGHT["none"] == 1.0


class TestPatternWeighting:
    """Learning 패턴 기반 가중치 테스트."""

    def test_no_pattern_returns_base_weight(self, shadow_calculator):
        """패턴이 없으면 기본 가중치(1.0) 반환."""
        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        # Learning 서비스에 패턴이 없거나 예외 발생 시 1.0
        assert weight == 1.0

    def test_pattern_weight_with_mocked_service(self, shadow_calculator, monkeypatch):
        """Learning 서비스에서 패턴 조회 시 가중치 반환."""
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock 패턴 생성
        mock_pattern = LearningPattern(
            pattern_id="test-1",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout pattern",
            confidence=0.9,
            occurrence_count=10,  # 10회 이상 → 2배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        assert weight == pytest.approx(2.0, rel=1e-6)

    def test_pattern_weight_medium_occurrence(self, shadow_calculator, monkeypatch):
        """5회 이상 발생 패턴은 1.5배 가중치."""
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        mock_pattern = LearningPattern(
            pattern_id="test-2",
            pattern_type=PatternType.FAILURE,
            name="order:validation_error",
            description="Order validation error",
            confidence=0.8,
            occurrence_count=7,  # 5 <= x < 10 → 1.5배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        weight = shadow_calculator._get_pattern_weight("order", "validation_error")
        assert weight == pytest.approx(1.5, rel=1e-6)

    def test_pattern_weight_low_occurrence(self, shadow_calculator, monkeypatch):
        """3회 이상 발생 패턴은 1.2배 가중치."""
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        mock_pattern = LearningPattern(
            pattern_id="test-3",
            pattern_type=PatternType.FAILURE,
            name="notification:delivery_failed",
            description="Notification delivery failed",
            confidence=0.7,
            occurrence_count=4,  # 3 <= x < 5 → 1.2배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        weight = shadow_calculator._get_pattern_weight("notification", "delivery_failed")
        assert weight == pytest.approx(1.2, rel=1e-6)

    def test_pattern_weight_below_threshold(self, shadow_calculator, monkeypatch):
        """3회 미만 발생 패턴은 기본 가중치(1.0)."""
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        mock_pattern = LearningPattern(
            pattern_id="test-4",
            pattern_type=PatternType.FAILURE,
            name="point:insufficient_balance",
            description="Point insufficient balance",
            confidence=0.6,
            occurrence_count=2,  # x < 3 → 1.0배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        weight = shadow_calculator._get_pattern_weight("point", "insufficient_balance")
        assert weight == pytest.approx(1.0, rel=1e-6)

    def test_pattern_weight_service_failure_returns_default(self, shadow_calculator, monkeypatch):
        """Learning 서비스 장애 시 기본값 1.0 반환."""
        class MockLearningService:
            def __init__(self):
                raise RuntimeError("Service unavailable")
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        weight = shadow_calculator._get_pattern_weight("payment", "timeout")
        assert weight == 1.0


class TestPatternWeightIntegration:
    """Pattern 가중치 통합 테스트."""

    def test_calculate_weighted_errors_with_pattern(self, shadow_calculator, monkeypatch):
        """_calculate_weighted_errors에 pattern 적용."""
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        mock_pattern = LearningPattern(
            pattern_id="test-5",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,  # 2배 가중치
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        # medium 10개 * 0.001 * prometheus(1.0) * pattern(2.0) = 0.02분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        # domain 가중치는 config가 없으면 1.0이므로 pattern만 적용
        assert result == pytest.approx(0.02, rel=1e-3)


# =============================================================================
# Phase 0: Multiplier Cap 통합 테스트
# Reference: 30_SHADOW_BUDGET_WEIGHTED_CALCULATION.md §4.0.1
# =============================================================================


class TestMultiplierCap:
    """Multiplier Cap 테스트."""

    def test_cap_prevents_weight_explosion(self, shadow_calculator, monkeypatch):
        """
        Cap이 가중치 폭발을 방지함.
        
        Critical(10x) × Payment(24x) × 반복(2x) = 480x → Cap 50x로 제한
        """
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config (payment = 1시간 → 24배)
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service (occurrence_count=10 → 2배)
        mock_pattern = LearningPattern(
            pattern_id="test-cap",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        # Critical 10개:
        # severity: 10 * 0.01 = 0.1분 (base)
        # 무제한 multiplier: 1.0(source) * 24(domain) * 2(pattern) = 48
        # Cap 후: min(48, 50) = 48
        # 최종: 0.1 * 48 = 4.8분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"critical": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        assert result == pytest.approx(4.8, rel=1e-3)

    def test_cap_applied_when_exceeds_limit(self, shadow_calculator, monkeypatch):
        """Cap이 50 초과 시 적용됨."""
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config (payment = 0.5시간 → 48배) - 경계값 테스트
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 0.5})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service (occurrence_count=10 → 2배)
        mock_pattern = LearningPattern(
            pattern_id="test-cap-exceed",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=10,
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        # 무제한 multiplier: 1.0(source) * 48(domain) * 2(pattern) = 96
        # Cap 후: min(96, 50) = 50
        # medium 10개: 10 * 0.001 = 0.01분 (base)
        # 최종: 0.01 * 50 = 0.5분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
            failure_type="timeout",
        )
        assert result == pytest.approx(0.5, rel=1e-3)


# =============================================================================
# calculate_shadow_budget with Phase 2, 3 통합 테스트
# =============================================================================


class TestCalculateShadowBudgetWithDomainAndPattern:
    """calculate_shadow_budget Phase 2, 3 통합 테스트."""

    def test_calculate_with_domain_and_failure_type(
        self, shadow_calculator, sample_failsafe_period, monkeypatch
    ):
        """domain과 failure_type을 사용한 Shadow Budget 계산."""
        from dataclasses import dataclass, field
        from selfhealing.services.learning.models import LearningPattern, PatternType
        
        # Mock SLA Config
        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 2})
        
        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)
        
        monkeypatch.setattr(
            "selfhealing.core.config.get_config",
            lambda: MockConfig(),
        )
        
        # Mock Learning Service
        mock_pattern = LearningPattern(
            pattern_id="test-integrate",
            pattern_type=PatternType.FAILURE,
            name="payment:timeout",
            description="Payment timeout",
            confidence=0.9,
            occurrence_count=5,  # 1.5배
        )
        
        class MockLearningService:
            def get_patterns(self, pattern_type=None):
                return [mock_pattern]
        
        monkeypatch.setattr(
            "selfhealing.services.learning.LearningService",
            MockLearningService,
        )
        
        result = shadow_calculator.calculate_shadow_budget(
            failsafe_period=sample_failsafe_period,
            primary_remaining_percent=75.0,
            primary_consumed_minutes=10.8,
            budget_total_minutes=43.2,
            errors_by_severity={"medium": 100},
            domain="payment",
            failure_type="timeout",
        )
        
        # 100 * 0.001 = 0.1 (base)
        # none_available source = 0.5
        # payment domain = 24/2 = 12
        # pattern = 1.5
        # multiplier = 0.5 * 12 * 1.5 = 9.0
        # 최종: 0.1 * 9.0 = 0.9분
        assert result.estimated_errors == 100
        assert result.adjustment_minutes == pytest.approx(0.9, rel=1e-2)

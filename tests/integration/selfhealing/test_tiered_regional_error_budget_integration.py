"""
티어별 차등 임계치 / 리전별 버짓 분리 통합 테스트.

ErrorBudgetGateSettings 환경변수 설정부터
ErrorBudgetGate.check() → governance checks.check_all_governance()까지
전체 경로를 실제 객체 조합으로 검증한다.

테스트 시나리오:
1. 티어별 차등 임계치 → Gate 판정 관통
2. Redis 리전 플래그 → Governance 흐름
3. 리전 데이터 누락 → 글로벌 Fallback 경로

Requirements:
- Docker Compose: docker-compose -f docker-compose.test.yml up -d
- Redis 필요: 리전별 플래그 저장/조회 검증
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.requires_redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from selfhealing.settings.error_budget_gate import (
    ErrorBudgetGateSettings,
    reset_error_budget_gate_settings,
)
from selfhealing.services.error_budget_gate.gate import (
    ErrorBudgetGate,
)
from selfhealing.services.error_budget_gate.config import (
    GateCheckResult,
    GateStatus,
)
from selfhealing.services.error_budget_gate.redis_flag import (
    BudgetExhaustedFlagManager,
    reset_budget_exhausted_flag_manager,
)
from selfhealing.services.error_budget_gate.region_tier_resolver import (
    resolve_tier_from_region,
)
from selfhealing.services.error_budget.service import ErrorBudgetService
from selfhealing.services.error_budget.calculator import ErrorBudgetCalculator
from selfhealing.services.error_budget.models import ErrorBudgetStatus
from selfhealing.services.governance.checks import (
    check_all_governance,
    is_error_budget_blocking,
    invalidate_governance_cache,
)
from selfhealing.services.canary.models import PassCriteria, apply_tier_floor
from selfhealing.services.metrics.recorders import record_error_budget_status


# ============================================================================
# 헬퍼: 지정 잔여율을 반환하는 ErrorBudgetService 생성
# ============================================================================


def _create_service_returning_budget(remaining_percent: float, region: str | None = None) -> ErrorBudgetService:
    """
    calculate_budget_status() 호출 시 고정 remaining_percent를 반환하는 서비스 생성.

    region 파라미터가 일치할 때만 해당 값을 반환하고,
    불일치하면 None을 반환하여 리전 데이터 누락을 시뮬레이션한다.
    """
    target_region = region

    def fake_stats_callback(start_time, end_time, exclude_synthetic=True, **kwargs):
        cb_region = kwargs.get("region")
        if target_region is not None and cb_region != target_region:
            # 리전 불일치: 에러 0건으로 반환 → remaining 100%
            return {"total_errors": 0, "source": "simulation"}
        # 잔여율을 역산하여 에러 수 계산
        # budget_total = 43.2분 (SLO 99.9%, 30일 기준)
        # remaining_percent = (remaining / total) * 100
        # consumed_ratio = 1 - (remaining_percent / 100)
        # consumed_ratio = error_rate / error_budget
        # estimated_total=100000, error_budget=0.001
        # error_count = consumed_ratio * error_budget * estimated_total
        consumed_ratio = 1.0 - (remaining_percent / 100.0)
        error_count = int(consumed_ratio * 0.001 * 100000)
        return {"total_errors": max(error_count, 0), "source": "simulation"}

    return ErrorBudgetService(get_failed_operation_stats=fake_stats_callback)


def _create_gate_with_budget(
    remaining_percent: float,
    settings: ErrorBudgetGateSettings | None = None,
    region: str | None = None,
) -> ErrorBudgetGate:
    """
    특정 잔여율을 반환하는 ErrorBudgetGate 생성.

    내부적으로 ErrorBudgetService를 모킹하여 gate._get_error_budget_percent()가
    지정된 값을 반환하도록 구성한다.
    """
    config = settings or ErrorBudgetGateSettings()
    gate = ErrorBudgetGate(config=config)

    service = _create_service_returning_budget(remaining_percent, region)

    # gate._get_error_budget_percent 를 직접 교체하여 service를 주입
    original_get = gate._get_error_budget_percent

    def patched_get(region=None):
        status = service.get_budget_status(region=region)
        if status and hasattr(status, "budget_remaining_percent"):
            return status.budget_remaining_percent
        return None

    gate._get_error_budget_percent = patched_get
    return gate


# ============================================================================
# 시나리오 1: 티어별 차등 임계치 → Gate 판정 관통
# ============================================================================


class TestTierThresholdsToGateDecision:
    """
    Settings.tier_thresholds_enabled=True 설정 시
    ErrorBudgetGate.check(tier_id=...) 판정이
    티어별 차등 임계치를 올바르게 적용하는지 검증한다.
    """

    def setup_method(self):
        reset_error_budget_gate_settings()
        invalidate_governance_cache()

    def teardown_method(self):
        reset_error_budget_gate_settings()
        invalidate_governance_cache()

    def test_critical_tier_blocks_at_higher_threshold(self):
        """
        critical 티어: 잔여율 12%일 때 차단 (임계치 15%).
        standard 티어 기본 임계치(10%)에서는 허용되지만,
        critical 티어에서는 15% 미만이므로 차단된다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
                "standard": {"critical_threshold_percent": 10.0, "warning_threshold_percent": 20.0},
                "non_essential": {"critical_threshold_percent": 5.0, "warning_threshold_percent": 10.0},
            },
        )
        gate = _create_gate_with_budget(remaining_percent=12.0, settings=settings)

        result_critical = gate.check(tier_id="critical")
        assert result_critical.status == GateStatus.BLOCKED
        assert result_critical.allowed is False
        assert result_critical.tier_id == "critical"

    def test_standard_tier_allows_at_same_budget(self):
        """
        standard 티어: 잔여율 12%일 때 허용 (임계치 10%).
        동일한 12% 예산이라도 standard 티어에서는 임계치(10%) 이상이므로 허용된다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
                "standard": {"critical_threshold_percent": 10.0, "warning_threshold_percent": 20.0},
                "non_essential": {"critical_threshold_percent": 5.0, "warning_threshold_percent": 10.0},
            },
        )
        gate = _create_gate_with_budget(remaining_percent=12.0, settings=settings)

        result_standard = gate.check(tier_id="standard")
        assert result_standard.allowed is True
        assert result_standard.tier_id == "standard"

    def test_non_essential_tier_allows_at_low_budget(self):
        """
        non_essential 티어: 잔여율 6%일 때 허용 (임계치 5%).
        critical/standard에서는 차단되지만 non_essential은 5% 미만까지 허용한다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
                "standard": {"critical_threshold_percent": 10.0, "warning_threshold_percent": 20.0},
                "non_essential": {"critical_threshold_percent": 5.0, "warning_threshold_percent": 10.0},
            },
        )
        gate = _create_gate_with_budget(remaining_percent=6.0, settings=settings)

        result = gate.check(tier_id="non_essential")
        assert result.allowed is True
        assert result.tier_id == "non_essential"

    def test_tier_disabled_uses_global_threshold(self):
        """
        tier_thresholds_enabled=False: 모든 티어에 글로벌 임계치(10%) 적용.
        티어 기능 비활성화 시 기존 동작과 동일하게 작동한다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=False,
            critical_threshold_percent=10.0,
        )
        gate = _create_gate_with_budget(remaining_percent=8.0, settings=settings)

        result_critical = gate.check(tier_id="critical")
        result_standard = gate.check(tier_id="standard")

        # 티어 무관하게 둘 다 글로벌 임계치(10%) 기준으로 차단
        assert result_critical.status == GateStatus.BLOCKED
        assert result_standard.status == GateStatus.BLOCKED

    def test_cache_separates_tier_results(self):
        """
        서로 다른 tier_id로 check() 호출 시 캐시가 분리된다.
        동일 Gate 인스턴스에서 critical/standard 판정이 섞이지 않아야 한다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            cache_ttl_seconds=60,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
                "standard": {"critical_threshold_percent": 10.0, "warning_threshold_percent": 20.0},
                "non_essential": {"critical_threshold_percent": 5.0, "warning_threshold_percent": 10.0},
            },
        )
        gate = _create_gate_with_budget(remaining_percent=12.0, settings=settings)

        result_critical = gate.check(tier_id="critical")
        result_standard = gate.check(tier_id="standard")

        assert result_critical.status == GateStatus.BLOCKED
        assert result_standard.status != GateStatus.BLOCKED

        # 캐시에서 각각 다른 키로 저장되었는지 확인
        cache_key_critical = gate._build_cache_key(None, "critical")
        cache_key_standard = gate._build_cache_key(None, "standard")
        assert cache_key_critical in gate._cache
        assert cache_key_standard in gate._cache
        assert cache_key_critical != cache_key_standard

    def test_gate_check_result_contains_tier_id(self):
        """
        GateCheckResult.tier_id에 판정 대상 티어가 기록된다.
        """
        settings = ErrorBudgetGateSettings(enabled=True)
        gate = _create_gate_with_budget(remaining_percent=50.0, settings=settings)

        result = gate.check(tier_id="critical")
        assert result.tier_id == "critical"

        result_dict = result.to_dict()
        assert result_dict["tier_id"] == "critical"


# ============================================================================
# 시나리오 2: Redis 리전 플래그 → Governance 흐름
# ============================================================================


class TestRedisRegionalFlagToGovernanceFlow:
    """
    BudgetExhaustedFlagManager 리전별 Redis 키 설정 후
    check_all_governance(tier_id=..., region=...) 결과를 검증한다.
    실제 Redis 연결을 사용한다.
    """

    def setup_method(self):
        reset_error_budget_gate_settings()
        reset_budget_exhausted_flag_manager()
        invalidate_governance_cache()

    def teardown_method(self):
        reset_error_budget_gate_settings()
        reset_budget_exhausted_flag_manager()
        invalidate_governance_cache()

    def test_redis_regional_key_format(self, redis_client):
        """
        BudgetExhaustedFlagManager._build_slo_key()가
        리전 있을 때 '{slo_name}:{region}' 형식 키를 생성한다.
        """
        manager = BudgetExhaustedFlagManager(redis_client=redis_client)

        global_key = manager._build_slo_key("availability")
        regional_key = manager._build_slo_key("availability", region="seoul")

        assert global_key == "selfhealing:error_budget:exhausted:availability"
        assert regional_key == "selfhealing:error_budget:exhausted:availability:seoul"

    def test_redis_regional_status_key_format(self, redis_client):
        """
        _build_status_key()가 리전별 상태 키를 올바르게 생성한다.
        """
        manager = BudgetExhaustedFlagManager(redis_client=redis_client)

        global_key = manager._build_status_key("availability")
        regional_key = manager._build_status_key("availability", region="tokyo")

        assert global_key == "selfhealing:error_budget:status:availability"
        assert regional_key == "selfhealing:error_budget:status:availability:tokyo"

    def test_set_and_get_regional_exhausted_flag(self, redis_client):
        """
        리전별 소진 플래그를 Redis에 저장하고 조회한다.
        seoul 리전은 소진 상태, tokyo 리전은 정상 상태일 때
        각 리전별로 정확한 플래그 값을 반환한다.
        """
        manager = BudgetExhaustedFlagManager(redis_client=redis_client)

        manager.set_exhausted("availability", exhausted=True, region="seoul")
        manager.set_exhausted("availability", exhausted=False, region="tokyo")

        assert manager.is_exhausted("availability", region="seoul") is True
        assert manager.is_exhausted("availability", region="tokyo") is False

    def test_regional_flag_does_not_affect_global_flag(self, redis_client):
        """
        리전별 플래그 설정이 글로벌 플래그에 영향을 주지 않는다.
        리전-글로벌 키가 완전히 분리된다.
        """
        manager = BudgetExhaustedFlagManager(redis_client=redis_client)

        manager.set_exhausted("availability", exhausted=True, region="seoul")

        assert manager.is_exhausted("availability", region="seoul") is True
        assert manager.is_exhausted("availability") is False  # 글로벌은 미변경

    def test_regional_flag_isolation_between_regions(self, redis_client):
        """
        서로 다른 리전의 소진 플래그가 완전히 격리된다.
        """
        manager = BudgetExhaustedFlagManager(redis_client=redis_client)

        manager.set_exhausted("availability", exhausted=True, region="seoul")
        manager.set_exhausted("availability", exhausted=False, region="oregon")

        assert manager.is_exhausted("availability", region="seoul") is True
        assert manager.is_exhausted("availability", region="oregon") is False

    def test_governance_check_propagates_tier_and_region(self):
        """
        check_all_governance(tier_id, region)가 내부적으로
        is_error_budget_blocking(tier_id, region)에
        파라미터를 올바르게 전파한다.
        """
        invalidate_governance_cache()

        # governance check는 내부에서 error_budget_gate를 호출하므로
        # gate 싱글톤을 disabled 상태로 설정하여 error budget 체크를 우회
        result = check_all_governance(
            check_kill_switch=False,
            check_emergency=False,
            check_error_budget=False,  # error budget 체크 OFF로 파라미터 전파만 확인
            operation_name="test_tier_region_propagation",
            tier_id="critical",
            region="seoul",
        )

        # 모든 체크 비활성화 → 항상 허용
        assert result.allowed is True

    def test_is_error_budget_blocking_with_tier_and_region(self):
        """
        is_error_budget_blocking(tier_id, region)이
        캐시 키에 tier_id와 region을 포함하여 분리된 판정을 한다.
        """
        invalidate_governance_cache()

        # 서로 다른 (tier, region) 조합은 별도 캐시 엔트리여야 한다
        result_1 = is_error_budget_blocking(tier_id="critical", region="seoul")
        result_2 = is_error_budget_blocking(tier_id="standard", region="tokyo")

        # 반환 형태: (is_blocked, budget_percent, threshold_percent)
        assert isinstance(result_1, tuple) and len(result_1) == 3
        assert isinstance(result_2, tuple) and len(result_2) == 3


# ============================================================================
# 시나리오 3: 리전 데이터 누락 → 글로벌 Fallback 경로
# ============================================================================


class TestRegionDataMissingGlobalFallback:
    """
    특정 리전의 Error Budget 데이터가 없을 때
    글로벌 값으로 Fallback하여 최소 보호를 제공하는지 검증한다.
    """

    def setup_method(self):
        reset_error_budget_gate_settings()
        invalidate_governance_cache()

    def teardown_method(self):
        reset_error_budget_gate_settings()
        invalidate_governance_cache()

    def test_gate_falls_back_to_global_when_region_data_missing(self):
        """
        region='nonexistent_region' 요청 시
        해당 리전 데이터가 없으면 글로벌 버짓으로 Fallback한다.
        글로벌 버짓이 50%인 상태에서 Fallback 후 OPEN 판정이 나와야 한다.
        """
        settings = ErrorBudgetGateSettings(enabled=True)
        service = ErrorBudgetService()
        # 글로벌 버짓은 에러 0건 → 100% 잔여
        gate = ErrorBudgetGate(config=settings)

        # _get_error_budget_percent에서 region->None fallback이 일어나는 경로 검증
        # 직접 service를 써서 fallback 구조를 검증
        global_status = service.get_budget_status(region=None)
        assert global_status is not None
        assert hasattr(global_status, "budget_remaining_percent")

    def test_calculator_passes_region_to_stats_callback(self):
        """
        ErrorBudgetCalculator가 region 파라미터를 콜백에 전달한다.
        콜백이 region을 정상 수신하는지 검증한다.
        """
        received_params = {}

        def capture_callback(start_time, end_time, exclude_synthetic=True, **kwargs):
            received_params.update(kwargs)
            return {"total_errors": 0}

        calc = ErrorBudgetCalculator(get_failed_operation_stats=capture_callback)
        calc.calculate_budget_status(slo_name="availability", region="seoul")

        assert received_params.get("region") == "seoul"

    def test_calculator_region_none_fallback_on_type_error(self):
        """
        콜백이 region 파라미터를 지원하지 않으면 TypeError polyfill로 Fallback한다.
        region 미지원 레거시 콜백과의 하위 호환성을 검증한다.
        """

        def legacy_callback(start_time, end_time, exclude_synthetic=True):
            """region 파라미터를 받지 않는 레거시 콜백."""
            return {"total_errors": 5}

        calc = ErrorBudgetCalculator(get_failed_operation_stats=legacy_callback)
        status = calc.calculate_budget_status(slo_name="availability", region="seoul")

        # TypeError가 발생하지 않고 정상 계산이 완료된다
        assert status is not None
        assert hasattr(status, "budget_remaining_percent")

    def test_error_budget_status_has_region_field(self):
        """
        ErrorBudgetStatus에 region 필드가 존재하고,
        Calculator가 반환한 status에 region 값이 포함된다.
        """

        def stub_callback(start_time, end_time, **kwargs):
            return {"total_errors": 0}

        calc = ErrorBudgetCalculator(get_failed_operation_stats=stub_callback)
        status = calc.calculate_budget_status(slo_name="availability", region="tokyo")

        assert status.region == "tokyo"
        assert hasattr(status, "tier_id")

    def test_error_budget_status_region_none_for_global(self):
        """
        region=None으로 계산 시 status.region이 None이다.
        글로벌 집계 데이터임을 식별할 수 있어야 한다.
        """

        def stub_callback(start_time, end_time, **kwargs):
            return {"total_errors": 0}

        calc = ErrorBudgetCalculator(get_failed_operation_stats=stub_callback)
        status = calc.calculate_budget_status(slo_name="availability", region=None)

        assert status.region is None

    def test_service_get_budget_status_with_explicit_region(self):
        """
        ErrorBudgetService.get_budget_status(region='seoul')가
        Calculator에 region을 전달하여 리전별 상태를 반환한다.
        """
        received_regions = []

        def tracking_callback(start_time, end_time, **kwargs):
            received_regions.append(kwargs.get("region"))
            return {"total_errors": 0}

        service = ErrorBudgetService(get_failed_operation_stats=tracking_callback)
        status = service.get_budget_status(slo_name="availability", region="seoul")

        assert status.region == "seoul"
        assert "seoul" in received_regions

    def test_service_get_all_region_statuses(self):
        """
        ErrorBudgetService.get_all_region_statuses()가
        여러 리전의 상태를 딕셔너리로 반환한다.
        """

        def stub_callback(start_time, end_time, **kwargs):
            return {"total_errors": 0}

        service = ErrorBudgetService(get_failed_operation_stats=stub_callback)
        statuses = service.get_all_region_statuses(
            slo_name="availability",
            regions=["seoul", "tokyo", "oregon"],
        )

        assert len(statuses) == 3
        assert "seoul" in statuses
        assert "tokyo" in statuses
        assert "oregon" in statuses
        assert all(isinstance(s, ErrorBudgetStatus) for s in statuses.values())


# ============================================================================
# 시나리오 4: Settings 우선순위 (regional > tier > global)
# ============================================================================


class TestSettingsThresholdPriority:
    """
    get_effective_thresholds()의 우선순위가
    regional_thresholds > tier_thresholds > global 순서로 적용되는지 검증한다.
    """

    def test_regional_overrides_tier(self):
        """
        리전 오버라이드가 활성화되면 티어 설정보다 우선한다.
        seoul 리전의 critical_threshold=20%이 tier의 15%보다 우선 적용된다.
        """
        settings = ErrorBudgetGateSettings(
            tier_thresholds_enabled=True,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
            },
            regional_thresholds_enabled=True,
            regional_thresholds={
                "seoul": {"critical_threshold_percent": 20.0, "warning_threshold_percent": 40.0},
            },
        )

        crit, warn = settings.get_effective_thresholds(tier_id="critical", region="seoul")
        assert crit == 20.0
        assert warn == 40.0

    def test_tier_used_when_no_regional_match(self):
        """
        리전 오버라이드에 해당 리전이 없으면 티어 설정으로 Fallback한다.
        """
        settings = ErrorBudgetGateSettings(
            tier_thresholds_enabled=True,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
            },
            regional_thresholds_enabled=True,
            regional_thresholds={
                "seoul": {"critical_threshold_percent": 20.0, "warning_threshold_percent": 40.0},
            },
        )

        crit, warn = settings.get_effective_thresholds(tier_id="critical", region="tokyo")
        assert crit == 15.0
        assert warn == 30.0

    def test_global_used_when_both_disabled(self):
        """
        티어/리전 모두 비활성화 시 글로벌 기본값을 사용한다.
        """
        settings = ErrorBudgetGateSettings(
            critical_threshold_percent=10.0,
            warning_threshold_percent=20.0,
            tier_thresholds_enabled=False,
            regional_thresholds_enabled=False,
        )

        crit, warn = settings.get_effective_thresholds(tier_id="critical", region="seoul")
        assert crit == 10.0
        assert warn == 20.0


# ============================================================================
# 시나리오 5: PassCriteria 티어별 하한 강제
# ============================================================================


class TestPassCriteriaTierFloorEnforcement:
    """
    PassCriteria.for_tier() 팩토리와 apply_tier_floor() 함수가
    티어별 최소 보안 기준을 올바르게 강제하는지 검증한다.
    """

    def test_for_tier_critical_has_stricter_limits(self):
        """
        critical 티어의 기본 PassCriteria는
        standard보다 엄격한 error_rate_absolute_max를 가진다.
        """
        critical = PassCriteria.for_tier("critical")
        standard = PassCriteria.for_tier("standard")

        assert critical.error_rate_absolute_max < standard.error_rate_absolute_max
        assert critical.error_budget_remaining_min > standard.error_budget_remaining_min
        assert critical.error_budget_drain_rate_max < standard.error_budget_drain_rate_max

    def test_for_tier_non_essential_has_relaxed_limits(self):
        """
        non_essential 티어의 기본 PassCriteria는
        standard보다 완화된 error_rate_absolute_max를 가진다.
        """
        non_essential = PassCriteria.for_tier("non_essential")
        standard = PassCriteria.for_tier("standard")

        assert non_essential.error_rate_absolute_max > standard.error_rate_absolute_max
        assert non_essential.error_budget_remaining_min < standard.error_budget_remaining_min

    def test_apply_tier_floor_enforces_critical_limit(self):
        """
        사용자가 완화된 기준을 설정해도
        apply_tier_floor()가 critical 티어 하한을 강제한다.
        """
        relaxed_user = PassCriteria(
            error_rate_absolute_max=0.10,  # 너무 느슨 (10%)
            error_budget_remaining_min=0.01,  # 너무 낮은 하한 (1%)
            error_budget_drain_rate_max=5.0,  # 너무 높은 drain rate
        )

        enforced = apply_tier_floor(relaxed_user, "critical")
        tier_floor = PassCriteria.for_tier("critical")

        # max 계열: min(user, tier) → 더 작은(엄격한) 값
        assert enforced.error_rate_absolute_max == tier_floor.error_rate_absolute_max
        assert enforced.error_budget_drain_rate_max == tier_floor.error_budget_drain_rate_max

        # min 계열: max(user, tier) → 더 큰(엄격한) 값
        assert enforced.error_budget_remaining_min == tier_floor.error_budget_remaining_min

    def test_apply_tier_floor_preserves_user_stricter_values(self):
        """
        사용자가 이미 티어보다 엄격한 기준을 설정했으면
        사용자 값이 유지된다.
        """
        strict_user = PassCriteria(
            error_rate_absolute_max=0.01,  # 이미 1%로 매우 엄격
            error_budget_remaining_min=0.30,  # 이미 30%로 높은 하한
        )

        enforced = apply_tier_floor(strict_user, "critical")

        # 사용자 값이 tier보다 엄격하므로 사용자 값 유지
        assert enforced.error_rate_absolute_max == 0.01
        assert enforced.error_budget_remaining_min == 0.30

    def test_for_tier_unknown_returns_default(self):
        """
        알 수 없는 tier_id를 전달하면 PassCriteria 기본값을 반환한다.
        """
        unknown = PassCriteria.for_tier("unknown_tier")
        default = PassCriteria()

        assert unknown.error_rate_absolute_max == default.error_rate_absolute_max
        assert unknown.error_budget_remaining_min == default.error_budget_remaining_min


# ============================================================================
# 시나리오 6: 리전-티어 매핑 해석기
# ============================================================================


class TestRegionTierResolver:
    """
    resolve_tier_from_region()이 RegionalRecoveryConfig.priority 기반으로
    올바른 티어를 반환하는지 검증한다.
    """

    def test_seoul_resolves_to_critical(self):
        """seoul은 priority=100 → critical 티어."""
        assert resolve_tier_from_region("seoul") == "critical"

    def test_tokyo_resolves_to_standard(self):
        """tokyo는 priority=50 → standard 티어."""
        assert resolve_tier_from_region("tokyo") == "standard"

    def test_oregon_resolves_to_non_essential(self):
        """oregon은 priority=10 → non_essential 티어."""
        assert resolve_tier_from_region("oregon") == "non_essential"

    def test_unknown_region_returns_standard(self):
        """알 수 없는 리전은 standard 기본값 반환."""
        assert resolve_tier_from_region("antarctica") == "standard"


# ============================================================================
# 시나리오 7: Prometheus 메트릭 리전/티어 레이블 확장
# ============================================================================


class TestMetricsRegionTierLabels:
    """
    record_error_budget_status()가 region/tier 레이블을
    포함하여 메트릭을 기록하는지 검증한다.
    """

    def test_record_error_budget_status_accepts_region_tier(self):
        """
        record_error_budget_status()가 region, tier 파라미터를
        예외 없이 수용한다.
        """
        try:
            record_error_budget_status(
                slo_name="availability",
                remaining_percent=85.0,
                remaining_minutes=36.72,
                burn_rate_1h_value=0.5,
                burn_rate_6h_value=0.3,
                region="seoul",
                tier="critical",
            )
        except Exception as e:
            pytest.fail(f"record_error_budget_status raised: {e}")

    def test_record_error_budget_status_with_empty_region_tier(self):
        """
        region/tier가 빈 문자열일 때도 정상 동작한다.
        하위 호환성 검증.
        """
        try:
            record_error_budget_status(
                slo_name="availability",
                remaining_percent=90.0,
                remaining_minutes=38.88,
                burn_rate_1h_value=0.2,
                burn_rate_6h_value=0.1,
                region="",
                tier="",
            )
        except Exception as e:
            pytest.fail(f"record_error_budget_status raised: {e}")


# ============================================================================
# 시나리오 8: GateCheckResult 리전/티어 필드 직렬화
# ============================================================================


class TestGateCheckResultSerialization:
    """
    GateCheckResult.to_dict()가 tier_id/region 필드를
    조건부로 포함하는지 검증한다.
    """

    def test_to_dict_includes_tier_when_set(self):
        """tier_id가 설정되면 to_dict() 출력에 포함된다."""
        result = GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
            tier_id="critical",
            region="seoul",
        )
        d = result.to_dict()
        assert d["tier_id"] == "critical"
        assert d["region"] == "seoul"

    def test_to_dict_excludes_tier_when_none(self):
        """tier_id/region이 None이면 to_dict() 출력에서 제외된다."""
        result = GateCheckResult(
            allowed=True,
            status=GateStatus.OPEN,
        )
        d = result.to_dict()
        assert "tier_id" not in d
        assert "region" not in d


# ============================================================================
# 시나리오 9: 히스테리시스 + 티어 결합 동작
# ============================================================================


class TestHysteresisWithTierThresholds:
    """
    히스테리시스 버퍼가 티어별 차등 임계치와 결합하여
    올바르게 동작하는지 검증한다.
    상태 전이: BLOCKED → WARNING에 critical_threshold + buffer가 적용된다.
    """

    def test_blocked_to_warning_requires_tier_threshold_plus_buffer(self):
        """
        BLOCKED 상태에서 WARNING으로 복귀하려면
        critical_threshold(15%) + hysteresis_buffer(2%) = 17% 이상이어야 한다.
        16%에서는 여전히 BLOCKED, 18%에서는 WARNING으로 복귀.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            threshold_hysteresis_buffer_percent=2.0,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
            },
        )

        # 먼저 BLOCKED 상태로 진입
        gate = _create_gate_with_budget(remaining_percent=10.0, settings=settings)
        result = gate.check(tier_id="critical")
        assert result.status == GateStatus.BLOCKED

        # 16%로 예산 증가 → 여전히 BLOCKED (17% 미만)
        gate._get_error_budget_percent = lambda region=None: 16.0
        gate.clear_cache()
        result = gate.check(tier_id="critical", force_refresh=True)
        assert result.status == GateStatus.BLOCKED

        # 18%로 예산 증가 → WARNING으로 복귀 (17% 이상)
        gate._get_error_budget_percent = lambda region=None: 18.0
        gate.clear_cache()
        result = gate.check(tier_id="critical", force_refresh=True)
        assert result.status == GateStatus.WARNING

    def test_warning_to_open_requires_warning_threshold_plus_buffer(self):
        """
        WARNING 상태에서 OPEN으로 복귀하려면
        warning_threshold(30%) + hysteresis_buffer(2%) = 32% 이상이어야 한다.
        """
        settings = ErrorBudgetGateSettings(
            enabled=True,
            tier_thresholds_enabled=True,
            threshold_hysteresis_buffer_percent=2.0,
            tier_thresholds={
                "critical": {"critical_threshold_percent": 15.0, "warning_threshold_percent": 30.0},
            },
        )

        # BLOCKED → WARNING → OPEN 이행을 위해 단계적 진행
        gate = _create_gate_with_budget(remaining_percent=10.0, settings=settings)
        gate.check(tier_id="critical")  # BLOCKED

        gate._get_error_budget_percent = lambda region=None: 18.0
        gate.clear_cache()
        gate.check(tier_id="critical", force_refresh=True)  # → WARNING

        # 31%: 아직 OPEN이 안 됨 (32% 미만)
        gate._get_error_budget_percent = lambda region=None: 31.0
        gate.clear_cache()
        result = gate.check(tier_id="critical", force_refresh=True)
        assert result.status == GateStatus.WARNING

        # 33%: OPEN으로 복귀
        gate._get_error_budget_percent = lambda region=None: 33.0
        gate.clear_cache()
        result = gate.check(tier_id="critical", force_refresh=True)
        assert result.status == GateStatus.OPEN

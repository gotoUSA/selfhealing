"""
DNA Zero-Base 모듈 테스트

Phase 4: Zero-Base 시나리오 탐험 테스트
"""

import pytest
from datetime import datetime
from typing import Dict, Any

from load_tests.utils.selfhealing.dna_zerobase import (
    ZeroBaseExplorer,
    ZeroBaseDNAGenerator,
    ExplorationMode,
    ExplorationTarget,
    Discovery,
    DiscoveryType,
    ExplorationReport,
)


class TestExplorationMode:
    """ExplorationMode 테스트"""
    
    def test_mode_values(self):
        """모드 값 확인"""
        assert ExplorationMode.CONSERVATIVE.value == "conservative"
        assert ExplorationMode.NORMAL.value == "normal"
        assert ExplorationMode.AGGRESSIVE.value == "aggressive"
        assert ExplorationMode.EXHAUSTIVE.value == "exhaustive"


class TestDiscoveryType:
    """DiscoveryType 테스트"""
    
    def test_discovery_type_values(self):
        """발견 유형 값 확인"""
        assert DiscoveryType.NEW_MODULE_NEEDED.value == "new_module_needed"
        assert DiscoveryType.EXISTING_MODULE_APPLICABLE.value == "existing_applicable"
        assert DiscoveryType.OPTIMIZATION_OPPORTUNITY.value == "optimization"
        assert DiscoveryType.EDGE_CASE_FOUND.value == "edge_case"
        assert DiscoveryType.NO_ACTION_NEEDED.value == "no_action"


class TestExplorationTarget:
    """ExplorationTarget 테스트"""
    
    def test_create_target(self):
        """탐험 대상 생성"""
        target = ExplorationTarget(
            name="test_target",
            category="test",
            description="Test description",
            priority=8,
            estimated_risk=0.5,
        )
        
        assert target.name == "test_target"
        assert target.category == "test"
        assert target.priority == 8
        assert target.estimated_risk == 0.5
    
    def test_default_values(self):
        """기본값 확인"""
        target = ExplorationTarget(
            name="minimal",
            category="test",
            description="Minimal target",
        )
        
        assert target.priority == 5
        assert target.estimated_risk == 0.5
        assert target.test_fn is None


class TestDiscovery:
    """Discovery 테스트"""
    
    def test_create_discovery(self):
        """발견 생성"""
        discovery = Discovery(
            target_name="test",
            timestamp=datetime.now().isoformat(),
            discovery_type=DiscoveryType.NEW_MODULE_NEEDED,
            suggested_module="test_handler",
            confidence=0.8,
        )
        
        assert discovery.target_name == "test"
        assert discovery.discovery_type == DiscoveryType.NEW_MODULE_NEEDED
        assert discovery.suggested_module == "test_handler"
        assert discovery.confidence == 0.8
    
    def test_to_dict(self):
        """딕셔너리 변환"""
        discovery = Discovery(
            target_name="test",
            timestamp="2025-12-29T12:00:00",
            discovery_type=DiscoveryType.EXISTING_MODULE_APPLICABLE,
            existing_module="circuit_breaker",
            confidence=0.9,
        )
        
        result = discovery.to_dict()
        assert result["target"] == "test"
        assert result["type"] == "existing_applicable"
        assert result["existing_module"] == "circuit_breaker"
        assert result["confidence"] == 0.9


class TestZeroBaseExplorer:
    """ZeroBaseExplorer 테스트"""
    
    @pytest.fixture
    def explorer(self):
        """탐험기 픽스처"""
        return ZeroBaseExplorer(mode=ExplorationMode.NORMAL)
    
    def test_init_default_targets(self, explorer):
        """기본 탐험 대상 초기화"""
        assert len(explorer.targets) > 0
        assert explorer.mode == ExplorationMode.NORMAL
    
    def test_add_target(self, explorer):
        """탐험 대상 추가"""
        initial_count = len(explorer.targets)
        
        new_target = ExplorationTarget(
            name="custom_target",
            category="custom",
            description="Custom test target",
        )
        explorer.add_target(new_target)
        
        assert len(explorer.targets) == initial_count + 1
    
    def test_explore_without_result(self, explorer):
        """결과 없이 탐험"""
        target = ExplorationTarget(
            name="ml_inference_test",
            category="ai",
            description="ML test",
        )
        
        discovery = explorer.explore(target, test_result=None)
        
        assert discovery.target_name == "ml_inference_test"
        assert discovery.discovery_type == DiscoveryType.NEW_MODULE_NEEDED
        assert discovery.suggested_module == "inference_optimizer"
        assert discovery.confidence == 0.5  # 낮은 신뢰도
    
    def test_explore_with_timeout_result(self, explorer):
        """타임아웃 결과로 탐험"""
        target = ExplorationTarget(
            name="api_timeout",
            category="network",
            description="API timeout test",
        )
        
        test_result = {"timeout": True, "error": "Connection timeout"}
        discovery = explorer.explore(target, test_result)
        
        assert discovery.discovery_type == DiscoveryType.EXISTING_MODULE_APPLICABLE
        assert discovery.existing_module == "circuit_breaker"
        assert discovery.confidence >= 0.8
    
    def test_explore_with_rate_exceeded(self, explorer):
        """Rate 초과 결과로 탐험"""
        target = ExplorationTarget(
            name="rate_limit_test",
            category="throttling",
            description="Rate limit test",
        )
        
        test_result = {"rate_exceeded": True}
        discovery = explorer.explore(target, test_result)
        
        assert discovery.discovery_type == DiscoveryType.EXISTING_MODULE_APPLICABLE
        assert discovery.existing_module == "rate_limiter"
    
    def test_explore_suggests_new_module(self, explorer):
        """신규 모듈 제안"""
        target = ExplorationTarget(
            name="crypto_operations",
            category="security",
            description="Crypto operations test",
        )
        
        test_result = {"slow": True, "cpu_intensive": True}
        discovery = explorer.explore(target, test_result)
        
        assert discovery.discovery_type == DiscoveryType.NEW_MODULE_NEEDED
        assert discovery.suggested_module == "crypto_offloader"
        assert discovery.module_spec is not None
    
    def test_run_exploration(self, explorer):
        """전체 탐험 실행"""
        report = explorer.run_exploration(max_targets=3)
        
        assert report.exploration_id.startswith("exp_")
        assert report.targets_explored == 3
        assert len(report.discoveries) == 3
        assert report.completed_at is not None
    
    def test_filter_targets_conservative(self):
        """보수적 모드 필터링"""
        explorer = ZeroBaseExplorer(mode=ExplorationMode.CONSERVATIVE)
        targets = explorer._filter_targets_by_mode()
        
        # 우선순위 8 이상, 위험도 0.5 이하만
        for target in targets:
            assert target.priority >= 8
            assert target.estimated_risk <= 0.5
    
    def test_filter_targets_aggressive(self):
        """공격적 모드 필터링"""
        explorer = ZeroBaseExplorer(mode=ExplorationMode.AGGRESSIVE)
        targets = explorer._filter_targets_by_mode()
        
        # 우선순위 순 정렬 확인
        for i in range(len(targets) - 1):
            assert targets[i].priority >= targets[i + 1].priority
    
    def test_suggest_new_module_from_target_name(self, explorer):
        """타겟 이름에서 신규 모듈 제안"""
        # 매핑된 제안
        assert explorer._suggest_new_module("ml_inference", {}) == "inference_optimizer"
        assert explorer._suggest_new_module("crypto_test", {}) == "crypto_offloader"
        assert explorer._suggest_new_module("cross_region", {}) == "geo_balancer"
        
        # 기본 제안
        assert explorer._suggest_new_module("unknown_feature", {}) == "unknown_feature_handler"
    
    def test_generate_module_spec(self, explorer):
        """모듈 스펙 생성"""
        target = ExplorationTarget(
            name="test_target",
            category="test",
            description="Test description",
            priority=7,
            estimated_risk=0.5,
        )
        
        spec = explorer._generate_module_spec("test_handler", target)
        
        assert "class TestHandlerClient" in spec
        assert "test_target" in spec
        assert "Test description" in spec
    
    def test_get_summary(self, explorer):
        """요약 조회"""
        # 몇 가지 탐험 실행
        explorer.run_exploration(max_targets=2)
        
        summary = explorer.get_summary()
        
        assert "total_discoveries" in summary
        assert "new_modules_needed" in summary
        assert "existing_modules_applicable" in summary
        assert "exploration_count" in summary


class TestZeroBaseDNAGenerator:
    """ZeroBaseDNAGenerator 테스트"""
    
    @pytest.fixture
    def generator(self):
        """생성기 픽스처"""
        explorer = ZeroBaseExplorer(mode=ExplorationMode.NORMAL)
        return ZeroBaseDNAGenerator(explorer)
    
    def test_generate_minimal_dna(self, generator):
        """최소 DNA 생성"""
        dna = generator.generate_minimal_dna("Stage Zero - Test")
        
        assert dna["name"] == "Stage Zero - Test"
        assert dna["type"] == "discovery"
        assert dna["required_modules"] == ["health"]
        assert dna["zero_base"]["enabled"] is True
        assert "exploration_targets" in dna
    
    def test_generate_with_custom_targets(self, generator):
        """커스텀 타겟으로 DNA 생성"""
        custom_targets = ["custom_test_1", "custom_test_2"]
        dna = generator.generate_minimal_dna(
            "Custom Stage",
            exploration_targets=custom_targets,
        )
        
        assert dna["exploration_targets"] == custom_targets
    
    def test_enrich_dna_from_discoveries(self, generator):
        """발견 결과로 DNA 보강"""
        # 탐험 실행
        generator.explorer.run_exploration(max_targets=3)
        
        base_dna = {
            "name": "Test Stage",
            "required_modules": ["health"],
        }
        
        enriched = generator.enrich_dna_from_discoveries(base_dna, min_confidence=0.5)
        
        assert "name" in enriched
        # 발견된 모듈이 추가되었는지 확인
        if generator.explorer.discoveries:
            has_additions = (
                "optional_modules" in enriched or
                "suggested_new_modules" in enriched
            )
            assert has_additions


class TestExplorationReport:
    """ExplorationReport 테스트"""
    
    def test_create_report(self):
        """리포트 생성"""
        report = ExplorationReport(
            exploration_id="exp_test",
            started_at=datetime.now().isoformat(),
            mode=ExplorationMode.NORMAL,
        )
        
        assert report.exploration_id == "exp_test"
        assert report.mode == ExplorationMode.NORMAL
        assert report.targets_explored == 0
    
    def test_to_dict(self):
        """딕셔너리 변환"""
        report = ExplorationReport(
            exploration_id="exp_test",
            started_at="2025-12-29T12:00:00",
            completed_at="2025-12-29T12:01:00",
            mode=ExplorationMode.AGGRESSIVE,
            targets_explored=5,
            new_modules_suggested=["module_a", "module_b"],
        )
        
        result = report.to_dict()
        assert result["exploration_id"] == "exp_test"
        assert result["mode"] == "aggressive"
        assert result["targets_explored"] == 5
        assert len(result["new_modules_suggested"]) == 2


class TestZeroBaseIntegration:
    """Zero-Base 통합 테스트"""
    
    def test_full_exploration_workflow(self):
        """전체 탐험 워크플로우"""
        # 1. 탐험기 생성
        explorer = ZeroBaseExplorer(mode=ExplorationMode.NORMAL)
        
        # 2. 커스텀 타겟 추가
        explorer.add_target(ExplorationTarget(
            name="custom_test",
            category="custom",
            description="Custom test",
            priority=9,
        ))
        
        # 3. 탐험 실행
        report = explorer.run_exploration(max_targets=5)
        
        # 4. 결과 검증
        assert report.targets_explored <= 5
        assert len(report.discoveries) == report.targets_explored
        assert report.success_rate >= 0
        
        # 5. DNA 생성
        generator = ZeroBaseDNAGenerator(explorer)
        dna = generator.generate_minimal_dna("Integration Test Stage")
        
        assert dna["type"] == "discovery"
        
        # 6. DNA 보강
        enriched = generator.enrich_dna_from_discoveries(dna)
        assert "name" in enriched
    
    def test_module_suggestion_for_various_targets(self):
        """다양한 타겟에 대한 모듈 제안"""
        explorer = ZeroBaseExplorer()
        
        test_cases = [
            ("ml_inference_latency", "inference_optimizer"),
            ("crypto_operations", "crypto_offloader"),
            ("cross_region_sync", "geo_balancer"),
            ("cold_start_lambda", "warm_pool_manager"),
            ("data_serialization", "schema_optimizer"),
        ]
        
        for target_name, expected_module in test_cases:
            result = explorer._suggest_new_module(target_name, {})
            assert result == expected_module, f"Expected {expected_module} for {target_name}, got {result}"
    
    def test_existing_module_detection(self):
        """기존 모듈 감지"""
        explorer = ZeroBaseExplorer()
        
        # 타임아웃 → circuit_breaker
        result = explorer._find_applicable_module("test", {"timeout": True})
        assert result == "circuit_breaker"
        
        # Rate 초과 → rate_limiter
        result = explorer._find_applicable_module("test", {"rate_exceeded": True})
        assert result == "rate_limiter"
        
        # 데이터 손상 → corruption_shield
        result = explorer._find_applicable_module("test", {"data_corruption": True})
        assert result == "corruption_shield"

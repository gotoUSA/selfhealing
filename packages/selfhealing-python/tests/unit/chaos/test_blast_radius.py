"""
Phase 1 DNA Safety 단위 테스트 - Blast Radius DNA

Reference: docs/self_healing/33_DNA_SAFETY_FEATURES.md
"""
import pytest
from load_tests.utils.selfhealing.dna_safety import (
    BlastRadiusController,
    BlastRadiusScope,
    BlastRadiusAnalysis,
    DNASafetyManager,
    validate_safety_dna,
    generate_safety_report,
)


class TestBlastRadiusScope:
    """BlastRadiusScope Enum 테스트"""
    
    def test_scope_values(self):
        """범위 값 확인"""
        assert BlastRadiusScope.ISOLATED.value == "isolated"
        assert BlastRadiusScope.LIMITED.value == "limited"
        assert BlastRadiusScope.MODERATE.value == "moderate"
        assert BlastRadiusScope.EXTENSIVE.value == "extensive"
        assert BlastRadiusScope.CRITICAL.value == "critical"


class TestBlastRadiusAnalysis:
    """BlastRadiusAnalysis 데이터클래스 테스트"""
    
    def test_analysis_creation(self):
        """분석 결과 생성"""
        analysis = BlastRadiusAnalysis(
            failed_service="payment",
            scope=BlastRadiusScope.LIMITED,
            affected_services=["payment", "order"],
            affected_users_percentage=40.0,
            affected_revenue_percentage=60.0,
            isolation_strategy="bulkhead",
            containment_actions=["Action 1", "Action 2"],
            estimated_recovery_time_minutes=15,
        )
        
        assert analysis.failed_service == "payment"
        assert analysis.scope == BlastRadiusScope.LIMITED
        assert len(analysis.affected_services) == 2
    
    def test_analysis_to_dict(self):
        """분석 결과 딕셔너리 변환"""
        analysis = BlastRadiusAnalysis(
            failed_service="user",
            scope=BlastRadiusScope.ISOLATED,
            affected_services=["user"],
            affected_users_percentage=10.0,
            affected_revenue_percentage=5.0,
            isolation_strategy="circuit_breaker",
            containment_actions=[],
            estimated_recovery_time_minutes=5,
        )
        
        result = analysis.to_dict()
        
        assert result["failed_service"] == "user"
        assert result["scope"] == "isolated"
        assert result["estimated_recovery_time_minutes"] == 5


class TestBlastRadiusControllerInit:
    """BlastRadiusController 초기화 테스트"""
    
    def test_default_config(self):
        """기본 설정으로 초기화"""
        controller = BlastRadiusController({})
        
        assert controller.max_affected == 3
        assert controller.auto_isolate is True
        assert len(controller.service_weights) > 0
    
    def test_custom_config(self):
        """커스텀 설정으로 초기화"""
        config = {
            "max_affected_services": 5,
            "auto_isolate": False,
            "service_weights": {
                "custom_service": {"users": 0.5, "revenue": 0.3, "tier": "medium"},
            },
        }
        
        controller = BlastRadiusController(config)
        
        assert controller.max_affected == 5
        assert controller.auto_isolate is False
        assert "custom_service" in controller.service_weights
    
    def test_default_service_weights(self):
        """기본 서비스 가중치"""
        controller = BlastRadiusController({})
        
        assert controller.service_weights["payment"]["tier"] == "critical"
        assert controller.service_weights["user"]["users"] == 1.0


class TestBlastRadiusControllerAnalysis:
    """BlastRadiusController 분석 테스트"""
    
    def test_analyze_isolated_service(self):
        """격리된 서비스 분석"""
        controller = BlastRadiusController({
            "dependencies": {
                "logging": [],
            },
        })
        
        analysis = controller.analyze_blast_radius("logging")
        
        assert analysis.scope == BlastRadiusScope.ISOLATED
        assert len(analysis.affected_services) == 1
        assert analysis.isolation_strategy == "circuit_breaker"
    
    def test_analyze_payment_service(self):
        """결제 서비스 장애 분석"""
        controller = BlastRadiusController({
            "dependencies": {
                "order": ["payment", "inventory"],
                "checkout": ["payment", "cart"],
            },
        })
        
        analysis = controller.analyze_blast_radius("payment")
        
        # payment에 의존하는 서비스들이 영향 받음
        assert "payment" in analysis.affected_services
        assert "order" in analysis.affected_services
        assert "checkout" in analysis.affected_services
    
    def test_containment_actions_generated(self):
        """억제 조치 생성"""
        controller = BlastRadiusController({
            "dependencies": {"order": ["payment"]},
        })
        
        analysis = controller.analyze_blast_radius("payment")
        
        assert len(analysis.containment_actions) > 0
    
    def test_recovery_time_estimation(self):
        """복구 시간 추정"""
        controller = BlastRadiusController({
            "dependencies": {},
        })
        
        analysis = controller.analyze_blast_radius("notification")
        
        # 최소 5분 (ISOLATED)
        assert analysis.estimated_recovery_time_minutes >= 5


class TestBlastRadiusControllerScope:
    """BlastRadiusController 범위 결정 테스트"""
    
    def test_isolated_scope(self):
        """ISOLATED 범위"""
        controller = BlastRadiusController({})
        
        scope = controller._determine_scope(
            affected_count=1,
            users_impact=0.1,
            revenue_impact=0.1,
        )
        
        assert scope == BlastRadiusScope.ISOLATED
    
    def test_limited_scope(self):
        """LIMITED 범위"""
        controller = BlastRadiusController({})
        
        scope = controller._determine_scope(
            affected_count=2,
            users_impact=0.3,
            revenue_impact=0.3,
        )
        
        assert scope == BlastRadiusScope.LIMITED
    
    def test_moderate_scope(self):
        """MODERATE 범위"""
        controller = BlastRadiusController({})
        
        scope = controller._determine_scope(
            affected_count=4,
            users_impact=0.5,
            revenue_impact=0.5,
        )
        
        assert scope == BlastRadiusScope.MODERATE
    
    def test_extensive_scope(self):
        """EXTENSIVE 범위"""
        controller = BlastRadiusController({})
        
        scope = controller._determine_scope(
            affected_count=6,
            users_impact=0.7,
            revenue_impact=0.7,
        )
        
        assert scope == BlastRadiusScope.EXTENSIVE
    
    def test_critical_scope(self):
        """CRITICAL 범위"""
        controller = BlastRadiusController({})
        
        scope = controller._determine_scope(
            affected_count=10,
            users_impact=0.9,
            revenue_impact=0.9,
        )
        
        assert scope == BlastRadiusScope.CRITICAL


class TestBlastRadiusControllerIsolation:
    """BlastRadiusController 격리 테스트"""
    
    def test_should_auto_isolate_true(self):
        """자동 격리 활성화"""
        controller = BlastRadiusController({
            "auto_isolate": True,
            "max_affected_services": 3,
        })
        
        analysis = BlastRadiusAnalysis(
            failed_service="notification",
            scope=BlastRadiusScope.ISOLATED,
            affected_services=["notification"],
            affected_users_percentage=5.0,
            affected_revenue_percentage=1.0,
            isolation_strategy="circuit_breaker",
            containment_actions=[],
            estimated_recovery_time_minutes=5,
        )
        
        assert controller.should_auto_isolate(analysis) is True
    
    def test_should_auto_isolate_false_disabled(self):
        """자동 격리 비활성화"""
        controller = BlastRadiusController({
            "auto_isolate": False,
        })
        
        analysis = BlastRadiusAnalysis(
            failed_service="notification",
            scope=BlastRadiusScope.ISOLATED,
            affected_services=["notification"],
            affected_users_percentage=5.0,
            affected_revenue_percentage=1.0,
            isolation_strategy="circuit_breaker",
            containment_actions=[],
            estimated_recovery_time_minutes=5,
        )
        
        assert controller.should_auto_isolate(analysis) is False
    
    def test_should_auto_isolate_false_too_many_affected(self):
        """영향 서비스 초과 시 자동 격리 안함"""
        controller = BlastRadiusController({
            "auto_isolate": True,
            "max_affected_services": 2,
        })
        
        analysis = BlastRadiusAnalysis(
            failed_service="payment",
            scope=BlastRadiusScope.EXTENSIVE,
            affected_services=["payment", "order", "checkout", "inventory"],
            affected_users_percentage=70.0,
            affected_revenue_percentage=80.0,
            isolation_strategy="emergency_mode",
            containment_actions=[],
            estimated_recovery_time_minutes=60,
        )
        
        assert controller.should_auto_isolate(analysis) is False
    
    def test_execute_isolation_success(self):
        """격리 실행 성공"""
        controller = BlastRadiusController({
            "auto_isolate": True,
            "max_affected_services": 3,
        })
        
        analysis = BlastRadiusAnalysis(
            failed_service="notification",
            scope=BlastRadiusScope.ISOLATED,
            affected_services=["notification"],
            affected_users_percentage=5.0,
            affected_revenue_percentage=1.0,
            isolation_strategy="circuit_breaker",
            containment_actions=["Circuit Breaker 활성화: notification"],
            estimated_recovery_time_minutes=5,
        )
        
        result = controller.execute_isolation(analysis)
        
        assert result["executed"] is True
        assert len(result["actions_taken"]) > 0
        assert len(result["errors"]) == 0
    
    def test_execute_isolation_with_custom_fn(self):
        """커스텀 격리 함수"""
        controller = BlastRadiusController({
            "auto_isolate": True,
            "max_affected_services": 3,
        })
        
        executed_actions = []
        
        def isolation_fn(service, action):
            executed_actions.append((service, action))
            return True
        
        analysis = BlastRadiusAnalysis(
            failed_service="cache",
            scope=BlastRadiusScope.ISOLATED,
            affected_services=["cache"],
            affected_users_percentage=5.0,
            affected_revenue_percentage=1.0,
            isolation_strategy="circuit_breaker",
            containment_actions=["Action 1", "Action 2"],
            estimated_recovery_time_minutes=5,
        )
        
        controller.execute_isolation(analysis, isolation_fn=isolation_fn)
        
        assert len(executed_actions) == 2


class TestBlastRadiusControllerSummary:
    """BlastRadiusController 요약 테스트"""
    
    def test_get_isolation_summary(self):
        """격리 설정 요약"""
        controller = BlastRadiusController({
            "auto_isolate": True,
            "max_affected_services": 5,
        })
        
        summary = controller.get_isolation_summary()
        
        assert summary["auto_isolate"] is True
        assert summary["max_affected_services"] == 5
        assert "service_weights" in summary
        assert "dependencies" in summary


class TestDNASafetyManager:
    """DNASafetyManager 통합 테스트"""
    
    def test_manager_init(self):
        """매니저 초기화"""
        stage_dna = {
            "name": "Test Stage",
            "rollback": {"strategy": "automatic"},
            "blast_radius": {"auto_isolate": True},
        }
        
        manager = DNASafetyManager(stage_dna)
        
        assert manager.stage_name == "Test Stage"
        assert manager.rollback is not None
        assert manager.blast_radius is not None
    
    def test_manager_take_snapshot(self):
        """매니저 스냅샷 생성"""
        stage_dna = {
            "name": "Test Stage",
            "rollback": {"snapshot": {"enabled": True}},
        }
        
        manager = DNASafetyManager(stage_dna)
        snapshot = manager.take_snapshot({"config": {"key": "value"}})
        
        assert snapshot is not None
    
    def test_manager_analyze_failure(self):
        """매니저 장애 분석"""
        stage_dna = {
            "name": "Test Stage",
            "rollback": {
                "strategy": "automatic",
                "triggers": {"error_rate_threshold": 0.1},
            },
            "blast_radius": {
                "dependencies": {"order": ["payment"]},
            },
        }
        
        manager = DNASafetyManager(stage_dna)
        
        analysis = manager.analyze_failure(
            failed_service="payment",
            metrics={"error_rate": 0.15},
        )
        
        assert analysis["failed_service"] == "payment"
        assert analysis["should_rollback"] is True
        assert "blast_radius" in analysis
    
    def test_manager_get_summary(self):
        """매니저 요약"""
        stage_dna = {
            "name": "Summary Test Stage",
            "rollback": {"strategy": "hybrid"},
            "blast_radius": {"auto_isolate": False},
        }
        
        manager = DNASafetyManager(stage_dna)
        summary = manager.get_summary()
        
        assert summary["stage_name"] == "Summary Test Stage"
        assert summary["rollback"]["strategy"] == "hybrid"
        assert summary["blast_radius"]["auto_isolate"] is False


class TestValidateSafetyDNA:
    """validate_safety_dna 함수 테스트"""
    
    def test_valid_config(self):
        """유효한 설정"""
        config = {
            "rollback": {
                "strategy": "automatic",
                "triggers": {"error_rate_threshold": 0.1},
            },
            "blast_radius": {
                "max_affected_services": 3,
            },
        }
        
        warnings = validate_safety_dna(config)
        
        assert len(warnings) == 0
    
    def test_invalid_strategy(self):
        """잘못된 전략"""
        config = {
            "rollback": {
                "strategy": "invalid_strategy",
            },
        }
        
        warnings = validate_safety_dna(config)
        
        assert any("Invalid rollback strategy" in w for w in warnings)
    
    def test_automatic_without_triggers(self):
        """자동 전략인데 트리거 없음"""
        config = {
            "rollback": {
                "strategy": "automatic",
                # triggers 없음
            },
        }
        
        warnings = validate_safety_dna(config)
        
        assert any("requires trigger conditions" in w for w in warnings)
    
    def test_invalid_max_affected(self):
        """잘못된 max_affected_services"""
        config = {
            "blast_radius": {
                "max_affected_services": 0,  # 최소 1 이상
            },
        }
        
        warnings = validate_safety_dna(config)
        
        assert any("max_affected_services" in w for w in warnings)


class TestGenerateSafetyReport:
    """generate_safety_report 함수 테스트"""
    
    def test_generate_report(self):
        """리포트 생성"""
        stage_dna = {
            "name": "Report Test Stage",
            "rollback": {"strategy": "automatic"},
            "blast_radius": {"auto_isolate": True},
        }
        
        manager = DNASafetyManager(stage_dna)
        report = generate_safety_report(manager)
        
        assert "## 🛡️ DNA Safety Report" in report
        assert "Report Test Stage" in report
        assert "Rollback Status" in report
        assert "Blast Radius Settings" in report

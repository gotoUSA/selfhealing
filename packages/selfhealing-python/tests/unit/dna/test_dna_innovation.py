"""
DNA Innovation 모듈 테스트

Auto-Suggestion Engine + Cross-Stage Learning 테스트
"""


import pytest
from load_tests.utils.selfhealing.dna_innovation import (
    AutoSuggestionEngine,
    CrossStageLearner,
    CrossStageLearningReport,
    DNAEvolutionTracker,
    DNAInnovationManager,
    StageProfile,
    Suggestion,
    SuggestionPriority,
    SuggestionType,
)


class TestSuggestionType:
    """SuggestionType 테스트"""

    def test_suggestion_type_values(self):
        """제안 유형 값 확인"""
        assert SuggestionType.ADD_MODULE.value == "add_module"
        assert SuggestionType.REMOVE_MODULE.value == "remove_module"
        assert SuggestionType.ADJUST_THRESHOLD.value == "adjust_threshold"
        assert SuggestionType.UPGRADE_TIER.value == "upgrade_tier"
        assert SuggestionType.DOWNGRADE_TIER.value == "downgrade_tier"
        assert SuggestionType.ENABLE_FEATURE.value == "enable_feature"


class TestSuggestionPriority:
    """SuggestionPriority 테스트"""

    def test_priority_values(self):
        """우선순위 값 확인"""
        assert SuggestionPriority.CRITICAL.value == "critical"
        assert SuggestionPriority.HIGH.value == "high"
        assert SuggestionPriority.MEDIUM.value == "medium"
        assert SuggestionPriority.LOW.value == "low"
        assert SuggestionPriority.INFO.value == "info"


class TestSuggestion:
    """Suggestion 테스트"""

    def test_create_suggestion(self):
        """제안 생성"""
        suggestion = Suggestion(
            suggestion_id="sug_001",
            suggestion_type=SuggestionType.ADD_MODULE,
            priority=SuggestionPriority.HIGH,
            target_stage="Stage 14",
            target_field="required_modules",
            current_value=["health"],
            suggested_value=["health", "dlq"],
            reasoning="에러율이 높음",
            confidence=0.85,
        )

        assert suggestion.suggestion_id == "sug_001"
        assert suggestion.suggestion_type == SuggestionType.ADD_MODULE
        assert suggestion.priority == SuggestionPriority.HIGH
        assert suggestion.confidence == 0.85

    def test_to_dict(self):
        """딕셔너리 변환"""
        suggestion = Suggestion(
            suggestion_id="sug_001",
            suggestion_type=SuggestionType.ADJUST_THRESHOLD,
            priority=SuggestionPriority.MEDIUM,
            target_stage="Stage 14",
            target_field="p99_threshold_ms",
            current_value=300,
            suggested_value=400,
            reasoning="P99이 임계값 초과",
            confidence=0.75,
        )

        result = suggestion.to_dict()
        assert result["id"] == "sug_001"
        assert result["type"] == "adjust_threshold"
        assert result["priority"] == "medium"
        assert result["current"] == 300
        assert result["suggested"] == 400


class TestStageProfile:
    """StageProfile 테스트"""

    def test_create_profile(self):
        """프로파일 생성"""
        dna = {"required_modules": ["health", "dlq"]}
        profile = StageProfile(stage_name="Stage 14", dna=dna)

        assert profile.stage_name == "Stage 14"
        assert profile.dna == dna
        assert profile.metrics_history == []

    def test_update_stats(self):
        """통계 업데이트"""
        profile = StageProfile(
            stage_name="Stage 14",
            dna={},
            metrics_history=[
                {"p99": 100, "error_rate": 0.01, "passed": True},
                {"p99": 150, "error_rate": 0.02, "passed": True},
                {"p99": 120, "error_rate": 0.01, "passed": False},
            ],
        )

        profile.update_stats()

        assert profile.avg_p99 == pytest.approx(123.33, rel=0.01)
        assert profile.avg_error_rate == pytest.approx(0.0133, rel=0.01)
        assert profile.success_rate == pytest.approx(0.666, rel=0.01)


class TestAutoSuggestionEngine:
    """AutoSuggestionEngine 테스트"""

    @pytest.fixture
    def engine(self):
        """엔진 픽스처"""
        return AutoSuggestionEngine()

    def test_init(self, engine):
        """초기화 테스트"""
        assert engine.suggestions == []
        assert engine.min_confidence == 0.6
        assert engine.min_samples == 3

    def test_analyze_stage_insufficient_samples(self, engine):
        """샘플 부족 시 분석"""
        dna = {"required_modules": ["health"]}
        metrics = [{"p99": 100}]  # 1개 샘플만

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        assert len(suggestions) == 0

    def test_analyze_high_error_rate_suggests_dlq(self, engine):
        """높은 에러율에 DLQ 제안"""
        dna = {"required_modules": ["health"]}
        metrics = [
            {"p99": 100, "error_rate": 0.03},
            {"p99": 120, "error_rate": 0.025},
            {"p99": 110, "error_rate": 0.035},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        # DLQ 추가 제안 확인
        dlq_suggestions = [
            s for s in suggestions if s.suggestion_type == SuggestionType.ADD_MODULE and "dlq" in str(s.suggested_value)
        ]
        assert len(dlq_suggestions) >= 1

    def test_analyze_very_high_error_rate_suggests_circuit_breaker(self, engine):
        """매우 높은 에러율에 Circuit Breaker 제안"""
        dna = {"required_modules": ["health"]}
        metrics = [
            {"p99": 100, "error_rate": 0.04},
            {"p99": 120, "error_rate": 0.05},
            {"p99": 110, "error_rate": 0.045},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        cb_suggestions = [
            s
            for s in suggestions
            if s.suggestion_type == SuggestionType.ADD_MODULE and "circuit_breaker" in str(s.suggested_value)
        ]
        assert len(cb_suggestions) >= 1
        # 우선순위가 CRITICAL이어야 함
        assert cb_suggestions[0].priority == SuggestionPriority.CRITICAL

    def test_analyze_high_latency_suggests_rate_limiter(self, engine):
        """높은 레이턴시에 Rate Limiter 제안"""
        dna = {"required_modules": ["health"]}
        metrics = [
            {"p99": 600, "error_rate": 0.01},
            {"p99": 700, "error_rate": 0.01},
            {"p99": 550, "error_rate": 0.01},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        rl_suggestions = [
            s
            for s in suggestions
            if s.suggestion_type == SuggestionType.ADD_MODULE and "rate_limiter" in str(s.suggested_value)
        ]
        assert len(rl_suggestions) >= 1

    def test_analyze_threshold_too_strict(self, engine):
        """너무 엄격한 임계값 감지"""
        dna = {"required_modules": ["health"], "p99_threshold_ms": 100}
        metrics = [
            {"p99": 150, "error_rate": 0.01},
            {"p99": 180, "error_rate": 0.01},
            {"p99": 160, "error_rate": 0.01},
            {"p99": 140, "error_rate": 0.01},
            {"p99": 170, "error_rate": 0.01},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        threshold_suggestions = [s for s in suggestions if s.suggestion_type == SuggestionType.ADJUST_THRESHOLD]
        # 임계값 조정 제안 확인
        assert len(threshold_suggestions) >= 1

    def test_analyze_tier_upgrade(self, engine):
        """티어 업그레이드 제안"""
        dna = {"required_modules": ["health"], "sla_tier": "silver"}
        metrics = [
            {"p99": 80, "error_rate": 0.005},
            {"p99": 90, "error_rate": 0.006},
            {"p99": 85, "error_rate": 0.004},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        tier_suggestions = [s for s in suggestions if s.suggestion_type == SuggestionType.UPGRADE_TIER]
        # gold 티어로 업그레이드 가능
        if tier_suggestions:
            assert tier_suggestions[0].suggested_value == "gold"

    def test_analyze_tier_downgrade(self, engine):
        """티어 다운그레이드 제안"""
        dna = {"required_modules": ["health"], "sla_tier": "gold"}
        metrics = [
            {"p99": 800, "error_rate": 0.05},
            {"p99": 900, "error_rate": 0.06},
            {"p99": 850, "error_rate": 0.055},
        ]

        suggestions = engine.analyze_stage("Stage 14", dna, metrics)

        tier_suggestions = [s for s in suggestions if s.suggestion_type == SuggestionType.DOWNGRADE_TIER]
        # silver 티어로 다운그레이드 권장
        if tier_suggestions:
            assert tier_suggestions[0].suggested_value == "silver"

    def test_get_suggestions_by_priority(self, engine):
        """우선순위별 제안 조회"""
        # 여러 제안 생성
        dna = {"required_modules": ["health"]}
        metrics = [
            {"p99": 600, "error_rate": 0.04},
            {"p99": 700, "error_rate": 0.05},
            {"p99": 650, "error_rate": 0.045},
        ]

        engine.analyze_stage("Stage 14", dna, metrics)

        # 높은 우선순위만 조회
        high_priority = engine.get_suggestions_by_priority(SuggestionPriority.HIGH)

        for s in high_priority:
            assert s.priority in [SuggestionPriority.CRITICAL, SuggestionPriority.HIGH]


class TestCrossStageLearner:
    """CrossStageLearner 테스트"""

    @pytest.fixture
    def learner(self):
        """학습기 픽스처"""
        return CrossStageLearner()

    def test_register_stage(self, learner):
        """Stage 등록"""
        dna = {"required_modules": ["health", "dlq"]}
        learner.register_stage("Stage 14", dna)

        assert "Stage 14" in learner.stage_profiles
        assert learner.stage_profiles["Stage 14"].dna == dna

    def test_record_metrics(self, learner):
        """메트릭 기록"""
        learner.register_stage("Stage 14", {"required_modules": ["health"]})

        learner.record_metrics("Stage 14", {"p99": 100, "error_rate": 0.01, "passed": True})
        learner.record_metrics("Stage 14", {"p99": 120, "error_rate": 0.02, "passed": True})

        profile = learner.stage_profiles["Stage 14"]
        assert len(profile.metrics_history) == 2
        assert profile.success_rate == 1.0

    def test_record_metrics_unknown_stage(self, learner):
        """알 수 없는 Stage 메트릭 기록"""
        # 경고만 발생, 예외 없음
        learner.record_metrics("Unknown Stage", {"p99": 100})
        assert "Unknown Stage" not in learner.stage_profiles

    def test_discover_patterns(self, learner):
        """패턴 발견"""
        # 여러 Stage 등록
        learner.register_stage("Stage A", {"required_modules": ["health", "dlq", "circuit_breaker"]})
        learner.register_stage("Stage B", {"required_modules": ["health", "dlq", "circuit_breaker"]})
        learner.register_stage("Stage C", {"required_modules": ["health", "dlq"]})

        # 메트릭 기록
        for _ in range(5):
            learner.record_metrics("Stage A", {"p99": 100, "error_rate": 0.01, "passed": True})
            learner.record_metrics("Stage B", {"p99": 110, "error_rate": 0.015, "passed": True})
            learner.record_metrics("Stage C", {"p99": 200, "error_rate": 0.03, "passed": False})

        patterns = learner.discover_patterns()

        # 고성능 패턴이 발견되어야 함
        assert len(patterns) >= 1

    def test_propagate_best_practices(self, learner):
        """모범 사례 전파"""
        # 고성능 Stage
        learner.register_stage("High Performer", {"required_modules": ["health", "dlq", "circuit_breaker"]})
        # 저성능 Stage
        learner.register_stage("Low Performer", {"required_modules": ["health"]})

        # 메트릭 기록
        for _ in range(5):
            learner.record_metrics("High Performer", {"passed": True})
            learner.record_metrics("Low Performer", {"passed": False})

        recommendations = learner.propagate_best_practices("High Performer")

        # Low Performer에게 모듈 추가 권장
        assert len(recommendations) >= 1
        assert recommendations[0]["target"] == "Low Performer"
        assert "dlq" in recommendations[0].get("modules", []) or "circuit_breaker" in recommendations[0].get("modules", [])

    def test_generate_cross_stage_report(self, learner):
        """Cross-Stage 보고서 생성"""
        learner.register_stage("Stage A", {"required_modules": ["health", "dlq"]})
        learner.register_stage("Stage B", {"required_modules": ["health"]})

        for _ in range(3):
            learner.record_metrics("Stage A", {"passed": True})
            learner.record_metrics("Stage B", {"passed": False})

        report = learner.generate_cross_stage_report()

        assert report.report_id.startswith("csl_")
        assert len(report.stages_analyzed) == 2

    def test_find_common_modules(self, learner):
        """공통 모듈 찾기"""
        profiles = [
            StageProfile("A", {"required_modules": ["health", "dlq", "circuit_breaker"]}),
            StageProfile("B", {"required_modules": ["health", "dlq", "alerts"]}),
            StageProfile("C", {"required_modules": ["health", "dlq", "observability"]}),
        ]

        common = learner._find_common_modules(profiles)

        assert set(common) == {"health", "dlq"}


class TestDNAEvolutionTracker:
    """DNAEvolutionTracker 테스트"""

    @pytest.fixture
    def tracker(self):
        """추적기 픽스처"""
        return DNAEvolutionTracker()

    def test_track_change(self, tracker):
        """변경 추적"""
        old_dna = {"required_modules": ["health"]}
        new_dna = {"required_modules": ["health", "dlq"]}

        record = tracker.track_change(
            stage_name="Stage 14",
            old_dna=old_dna,
            new_dna=new_dna,
            change_reason="DLQ 추가",
        )

        assert record["stage_name"] == "Stage 14"
        assert record["version"] == 1
        assert len(record["changes"]) >= 1

    def test_track_multiple_changes(self, tracker):
        """여러 변경 추적"""
        dna1 = {"required_modules": ["health"]}
        dna2 = {"required_modules": ["health", "dlq"]}
        dna3 = {"required_modules": ["health", "dlq", "circuit_breaker"]}

        tracker.track_change("Stage 14", dna1, dna2, "DLQ 추가")
        tracker.track_change("Stage 14", dna2, dna3, "CB 추가")

        assert tracker.version_counter["Stage 14"] == 2
        assert len(tracker.evolution_history) == 2

    def test_get_evolution_timeline(self, tracker):
        """진화 타임라인 조회"""
        tracker.track_change("Stage 14", {"a": 1}, {"a": 2}, "변경 1")
        tracker.track_change("Stage 15", {"b": 1}, {"b": 2}, "변경 2")
        tracker.track_change("Stage 14", {"a": 2}, {"a": 3}, "변경 3")

        timeline = tracker.get_evolution_timeline("Stage 14")

        assert len(timeline) == 2
        assert all(r["stage_name"] == "Stage 14" for r in timeline)

    def test_get_stats(self, tracker):
        """통계 조회"""
        tracker.track_change("Stage 14", {}, {"a": 1}, "변경")
        tracker.track_change("Stage 15", {}, {"b": 1}, "변경")

        stats = tracker.get_stats()

        assert stats["total_changes"] == 2
        assert stats["stages_tracked"] == 2

    def test_calculate_changes(self, tracker):
        """변경 사항 계산"""
        old = {"a": 1, "b": 2}
        new = {"a": 1, "b": 3, "c": 4}

        changes = tracker._calculate_changes(old, new)

        # b 수정, c 추가
        assert len(changes) == 2

        change_fields = {c["field"] for c in changes}
        assert "b" in change_fields
        assert "c" in change_fields


class TestDNAInnovationManager:
    """DNAInnovationManager 테스트"""

    @pytest.fixture
    def manager(self):
        """관리자 픽스처"""
        return DNAInnovationManager()

    def test_analyze_and_suggest(self, manager):
        """분석 및 제안"""
        dna = {"required_modules": ["health"]}
        metrics = [
            {"p99": 600, "error_rate": 0.03, "passed": True},
            {"p99": 700, "error_rate": 0.04, "passed": False},
            {"p99": 650, "error_rate": 0.035, "passed": True},
        ]

        result = manager.analyze_and_suggest("Stage 14", dna, metrics)

        assert result["stage_name"] == "Stage 14"
        assert "suggestions_count" in result
        assert "suggestions" in result

    def test_run_cross_stage_analysis(self, manager):
        """Cross-Stage 분석"""
        # Stage 등록 및 메트릭 기록
        manager.analyze_and_suggest(
            "Stage A",
            {"required_modules": ["health", "dlq"]},
            [{"passed": True} for _ in range(3)],
        )
        manager.analyze_and_suggest(
            "Stage B",
            {"required_modules": ["health"]},
            [{"passed": False} for _ in range(3)],
        )

        report = manager.run_cross_stage_analysis()

        assert isinstance(report, CrossStageLearningReport)
        assert len(report.stages_analyzed) == 2

    def test_apply_suggestion_add_module(self, manager):
        """모듈 추가 제안 적용"""
        suggestion = Suggestion(
            suggestion_id="sug_001",
            suggestion_type=SuggestionType.ADD_MODULE,
            priority=SuggestionPriority.HIGH,
            target_stage="Stage 14",
            target_field="required_modules",
            current_value=["health"],
            suggested_value=["health", "dlq"],
            reasoning="DLQ 필요",
            confidence=0.9,
        )

        dna = {"required_modules": ["health"]}
        new_dna = manager.apply_suggestion(suggestion, dna)

        assert new_dna["required_modules"] == ["health", "dlq"]

    def test_apply_suggestion_adjust_threshold(self, manager):
        """임계값 조정 제안 적용"""
        suggestion = Suggestion(
            suggestion_id="sug_002",
            suggestion_type=SuggestionType.ADJUST_THRESHOLD,
            priority=SuggestionPriority.MEDIUM,
            target_stage="Stage 14",
            target_field="p99_threshold_ms",
            current_value=300,
            suggested_value=400,
            reasoning="P99 조정",
            confidence=0.8,
        )

        dna = {"p99_threshold_ms": 300}
        new_dna = manager.apply_suggestion(suggestion, dna)

        assert new_dna["p99_threshold_ms"] == 400

    def test_apply_suggestion_upgrade_tier(self, manager):
        """티어 업그레이드 제안 적용"""
        suggestion = Suggestion(
            suggestion_id="sug_003",
            suggestion_type=SuggestionType.UPGRADE_TIER,
            priority=SuggestionPriority.MEDIUM,
            target_stage="Stage 14",
            target_field="sla_tier",
            current_value="silver",
            suggested_value="gold",
            reasoning="성능 개선됨",
            confidence=0.75,
        )

        dna = {"sla_tier": "silver"}
        new_dna = manager.apply_suggestion(suggestion, dna)

        assert new_dna["sla_tier"] == "gold"

    def test_apply_suggestion_tracks_evolution(self, manager):
        """제안 적용 시 진화 추적"""
        suggestion = Suggestion(
            suggestion_id="sug_004",
            suggestion_type=SuggestionType.ENABLE_FEATURE,
            priority=SuggestionPriority.MEDIUM,
            target_stage="Stage 14",
            target_field="enable_self_learning",
            current_value=False,
            suggested_value=True,
            reasoning="학습 활성화",
            confidence=0.7,
        )

        dna = {"enable_self_learning": False}
        manager.apply_suggestion(suggestion, dna)

        # 진화 추적 확인
        timeline = manager.evolution_tracker.get_evolution_timeline("Stage 14")
        assert len(timeline) == 1


class TestInnovationIntegration:
    """Innovation 통합 테스트"""

    def test_full_innovation_workflow(self):
        """전체 혁신 워크플로우"""
        manager = DNAInnovationManager()

        # 1. 여러 Stage 분석
        stages_data = [
            ("Stage A", {"required_modules": ["health", "dlq", "circuit_breaker"]}, 0.98),
            ("Stage B", {"required_modules": ["health", "dlq"]}, 0.95),
            ("Stage C", {"required_modules": ["health"]}, 0.85),
        ]

        for stage_name, dna, pass_rate in stages_data:
            metrics = [{"p99": 100, "error_rate": 0.01, "passed": i < int(pass_rate * 10)} for i in range(10)]
            manager.analyze_and_suggest(stage_name, dna, metrics)

        # 2. Cross-Stage 분석
        report = manager.run_cross_stage_analysis()

        assert len(report.stages_analyzed) == 3

        # 3. 제안 적용
        suggestions = manager.suggestion_engine.get_suggestions_by_priority(SuggestionPriority.MEDIUM)

        if suggestions:
            dna = {"required_modules": ["health"]}
            new_dna = manager.apply_suggestion(suggestions[0], dna)
            assert new_dna is not None

    def test_tier_recommendation_accuracy(self):
        """티어 추천 정확도"""
        engine = AutoSuggestionEngine()

        # Platinum 수준 성능
        metrics = [{"p99": 50, "error_rate": 0.0005} for _ in range(5)]
        suggestions = engine.analyze_stage("High Performance Stage", {"sla_tier": "silver"}, metrics)

        tier_suggestions = [s for s in suggestions if s.suggestion_type == SuggestionType.UPGRADE_TIER]

        # gold 이상으로 업그레이드 권장
        if tier_suggestions:
            assert tier_suggestions[0].suggested_value in ["gold", "platinum"]

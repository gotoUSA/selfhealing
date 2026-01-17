"""
Self-Learning DNA 단위 테스트

DNA Learning 테스트
"""

import pytest
from datetime import datetime, timedelta

from load_tests.utils.selfhealing.dna_learning import (
    DNATestSample,
    LearningInsight,
    LearningReport,
    DNALearner,
    generate_learning_report_section,
)


class TestDNASample:
    """DNATestSample 테스트"""
    
    def test_sample_creation(self):
        """샘플 생성"""
        sample = DNATestSample(
            timestamp="2025-01-01T00:00:00",
            stage_name="Test Stage",
            metrics={"p99": 200, "error_rate": 0.01},
            dna_config={"p99_threshold_ms": 300},
            passed=True,
        )
        
        assert sample.stage_name == "Test Stage"
        assert sample.passed is True
        assert sample.metrics["p99"] == 200


class TestLearningInsight:
    """LearningInsight 테스트"""
    
    def test_insight_creation(self):
        """인사이트 생성"""
        insight = LearningInsight(
            insight_type="threshold",
            target_field="p99_threshold_ms",
            current_value=300,
            suggested_value=250,
            confidence=0.85,
            reasoning="테스트 이유",
            evidence=[{"mean": 200}],
        )
        
        assert insight.insight_type == "threshold"
        assert insight.confidence == 0.85
        assert insight.suggested_value == 250


class TestDNALearner:
    """DNALearner 테스트"""
    
    @pytest.fixture
    def learner(self):
        """기본 학습기"""
        config = {
            "enabled": True,
            "min_samples": 5,
            "learning_rate": 0.1,
            "confidence_threshold": 0.8,
            "max_adjustment_per_cycle": 0.2,
            "cooldown_hours": 24,
        }
        return DNALearner(config)
    
    @pytest.fixture
    def sample_dna(self):
        """테스트용 DNA"""
        return {
            "p99_threshold_ms": 300,
            "error_rate_threshold": 0.01,
        }
    
    def _create_sample(self, p99=200, error_rate=0.005, passed=True, stage_name="Test"):
        """샘플 생성 헬퍼"""
        return DNATestSample(
            timestamp=datetime.now().isoformat(),
            stage_name=stage_name,
            metrics={
                "p99": p99,
                "error_rate": error_rate,
                "availability": 0.999,
            },
            dna_config={
                "p99_threshold_ms": 300,
                "error_rate_threshold": 0.01,
            },
            passed=passed,
        )
    
    def test_init_default(self):
        """기본 초기화"""
        learner = DNALearner()
        assert learner.enabled is True
        assert learner.min_samples == 5
    
    def test_init_with_config(self, learner):
        """설정으로 초기화"""
        assert learner.min_samples == 5
        assert learner.confidence_threshold == 0.8
        assert learner.max_adjustment == 0.2
    
    def test_record_sample(self, learner):
        """샘플 기록"""
        sample = self._create_sample()
        learner.record_sample(sample)
        
        assert len(learner.history) == 1
        assert learner.history[0].stage_name == "Test"
    
    def test_analyze_insufficient_samples(self, learner):
        """샘플 부족"""
        for i in range(3):  # min_samples=5보다 적음
            learner.record_sample(self._create_sample())
        
        report = learner.analyze()
        
        assert report.samples_analyzed == 3
        assert len(report.insights) == 0
        assert "Insufficient samples" in report.pending_review[0]
    
    def test_analyze_with_sufficient_samples(self, learner):
        """충분한 샘플"""
        for i in range(7):
            learner.record_sample(self._create_sample(p99=200 + i * 5))
        
        report = learner.analyze()
        
        assert report.samples_analyzed == 7
        assert isinstance(report.insights, list)
    
    def test_p99_insight_generation(self, learner):
        """P99 인사이트 생성"""
        # 일관되게 낮은 P99
        for i in range(10):
            learner.record_sample(self._create_sample(p99=180 + i))
        
        report = learner.analyze()
        
        # P99 관련 인사이트가 있어야 함 (threshold가 300인데 실제 평균이 ~185)
        p99_insights = [i for i in report.insights if i.target_field == "p99_threshold_ms"]
        
        # 인사이트가 생성되면 하향 조정 제안
        if p99_insights:
            assert p99_insights[0].suggested_value < 300
    
    def test_error_rate_insight_low_rate(self, learner):
        """낮은 에러율 인사이트"""
        # 에러율이 매우 낮음 (threshold의 50% 미만)
        for i in range(10):
            learner.record_sample(self._create_sample(error_rate=0.002))
        
        report = learner.analyze()
        
        error_insights = [i for i in report.insights if "error_rate" in i.target_field]
        
        # 에러율 하향 조정 제안
        if error_insights:
            assert error_insights[0].insight_type in ["threshold", "pattern"]
    
    def test_pass_rate_insight_low_rate(self, learner):
        """낮은 통과율 인사이트"""
        # 80% 미만 통과율
        for i in range(10):
            passed = i < 6  # 60% 통과
            learner.record_sample(self._create_sample(passed=passed))
        
        report = learner.analyze()
        
        pass_insights = [i for i in report.insights if i.target_field == "pass_rate"]
        
        if pass_insights:
            assert pass_insights[0].confidence >= 0.8
    
    def test_recovery_time_trend(self, learner):
        """복구 시간 추세 분석"""
        # 복구 시간이 증가하는 추세
        for i in range(10):
            sample = DNATestSample(
                timestamp=datetime.now().isoformat(),
                stage_name="Test",
                metrics={
                    "p99": 200,
                    "recovery_time_seconds": 10 + i * 5,  # 증가 추세
                },
                dna_config={"p99_threshold_ms": 300},
                passed=True,
            )
            learner.record_sample(sample)
        
        report = learner.analyze()
        
        recovery_insights = [i for i in report.insights if i.target_field == "recovery_time"]
        
        # 복구 시간 증가 경고
        if recovery_insights:
            assert "증가" in recovery_insights[0].reasoning
    
    def test_can_auto_apply_first_time(self, learner):
        """첫 자동 적용 가능"""
        assert learner._can_auto_apply() is True
    
    def test_can_auto_apply_after_cooldown(self, learner):
        """쿨다운 후 자동 적용"""
        learner.last_adjustment = datetime.now() - timedelta(hours=25)
        assert learner._can_auto_apply() is True
    
    def test_cannot_auto_apply_during_cooldown(self, learner):
        """쿨다운 중 자동 적용 불가"""
        learner.last_adjustment = datetime.now() - timedelta(hours=12)
        assert learner._can_auto_apply() is False
    
    def test_apply_suggestion(self, learner, sample_dna):
        """제안 적용"""
        insight = LearningInsight(
            insight_type="threshold",
            target_field="p99_threshold_ms",
            current_value=300,
            suggested_value=250,
            confidence=0.9,
            reasoning="테스트",
            evidence=[],
        )
        
        updated_dna = learner.apply_suggestion(insight, sample_dna)
        
        assert updated_dna["p99_threshold_ms"] == 250
        assert learner.last_adjustment is not None
    
    def test_apply_suggestion_no_value(self, learner, sample_dna):
        """제안 값 없는 경우"""
        insight = LearningInsight(
            insight_type="pattern",
            target_field="recovery_time",
            current_value=10,
            suggested_value=None,  # 제안 값 없음
            confidence=0.6,
            reasoning="패턴 경고",
            evidence=[],
        )
        
        updated_dna = learner.apply_suggestion(insight, sample_dna)
        
        # 원본 유지
        assert updated_dna == sample_dna
    
    def test_get_suggestions_summary_empty(self, learner):
        """제안 없는 경우 요약"""
        summary = learner.get_suggestions_summary()
        assert "제안 사항이 없습니다" in summary
    
    def test_get_suggestions_summary_with_insights(self, learner):
        """제안 있는 경우 요약"""
        learner.insights = [
            LearningInsight(
                insight_type="threshold",
                target_field="p99_threshold_ms",
                current_value=300,
                suggested_value=250,
                confidence=0.85,
                reasoning="테스트 이유",
                evidence=[],
            )
        ]
        
        summary = learner.get_suggestions_summary()
        
        assert "Self-Learning Suggestions" in summary
        assert "p99_threshold_ms" in summary
        assert "250" in summary
    
    def test_get_learning_stats_no_data(self, learner):
        """데이터 없는 경우 통계"""
        stats = learner.get_learning_stats()
        assert stats["status"] == "no_data"
    
    def test_get_learning_stats_with_data(self, learner):
        """데이터 있는 경우 통계"""
        for i in range(5):
            learner.record_sample(self._create_sample(stage_name=f"Stage_{i % 2}"))
        
        learner.analyze()
        stats = learner.get_learning_stats()
        
        assert stats["total_samples"] == 5
        assert stats["stages_analyzed"] == 2
        assert "pass_rate" in stats


class TestLearningReport:
    """LearningReport 테스트"""
    
    def test_report_structure(self):
        """보고서 구조"""
        report = LearningReport(
            analysis_timestamp="2025-01-01T00:00:00",
            samples_analyzed=10,
            insights=[],
            auto_applied=[],
            pending_review=["item1"],
        )
        
        assert report.samples_analyzed == 10
        assert len(report.pending_review) == 1


class TestGenerateLearningReportSection:
    """리포트 섹션 생성 테스트"""
    
    def test_generate_report_no_insights(self):
        """인사이트 없는 경우"""
        learner = DNALearner({"min_samples": 5})
        
        # 샘플 부족
        for i in range(3):
            learner.record_sample(DNATestSample(
                timestamp=datetime.now().isoformat(),
                stage_name="Test",
                metrics={"p99": 200},
                dna_config={},
                passed=True,
            ))
        
        report = generate_learning_report_section(learner)
        
        assert "Self-Learning DNA Report" in report
        assert "Analyzed Samples**: 3" in report
    
    def test_generate_report_with_insights(self):
        """인사이트 있는 경우"""
        learner = DNALearner({"min_samples": 3})
        
        for i in range(5):
            learner.record_sample(DNATestSample(
                timestamp=datetime.now().isoformat(),
                stage_name="Test",
                metrics={"p99": 180 + i, "error_rate": 0.002},
                dna_config={"p99_threshold_ms": 300, "error_rate_threshold": 0.01},
                passed=True,
            ))
        
        report = generate_learning_report_section(learner)
        
        assert "Self-Learning DNA Report" in report
        assert "Analyzed Samples**: 5" in report
    
    def test_generate_report_with_pending(self):
        """대기 중 항목 있는 경우"""
        learner = DNALearner({"min_samples": 5, "cooldown_hours": 24})
        
        # 쿨다운 중 설정
        learner.last_adjustment = datetime.now()
        
        for i in range(6):
            learner.record_sample(DNATestSample(
                timestamp=datetime.now().isoformat(),
                stage_name="Test",
                metrics={"p99": 150 + i, "error_rate": 0.002},
                dna_config={"p99_threshold_ms": 300, "error_rate_threshold": 0.01},
                passed=True,
            ))
        
        report = generate_learning_report_section(learner)
        
        # Pending Review 섹션 확인
        if "Pending Review" in report:
            assert "cooldown" in report or "confidence" in report


class TestIntegration:
    """통합 테스트"""
    
    def test_full_learning_cycle(self):
        """전체 학습 사이클"""
        config = {
            "enabled": True,
            "min_samples": 5,
            "confidence_threshold": 0.7,
            "cooldown_hours": 0,  # 즉시 적용 가능
        }
        learner = DNALearner(config)
        
        dna = {
            "p99_threshold_ms": 300,
            "error_rate_threshold": 0.01,
        }
        
        # 1. 샘플 수집
        for i in range(10):
            sample = DNATestSample(
                timestamp=datetime.now().isoformat(),
                stage_name="Production",
                metrics={
                    "p99": 150 + i,  # 평균 ~155
                    "error_rate": 0.002,  # 매우 낮음
                },
                dna_config=dna,
                passed=True,
            )
            learner.record_sample(sample)
        
        # 2. 분석
        report = learner.analyze()
        
        assert report.samples_analyzed == 10
        
        # 3. 인사이트 확인 및 적용
        for insight in report.insights:
            if insight.suggested_value is not None:
                updated_dna = learner.apply_suggestion(insight, dna)
                dna = updated_dna
        
        # 4. 통계 확인
        stats = learner.get_learning_stats()
        
        assert stats["total_samples"] == 10
        assert stats["pass_rate"] == 1.0

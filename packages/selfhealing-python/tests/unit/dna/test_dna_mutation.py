"""
Mutation DNA 단위 테스트

DNA Mutation 테스트
"""


import pytest
from load_tests.utils.selfhealing.dna_mutation import (
    DNAMutator,
    MutationExperiment,
    MutationReport,
    MutationType,
    generate_mutation_report_section,
)


class TestMutationType:
    """MutationType 테스트"""

    def test_enum_values(self):
        """Enum 값 확인"""
        assert MutationType.REMOVE_MODULE.value == "remove"
        assert MutationType.DEGRADE_MODULE.value == "degrade"
        assert MutationType.REPLACE_MODULE.value == "replace"
        assert MutationType.INVERT_BEHAVIOR.value == "invert"


class TestMutationExperiment:
    """MutationExperiment 테스트"""

    def test_default_values(self):
        """기본값 확인"""
        exp = MutationExperiment(
            experiment_id="test_001",
            original_dna={"name": "Test"},
            mutation_type=MutationType.REMOVE_MODULE,
            mutated_module="circuit_breaker",
            mutated_dna={"name": "Test"},
        )

        assert exp.started_at is None
        assert exp.completed_at is None
        assert exp.impact_score == 0.0
        assert exp.baseline_metrics == {}
        assert exp.mutated_metrics == {}
        assert exp.discovered_insights == []


class TestDNAMutator:
    """DNAMutator 테스트"""

    @pytest.fixture
    def base_dna(self):
        """테스트용 기본 DNA"""
        return {
            "name": "Test Stage",
            "required_modules": ["circuit_breaker", "dlq", "health"],
            "optional_modules": ["observability"],
        }

    def test_init(self, base_dna):
        """초기화 테스트"""
        mutator = DNAMutator(base_dna)
        assert mutator.base_dna == base_dna
        assert mutator.experiments == []

    def test_generate_mutations_remove(self, base_dna):
        """REMOVE_MODULE 변이 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.REMOVE_MODULE],
        )

        assert len(mutations) == 1
        assert "circuit_breaker" not in mutations[0]["required_modules"]
        assert mutations[0]["_mutation"]["type"] == "remove"
        assert mutations[0]["_mutation"]["module"] == "circuit_breaker"

    def test_generate_mutations_replace(self, base_dna):
        """REPLACE_MODULE 변이 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.REPLACE_MODULE],
        )

        assert len(mutations) == 1
        assert "circuit_breaker" not in mutations[0]["required_modules"]
        assert mutations[0]["_mutation"]["type"] == "replace"
        assert mutations[0]["_mutation"]["original"] == "circuit_breaker"
        assert mutations[0]["_mutation"]["replacement"] in mutator.ALTERNATIVES["circuit_breaker"]

    def test_generate_mutations_degrade(self, base_dna):
        """DEGRADE_MODULE 변이 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.DEGRADE_MODULE],
        )

        assert len(mutations) == 1
        assert mutations[0]["_mutation"]["type"] == "degrade"
        assert "degradation" in mutations[0]["_mutation"]

    def test_generate_mutations_invert(self, base_dna):
        """INVERT_BEHAVIOR 변이 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.INVERT_BEHAVIOR],
        )

        assert len(mutations) == 1
        assert mutations[0]["_mutation"]["type"] == "invert"

    def test_generate_all_mutations(self, base_dna):
        """모든 필수 모듈에 대한 변이 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations()

        # 3개 모듈 × 1개 타입 = 3개
        assert len(mutations) == 3

    def test_generate_multiple_types(self, base_dna):
        """여러 변이 유형 동시 생성"""
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.REMOVE_MODULE, MutationType.DEGRADE_MODULE],
        )

        assert len(mutations) == 2

    def test_run_experiment_success(self, base_dna):
        """실험 실행 - 성공"""
        mutator = DNAMutator(base_dna)

        mutated_dna = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
            mutation_types=[MutationType.REMOVE_MODULE],
        )[0]

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}

        def test_runner(dna):
            return {"p99": 250, "error_rate": 0.02, "availability": 0.998}

        exp = mutator.run_experiment(mutated_dna, test_runner, baseline)

        assert exp.experiment_id.startswith("mut_")
        assert exp.started_at is not None
        assert exp.completed_at is not None
        assert exp.mutated_metrics["p99"] == 250
        assert exp.impact_score > 0

    def test_run_experiment_with_error(self, base_dna):
        """실험 실행 - 에러 발생"""
        mutator = DNAMutator(base_dna)

        mutated_dna = mutator.generate_mutations(
            modules_to_mutate=["circuit_breaker"],
        )[0]

        def failing_runner(dna):
            raise Exception("Test failure")

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}
        exp = mutator.run_experiment(mutated_dna, failing_runner, baseline)

        assert exp.mutated_metrics.get("error") == 1.0
        assert "Test failure" in exp.mutated_metrics.get("error_message", "")
        assert exp.impact_score == 100.0  # 에러 시 최대 영향도

    def test_impact_score_high(self, base_dna):
        """높은 영향도 계산"""
        mutator = DNAMutator(base_dna)

        mutated_dna = mutator.generate_mutations(["circuit_breaker"])[0]

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}

        def high_impact_runner(dna):
            return {"p99": 600, "error_rate": 0.1, "availability": 0.95}

        exp = mutator.run_experiment(mutated_dna, high_impact_runner, baseline)

        assert exp.impact_score > 50
        assert "🔴" in exp.discovered_insights[0] or "🟡" in exp.discovered_insights[0]

    def test_impact_score_low(self, base_dna):
        """낮은 영향도 계산"""
        mutator = DNAMutator(base_dna)

        mutated_dna = mutator.generate_mutations(["circuit_breaker"])[0]

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}

        def low_impact_runner(dna):
            return {"p99": 205, "error_rate": 0.011, "availability": 0.998}

        exp = mutator.run_experiment(mutated_dna, low_impact_runner, baseline)

        assert exp.impact_score < 20
        assert "🟢" in exp.discovered_insights[0]

    def test_alternative_solutions(self, base_dna):
        """대안 솔루션 제안"""
        mutator = DNAMutator(base_dna)

        mutated_dna = mutator.generate_mutations(["circuit_breaker"])[0]

        exp = mutator.run_experiment(mutated_dna, lambda d: {"p99": 250}, {})

        assert len(exp.alternative_solutions) > 0
        assert any(alt in mutator.ALTERNATIVES["circuit_breaker"] for alt in exp.alternative_solutions)

    def test_generate_report(self, base_dna):
        """전체 보고서 생성"""
        mutator = DNAMutator(base_dna)

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}

        # 여러 실험 수행
        for module in ["circuit_breaker", "dlq", "health"]:
            mutations = mutator.generate_mutations([module])
            if mutations:
                # Critical 모듈
                if module == "circuit_breaker":
                    runner = lambda d: {"p99": 800, "error_rate": 0.2, "availability": 0.9}
                # Optional 모듈
                else:
                    runner = lambda d: {"p99": 210, "error_rate": 0.012, "availability": 0.998}

                mutator.run_experiment(mutations[0], runner, baseline)

        report = mutator.generate_report()

        assert report.total_experiments == 3
        assert report.successful_mutations == 3
        assert len(report.critical_modules) >= 0
        assert isinstance(report.insights, list)

    def test_get_module_criticality(self, base_dna):
        """모듈별 중요도 분류"""
        mutator = DNAMutator(base_dna)

        baseline = {"p99": 200, "error_rate": 0.01, "availability": 0.999}

        # Critical 모듈 시뮬레이션
        mutations = mutator.generate_mutations(["circuit_breaker"])
        mutator.run_experiment(
            mutations[0],
            lambda d: {"p99": 900, "error_rate": 0.3, "availability": 0.8},
            baseline
        )

        criticality = mutator.get_module_criticality()

        assert "circuit_breaker" in criticality
        assert criticality["circuit_breaker"] in ["critical", "important", "optional"]


class TestMutationReport:
    """MutationReport 테스트"""

    def test_report_structure(self):
        """보고서 구조 확인"""
        report = MutationReport(
            total_experiments=5,
            successful_mutations=4,
            critical_modules=["circuit_breaker"],
            redundant_modules=["logging"],
            insights=["insight1", "insight2"],
            suggested_improvements=["improvement1"],
        )

        assert report.total_experiments == 5
        assert report.successful_mutations == 4
        assert "circuit_breaker" in report.critical_modules
        assert "logging" in report.redundant_modules


class TestGenerateMutationReportSection:
    """리포트 섹션 생성 테스트"""

    def test_generate_report_empty(self):
        """실험 없는 경우"""
        base_dna = {"required_modules": ["cb"]}
        mutator = DNAMutator(base_dna)

        report = generate_mutation_report_section(mutator)

        assert "Mutation DNA Report" in report
        assert "Total Experiments**: 0" in report

    def test_generate_report_with_experiments(self):
        """실험 있는 경우"""
        base_dna = {"required_modules": ["circuit_breaker", "dlq"]}
        mutator = DNAMutator(base_dna)

        mutations = mutator.generate_mutations(["circuit_breaker"])
        mutator.run_experiment(
            mutations[0],
            lambda d: {"p99": 500},
            {"p99": 200}
        )

        report = generate_mutation_report_section(mutator)

        assert "Mutation DNA Report" in report
        assert "circuit_breaker" in report
        assert "Module Criticality" in report

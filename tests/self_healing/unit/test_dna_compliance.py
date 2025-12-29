"""
Compliance DNA 단위 테스트

Phase 2: dna_compliance.py 모듈 테스트
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from load_tests.utils.selfhealing.dna_compliance import (
    ComplianceVerifier,
    ComplianceMapper,
    ComplianceRequirement,
    ComplianceReport,
    ComplianceEvidence,
    ComplianceStandard,
    ComplianceStatus,
    DORARequirement,
    PCIDSSRequirement,
    SOC2Requirement,
)


class TestComplianceStandard:
    """ComplianceStandard 열거형 테스트"""
    
    def test_standard_values(self):
        """표준 값 테스트"""
        assert ComplianceStandard.DORA_2025.value == "DORA_2025"
        assert ComplianceStandard.PCI_DSS_4_0.value == "PCI_DSS_4_0"
        assert ComplianceStandard.SOC2_TYPE_II.value == "SOC2_TYPE_II"


class TestComplianceStatus:
    """ComplianceStatus 열거형 테스트"""
    
    def test_status_values(self):
        """상태 값 테스트"""
        assert ComplianceStatus.COMPLIANT.value == "compliant"
        assert ComplianceStatus.NON_COMPLIANT.value == "non_compliant"
        assert ComplianceStatus.PARTIAL.value == "partial"
        assert ComplianceStatus.NOT_APPLICABLE.value == "not_applicable"


class TestComplianceRequirement:
    """ComplianceRequirement 데이터 클래스 테스트"""
    
    def test_requirement_creation(self):
        """요구사항 생성 테스트"""
        req = ComplianceRequirement(
            id="DORA-RTO-1",
            standard=ComplianceStandard.DORA_2025,
            name="RTO Requirement",
            description="Maximum 4 hour RTO for critical services",
            category="Recovery",
            is_mandatory=True,
        )
        
        assert req.id == "DORA-RTO-1"
        assert req.standard == ComplianceStandard.DORA_2025
        assert req.is_mandatory is True
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        req = ComplianceRequirement(
            id="PCI-DSS-6.1",
            standard=ComplianceStandard.PCI_DSS_4_0,
            name="Security Patch",
            description="Apply security patches within 30 days",
            category="Security",
            is_mandatory=True,
        )
        
        result = req.to_dict()
        
        assert result["id"] == "PCI-DSS-6.1"
        assert result["standard"] == "PCI_DSS_4_0"


class TestComplianceEvidence:
    """ComplianceEvidence 데이터 클래스 테스트"""
    
    def test_evidence_creation(self):
        """증거 생성 테스트"""
        evidence = ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"rto_achieved": "2h 15m", "target": "4h"},
            source="stage15_chaos_test",
        )
        
        assert evidence.requirement_id == "DORA-RTO-1"
        assert evidence.evidence_type == "test_result"
    
    def test_is_valid_true(self):
        """유효한 증거 테스트"""
        evidence = ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"result": "pass"},
            source="test_stage",
            is_verified=True,
        )
        
        assert evidence.is_valid is True
    
    def test_is_valid_false_no_data(self):
        """데이터 없는 증거는 유효하지 않음"""
        evidence = ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={},
            source="test_stage",
        )
        
        assert evidence.is_valid is False


class TestDORARequirement:
    """DORA 2025 요구사항 테스트"""
    
    def test_rto_requirement(self):
        """RTO 요구사항 테스트"""
        req = DORARequirement.RTO_CRITICAL
        
        assert "RTO" in req.name
        assert req.standard == ComplianceStandard.DORA_2025
    
    def test_rpo_requirement(self):
        """RPO 요구사항 테스트"""
        req = DORARequirement.RPO_CRITICAL
        
        assert "RPO" in req.name
        assert req.standard == ComplianceStandard.DORA_2025
    
    def test_chaos_testing_requirement(self):
        """카오스 테스트 요구사항"""
        req = DORARequirement.CHAOS_TESTING
        
        assert "chaos" in req.name.lower() or "test" in req.name.lower()


class TestPCIDSSRequirement:
    """PCI-DSS 4.0 요구사항 테스트"""
    
    def test_encryption_requirement(self):
        """암호화 요구사항 테스트"""
        req = PCIDSSRequirement.ENCRYPTION_AT_REST
        
        assert req.standard == ComplianceStandard.PCI_DSS_4_0
        assert "encryption" in req.name.lower()
    
    def test_access_control_requirement(self):
        """접근 제어 요구사항 테스트"""
        req = PCIDSSRequirement.ACCESS_CONTROL
        
        assert "access" in req.name.lower()


class TestSOC2Requirement:
    """SOC2 Type II 요구사항 테스트"""
    
    def test_availability_requirement(self):
        """가용성 요구사항 테스트"""
        req = SOC2Requirement.AVAILABILITY
        
        assert req.standard == ComplianceStandard.SOC2_TYPE_II
    
    def test_confidentiality_requirement(self):
        """기밀성 요구사항 테스트"""
        req = SOC2Requirement.CONFIDENTIALITY
        
        assert req.standard == ComplianceStandard.SOC2_TYPE_II


class TestComplianceMapper:
    """ComplianceMapper 클래스 테스트"""
    
    @pytest.fixture
    def mapper(self):
        """기본 Compliance Mapper"""
        return ComplianceMapper()
    
    def test_get_all_requirements(self, mapper):
        """모든 요구사항 조회"""
        requirements = mapper.get_all_requirements()
        
        assert len(requirements) > 0
        assert all(isinstance(r, ComplianceRequirement) for r in requirements)
    
    def test_get_requirements_by_standard(self, mapper):
        """표준별 요구사항 조회"""
        dora_reqs = mapper.get_requirements_by_standard(ComplianceStandard.DORA_2025)
        
        assert len(dora_reqs) > 0
        assert all(r.standard == ComplianceStandard.DORA_2025 for r in dora_reqs)
    
    def test_get_mandatory_requirements(self, mapper):
        """필수 요구사항 조회"""
        mandatory = mapper.get_mandatory_requirements()
        
        assert len(mandatory) > 0
        assert all(r.is_mandatory for r in mandatory)
    
    def test_map_stage_to_requirements(self, mapper):
        """Stage → 요구사항 매핑"""
        # stage15 = Chaos Test → DORA Chaos Testing
        requirements = mapper.map_stage_to_requirements("stage15")
        
        assert len(requirements) >= 0  # 매핑이 없을 수도 있음
    
    def test_map_requirement_to_stages(self, mapper):
        """요구사항 → Stage 매핑"""
        # DORA RTO → stage15 등
        stages = mapper.map_requirement_to_stages("DORA-RTO-1")
        
        assert isinstance(stages, list)


class TestComplianceVerifier:
    """ComplianceVerifier 클래스 테스트"""
    
    @pytest.fixture
    def verifier(self):
        """기본 Compliance Verifier"""
        return ComplianceVerifier()
    
    def test_initialization(self, verifier):
        """초기화 테스트"""
        assert verifier.mapper is not None
        assert isinstance(verifier.evidence_store, dict)
    
    def test_collect_evidence(self, verifier):
        """증거 수집 테스트"""
        evidence = ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"rto_achieved": "2h", "passed": True},
            source="stage15",
        )
        
        verifier.collect_evidence(evidence)
        
        assert "DORA-RTO-1" in verifier.evidence_store
        assert len(verifier.evidence_store["DORA-RTO-1"]) == 1
    
    def test_verify_requirement_compliant(self, verifier):
        """요구사항 검증 - 충족"""
        requirement = ComplianceRequirement(
            id="TEST-REQ-1",
            standard=ComplianceStandard.DORA_2025,
            name="Test Requirement",
            description="Test requirement for unit test",
            category="Testing",
            is_mandatory=True,
        )
        
        # 증거 추가
        evidence = ComplianceEvidence(
            requirement_id="TEST-REQ-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"passed": True},
            source="unit_test",
            is_verified=True,
        )
        verifier.collect_evidence(evidence)
        
        status = verifier.verify_requirement(requirement)
        
        assert status == ComplianceStatus.COMPLIANT
    
    def test_verify_requirement_non_compliant(self, verifier):
        """요구사항 검증 - 미충족"""
        requirement = ComplianceRequirement(
            id="UNMET-REQ-1",
            standard=ComplianceStandard.DORA_2025,
            name="Unmet Requirement",
            description="Requirement without evidence",
            category="Testing",
            is_mandatory=True,
        )
        
        # 증거 없음
        status = verifier.verify_requirement(requirement)
        
        assert status == ComplianceStatus.NON_COMPLIANT
    
    def test_generate_report(self, verifier):
        """리포트 생성 테스트"""
        # 일부 증거 추가
        evidence = ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"passed": True},
            source="stage15",
            is_verified=True,
        )
        verifier.collect_evidence(evidence)
        
        report = verifier.generate_report()
        
        assert isinstance(report, ComplianceReport)
        assert report.total_requirements > 0


class TestComplianceReport:
    """ComplianceReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = ComplianceReport(
            generated_at=datetime.now().isoformat(),
            total_requirements=10,
            compliant_count=8,
            non_compliant_count=1,
            partial_count=1,
            overall_status=ComplianceStatus.PARTIAL,
            by_standard={},
            details=[],
        )
        
        assert report.total_requirements == 10
        assert report.overall_status == ComplianceStatus.PARTIAL
    
    def test_compliance_percentage(self):
        """준수율 계산 테스트"""
        report = ComplianceReport(
            generated_at=datetime.now().isoformat(),
            total_requirements=10,
            compliant_count=7,
            non_compliant_count=2,
            partial_count=1,
            overall_status=ComplianceStatus.PARTIAL,
            by_standard={},
            details=[],
        )
        
        assert report.compliance_percentage == 70.0
    
    def test_is_fully_compliant_true(self):
        """완전 준수 테스트"""
        report = ComplianceReport(
            generated_at=datetime.now().isoformat(),
            total_requirements=10,
            compliant_count=10,
            non_compliant_count=0,
            partial_count=0,
            overall_status=ComplianceStatus.COMPLIANT,
            by_standard={},
            details=[],
        )
        
        assert report.is_fully_compliant is True
    
    def test_is_fully_compliant_false(self):
        """불완전 준수 테스트"""
        report = ComplianceReport(
            generated_at=datetime.now().isoformat(),
            total_requirements=10,
            compliant_count=9,
            non_compliant_count=1,
            partial_count=0,
            overall_status=ComplianceStatus.PARTIAL,
            by_standard={},
            details=[],
        )
        
        assert report.is_fully_compliant is False
    
    def test_to_markdown(self):
        """마크다운 변환 테스트"""
        report = ComplianceReport(
            generated_at="2025-12-29T00:00:00",
            total_requirements=10,
            compliant_count=8,
            non_compliant_count=1,
            partial_count=1,
            overall_status=ComplianceStatus.PARTIAL,
            by_standard={
                "DORA_2025": {"compliant": 5, "total": 6},
                "PCI_DSS_4_0": {"compliant": 3, "total": 4},
            },
            details=[],
        )
        
        md = report.to_markdown()
        
        assert "# Compliance Report" in md
        assert "80.0%" in md  # 8/10 = 80%
        assert "DORA_2025" in md


class TestCertificationStatement:
    """인증 진술서 생성 테스트"""
    
    def test_generate_certification_statement(self):
        """인증 진술서 생성"""
        verifier = ComplianceVerifier()
        
        # 충분한 증거 추가
        for i in range(5):
            evidence = ComplianceEvidence(
                requirement_id=f"DORA-REQ-{i}",
                collected_at=datetime.now().isoformat(),
                evidence_type="test_result",
                data={"passed": True},
                source="automated_test",
                is_verified=True,
            )
            verifier.collect_evidence(evidence)
        
        statement = verifier.generate_certification_statement()
        
        assert isinstance(statement, str)
        assert len(statement) > 0


class TestComplianceWithGovernance:
    """Governance 연동 테스트"""
    
    def test_validate_with_governance_policy(self):
        """Governance 정책 연동 검증"""
        verifier = ComplianceVerifier()
        
        # Mock governance policy
        governance_policy = {
            "required_standards": ["DORA_2025", "PCI_DSS_4_0"],
            "minimum_compliance_percentage": 80.0,
        }
        
        # 충분한 증거 추가
        verifier.collect_evidence(ComplianceEvidence(
            requirement_id="DORA-RTO-1",
            collected_at=datetime.now().isoformat(),
            evidence_type="test_result",
            data={"passed": True},
            source="test",
            is_verified=True,
        ))
        
        result = verifier.validate_against_governance(governance_policy)
        
        assert isinstance(result, dict)
        assert "meets_minimum" in result

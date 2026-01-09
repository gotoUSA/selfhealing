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
    ComplianceResult,
    ComplianceStandard,
    ComplianceTestType,
)


class TestComplianceStandard:
    """ComplianceStandard 열거형 테스트"""
    
    def test_standard_values(self):
        """표준 값 테스트"""
        assert ComplianceStandard.DORA_2025.value == "DORA_2025"
        assert ComplianceStandard.PCI_DSS_4_0.value == "PCI-DSS_4.0"
        assert ComplianceStandard.SOC2_TYPE2.value == "SOC2_TYPE2"
        assert ComplianceStandard.ISO_27001.value == "ISO_27001"
        assert ComplianceStandard.HIPAA.value == "HIPAA"
        assert ComplianceStandard.GDPR.value == "GDPR"
        assert ComplianceStandard.K_ISMS.value == "K-ISMS"


class TestComplianceTestType:
    """ComplianceTestType 열거형 테스트"""
    
    def test_test_type_values(self):
        """테스트 유형 값 테스트"""
        assert ComplianceTestType.RESILIENCE.value == "resilience"
        assert ComplianceTestType.SECURITY.value == "security"
        assert ComplianceTestType.AVAILABILITY.value == "availability"
        assert ComplianceTestType.AUDIT.value == "audit"
        assert ComplianceTestType.INCIDENT_RESPONSE.value == "incident_response"


class TestComplianceRequirement:
    """ComplianceRequirement 데이터 클래스 테스트"""
    
    def test_requirement_creation(self):
        """요구사항 생성 테스트"""
        req = ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-ICT-1",
            description="ICT risk management framework",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={"min_coverage": 90},
            evidence_required=["test_results", "documentation"],
            severity="high",
        )
        
        assert req.standard == ComplianceStandard.DORA_2025
        assert req.requirement_id == "DORA-ICT-1"
        assert req.test_type == ComplianceTestType.RESILIENCE
        assert req.severity == "high"
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        req = ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-3.4",
            description="Render PAN unreadable",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={"encryption": True},
            evidence_required=["encryption_logs"],
        )
        
        result = req.to_dict()
        
        assert result["standard"] == "PCI-DSS_4.0"
        assert result["requirement_id"] == "PCI-3.4"
        assert result["test_type"] == "security"


class TestComplianceEvidence:
    """ComplianceEvidence 데이터 클래스 테스트"""
    
    def test_evidence_creation(self):
        """증거 생성 테스트"""
        evidence = ComplianceEvidence(
            evidence_id="ev_001",
            requirement_id="DORA-ICT-1",
            collected_at="2025-01-01T12:00:00",
            evidence_type="test_result",
            data={"passed": True, "score": 95},
            verified=True,
            verifier="auditor@company.com",
        )
        
        assert evidence.evidence_id == "ev_001"
        assert evidence.requirement_id == "DORA-ICT-1"
        assert evidence.verified is True
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        evidence = ComplianceEvidence(
            evidence_id="ev_002",
            requirement_id="PCI-3.4",
            collected_at="2025-01-01T12:00:00",
            evidence_type="log",
            data={"encryption_enabled": True},
        )
        
        result = evidence.to_dict()
        
        assert result["evidence_id"] == "ev_002"
        assert result["requirement_id"] == "PCI-3.4"
        assert result["evidence_type"] == "log"


class TestComplianceResult:
    """ComplianceResult 데이터 클래스 테스트"""
    
    def test_result_creation(self):
        """결과 생성 테스트"""
        req = ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-CC6.1",
            description="Logical and physical access controls",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={},
            evidence_required=[],
        )
        
        result = ComplianceResult(
            requirement=req,
            passed=True,
            evidence=[],
            checked_at="2025-01-01T12:00:00",
            notes="All controls verified",
        )
        
        assert result.passed is True
        assert result.requirement == req
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        req = ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-TEST-1",
            description="Test requirement",
            test_type=ComplianceTestType.AVAILABILITY,
            pass_criteria={},
            evidence_required=[],
        )
        
        result = ComplianceResult(
            requirement=req,
            passed=False,
            evidence=[],
            checked_at="2025-01-01T12:00:00",
            remediation="Fix the issue",
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["passed"] is False
        assert result_dict["remediation"] == "Fix the issue"


class TestComplianceReport:
    """ComplianceReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = ComplianceReport(
            report_id="rpt_001",
            generated_at="2025-01-01T12:00:00",
            standards=[ComplianceStandard.DORA_2025, ComplianceStandard.PCI_DSS_4_0],
            results=[],
            overall_passed=True,
            certification_statement="All requirements met",
        )
        
        assert report.report_id == "rpt_001"
        assert len(report.standards) == 2
        assert report.overall_passed is True
    
    def test_pass_rate_empty(self):
        """통과율 - 빈 결과"""
        report = ComplianceReport(
            report_id="rpt_002",
            generated_at="2025-01-01T12:00:00",
            standards=[],
            results=[],
            overall_passed=False,
            certification_statement="",
        )
        
        assert report.pass_rate == 0.0
    
    def test_pass_rate_calculation(self):
        """통과율 계산 테스트"""
        req1 = ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="REQ-1",
            description="Requirement 1",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={},
            evidence_required=[],
        )
        req2 = ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="REQ-2",
            description="Requirement 2",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={},
            evidence_required=[],
        )
        
        results = [
            ComplianceResult(requirement=req1, passed=True, evidence=[], checked_at=""),
            ComplianceResult(requirement=req2, passed=False, evidence=[], checked_at=""),
        ]
        
        report = ComplianceReport(
            report_id="rpt_003",
            generated_at="2025-01-01T12:00:00",
            standards=[ComplianceStandard.DORA_2025],
            results=results,
            overall_passed=False,
            certification_statement="",
        )
        
        assert report.pass_rate == 50.0
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        report = ComplianceReport(
            report_id="rpt_004",
            generated_at="2025-01-01T12:00:00",
            standards=[ComplianceStandard.SOC2_TYPE2],
            results=[],
            overall_passed=True,
            certification_statement="Certified",
        )
        
        result = report.to_dict()
        
        assert result["report_id"] == "rpt_004"
        assert result["overall_passed"] is True
        assert "summary" in result


class TestComplianceMapper:
    """ComplianceMapper 테스트"""
    
    def test_get_requirements_by_standard_list(self):
        """표준 목록으로 요구사항 조회 테스트"""
        reqs = ComplianceMapper.get_requirements([ComplianceStandard.DORA_2025])
        
        assert isinstance(reqs, list)
    
    def test_get_all_requirements(self):
        """모든 요구사항 조회 테스트"""
        reqs = ComplianceMapper.get_all_requirements()
        
        assert isinstance(reqs, list)
        assert len(reqs) > 0


class TestComplianceVerifier:
    """ComplianceVerifier 테스트"""
    
    def test_verifier_initialization(self):
        """검증기 초기화 테스트"""
        verifier = ComplianceVerifier(
            standards=[ComplianceStandard.DORA_2025],
        )
        
        assert verifier is not None
        assert ComplianceStandard.DORA_2025 in verifier.standards
    
    def test_verify_requirement(self):
        """요구사항 검증 테스트"""
        verifier = ComplianceVerifier(
            standards=[ComplianceStandard.PCI_DSS_4_0],
        )
        
        req = ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-TEST",
            description="Test requirement",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={"min_score": 80},
            evidence_required=["test_log"],
        )
        
        result = verifier.verify_requirement(
            requirement=req,
            test_results={"score": 85},
        )
        
        assert isinstance(result, ComplianceResult)
    
    def test_collect_evidence(self):
        """증거 수집 테스트"""
        verifier = ComplianceVerifier(
            standards=[ComplianceStandard.DORA_2025],
        )
        
        evidence = verifier.collect_evidence(
            requirement_id="DORA-ICT-1",
            evidence_type="test_result",
            data={"passed": True},
        )
        
        assert isinstance(evidence, ComplianceEvidence)
        assert evidence.requirement_id == "DORA-ICT-1"
    
    def test_from_dna(self):
        """DNA 설정에서 생성 테스트"""
        compliance_config = {
            "standards": ["DORA_2025", "PCI-DSS_4.0"],
        }
        
        verifier = ComplianceVerifier.from_dna(compliance_config)
        
        assert len(verifier.standards) == 2

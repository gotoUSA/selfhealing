"""
Compliance DNA - 규제 준수 자동 증명

Phase 2 구현: 금융권 필수 규제 (DORA 2025, PCI-DSS 4.0, SOC2 Type II)

기능:
1. DORA, PCI-DSS, SOC2 규제 요구사항 매핑
2. 테스트 결과 → 규제 요구사항 매핑
3. 자동 Compliance 리포트 생성
4. 감사 증거 수집
5. 기존 governance.py와 연동

참조:
- docs/self_healing/32_DNA_ENTERPRISE_FEATURES.md (Section 2)
- docs/self_healing/29_STAGE_DNA_EVOLUTION_MASTER.md

비즈니스 가치: +$100M (금융권 / 규제 산업)

기존 구현 활용:
- governance.py: get_compliance_status(), run_compliance_check()
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta
import json
import uuid
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# 규제 표준 정의
# =============================================================================

class ComplianceStandard(Enum):
    """규제 표준"""
    DORA_2025 = "DORA_2025"           # EU 디지털 운영 회복력법
    PCI_DSS_4_0 = "PCI-DSS_4.0"       # 결제 카드 데이터 보안
    SOC2_TYPE2 = "SOC2_TYPE2"         # 서비스 조직 통제
    ISO_27001 = "ISO_27001"           # 정보보안 관리
    HIPAA = "HIPAA"                   # 의료 정보 보호
    GDPR = "GDPR"                     # 개인정보보호규정
    K_ISMS = "K-ISMS"                 # 한국 정보보호관리체계


class ComplianceTestType(Enum):
    """컴플라이언스 테스트 유형 (pytest 수집 방지를 위해 Test로 시작하지 않음)"""
    RESILIENCE = "resilience"          # 회복력 테스트
    SECURITY = "security"              # 보안 테스트
    AVAILABILITY = "availability"      # 가용성 테스트
    AUDIT = "audit"                    # 감사 테스트
    INCIDENT_RESPONSE = "incident_response"  # 인시던트 대응 테스트
    PENETRATION = "penetration"        # 침투 테스트


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class ComplianceRequirement:
    """규제 요구사항"""
    standard: ComplianceStandard
    requirement_id: str
    description: str
    test_type: ComplianceTestType
    pass_criteria: Dict[str, Any]
    evidence_required: List[str]
    severity: str = "high"  # critical, high, medium, low
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard": self.standard.value,
            "requirement_id": self.requirement_id,
            "description": self.description,
            "test_type": self.test_type.value,
            "pass_criteria": self.pass_criteria,
            "evidence_required": self.evidence_required,
            "severity": self.severity,
        }


@dataclass
class ComplianceEvidence:
    """규제 준수 증거"""
    evidence_id: str
    requirement_id: str
    collected_at: str
    evidence_type: str
    data: Dict[str, Any]
    verified: bool = False
    verifier: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "requirement_id": self.requirement_id,
            "collected_at": self.collected_at,
            "evidence_type": self.evidence_type,
            "verified": self.verified,
            "data_summary": str(self.data)[:200] + "..." if len(str(self.data)) > 200 else str(self.data),
        }


@dataclass
class ComplianceResult:
    """규제 준수 결과"""
    requirement: ComplianceRequirement
    passed: bool
    evidence: List[ComplianceEvidence]
    checked_at: str
    notes: str = ""
    remediation: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement.requirement_id,
            "standard": self.requirement.standard.value,
            "description": self.requirement.description,
            "passed": self.passed,
            "evidence_count": len(self.evidence),
            "checked_at": self.checked_at,
            "notes": self.notes,
            "remediation": self.remediation,
        }


@dataclass
class ComplianceReport:
    """Compliance 리포트"""
    report_id: str
    generated_at: str
    standards: List[ComplianceStandard]
    results: List[ComplianceResult]
    overall_passed: bool
    certification_statement: str
    next_audit_date: Optional[str] = None
    
    @property
    def pass_rate(self) -> float:
        """통과율"""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results) * 100
    
    @property
    def by_standard(self) -> Dict[str, Dict[str, int]]:
        """규제별 통계"""
        stats = {}
        for result in self.results:
            std = result.requirement.standard.value
            if std not in stats:
                stats[std] = {"total": 0, "passed": 0, "failed": 0}
            stats[std]["total"] += 1
            if result.passed:
                stats[std]["passed"] += 1
            else:
                stats[std]["failed"] += 1
        return stats
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "standards": [s.value for s in self.standards],
            "summary": {
                "total_requirements": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "failed": sum(1 for r in self.results if not r.passed),
                "pass_rate": self.pass_rate,
            },
            "by_standard": self.by_standard,
            "overall_passed": self.overall_passed,
            "certification_statement": self.certification_statement,
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
    
    def to_markdown(self) -> str:
        """마크다운 리포트 생성"""
        lines = [
            "# ⚖️ Compliance Report",
            "",
            f"📅 **Generated**: {self.generated_at}",
            f"🆔 **Report ID**: {self.report_id}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Standards | {', '.join(s.value for s in self.standards)} |",
            f"| Total Requirements | {len(self.results)} |",
            f"| Passed | {sum(1 for r in self.results if r.passed)} |",
            f"| Failed | {sum(1 for r in self.results if not r.passed)} |",
            f"| Pass Rate | {self.pass_rate:.1f}% |",
            f"| Overall | {'✅ PASSED' if self.overall_passed else '❌ FAILED'} |",
            "",
            "## By Standard",
            "",
            "| Standard | Passed | Failed | Rate |",
            "|----------|--------|--------|------|",
        ]
        
        for std, stats in self.by_standard.items():
            rate = stats["passed"] / stats["total"] * 100 if stats["total"] > 0 else 0
            status = "✅" if stats["failed"] == 0 else "⚠️"
            lines.append(f"| {std} {status} | {stats['passed']} | {stats['failed']} | {rate:.1f}% |")
        
        lines.extend([
            "",
            "## Detailed Results",
            "",
        ])
        
        for result in self.results:
            status = "✅" if result.passed else "❌"
            lines.append(f"### {status} {result.requirement.requirement_id}")
            lines.append(f"- **Standard**: {result.requirement.standard.value}")
            lines.append(f"- **Description**: {result.requirement.description}")
            lines.append(f"- **Evidence Count**: {len(result.evidence)}")
            if result.notes:
                lines.append(f"- **Notes**: {result.notes}")
            if result.remediation:
                lines.append(f"- **Remediation**: {result.remediation}")
            lines.append("")
        
        lines.extend([
            "## Certification Statement",
            "",
            f"> {self.certification_statement}",
            "",
        ])
        
        if self.next_audit_date:
            lines.append(f"📆 **Next Audit**: {self.next_audit_date}")
        
        return "\n".join(lines)


# =============================================================================
# 규제 요구사항 매퍼
# =============================================================================

class ComplianceMapper:
    """규제 요구사항 매퍼"""
    
    # DORA 2025 요구사항
    DORA_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-17.1",
            description="ICT 시스템 회복력 테스트 수행",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={
                "chaos_test_completed": True,
                "recovery_time_minutes": 120,
            },
            evidence_required=["chaos_test_report", "recovery_time_log"],
            severity="critical",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-17.2",
            description="시나리오 기반 테스트 수행 (최소 5개 장애 시나리오)",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={
                "scenarios_tested": 5,
                "failure_modes_covered": ["network", "database", "external_api"],
            },
            evidence_required=["scenario_list", "test_results"],
            severity="high",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-17.3",
            description="정기적인 회복력 테스트 (분기별)",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={
                "test_frequency_days": 90,
                "last_test_within_period": True,
            },
            evidence_required=["test_schedule", "test_history"],
            severity="high",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-25.1",
            description="중대 인시던트 72시간 내 보고 체계",
            test_type=ComplianceTestType.INCIDENT_RESPONSE,
            pass_criteria={
                "incident_response_plan_exists": True,
                "notification_channel_tested": True,
                "max_notification_hours": 72,
            },
            evidence_required=["incident_response_plan", "notification_test_log"],
            severity="critical",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-28.1",
            description="써드파티 ICT 서비스 모니터링",
            test_type=ComplianceTestType.AVAILABILITY,
            pass_criteria={
                "third_party_monitoring_enabled": True,
                "sla_tracking": True,
            },
            evidence_required=["vendor_list", "monitoring_dashboard"],
            severity="high",
        ),
    ]
    
    # PCI-DSS 4.0 요구사항
    PCI_DSS_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-12.10.1",
            description="인시던트 대응 계획 및 테스트",
            test_type=ComplianceTestType.INCIDENT_RESPONSE,
            pass_criteria={
                "incident_response_plan_tested": True,
                "response_time_minutes": 30,
            },
            evidence_required=["incident_response_test_report"],
            severity="critical",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-11.3.1",
            description="내부 취약점 스캔 (분기별)",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={
                "vulnerability_scan_completed": True,
                "critical_vulnerabilities": 0,
                "high_vulnerabilities_remediated": True,
            },
            evidence_required=["vulnerability_scan_report"],
            severity="critical",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-11.4.1",
            description="침투 테스트 (연간)",
            test_type=ComplianceTestType.PENETRATION,
            pass_criteria={
                "penetration_test_completed": True,
                "critical_findings_remediated": True,
            },
            evidence_required=["penetration_test_report", "remediation_log"],
            severity="critical",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-3.4.1",
            description="저장 데이터 암호화",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={
                "data_encryption_enabled": True,
                "encryption_algorithm": "AES-256",
            },
            evidence_required=["encryption_config", "key_management_policy"],
            severity="critical",
        ),
    ]
    
    # SOC 2 Type II 요구사항
    SOC2_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-A1.1",
            description="가용성 SLA 달성 (99.9%)",
            test_type=ComplianceTestType.AVAILABILITY,
            pass_criteria={
                "availability_percentage": 99.9,
                "max_unplanned_downtime_minutes": 525.6,  # 99.9% = 연간 ~8.76시간
            },
            evidence_required=["uptime_report", "incident_log"],
            severity="high",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-CC6.1",
            description="변경 관리 프로세스",
            test_type=ComplianceTestType.AUDIT,
            pass_criteria={
                "change_management_enabled": True,
                "approval_workflow_exists": True,
                "rollback_capability": True,
            },
            evidence_required=["change_log", "approval_records"],
            severity="high",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-CC7.2",
            description="보안 이벤트 모니터링",
            test_type=ComplianceTestType.SECURITY,
            pass_criteria={
                "security_monitoring_enabled": True,
                "alert_response_time_minutes": 15,
            },
            evidence_required=["monitoring_config", "alert_history"],
            severity="high",
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-CC9.1",
            description="비즈니스 연속성 계획",
            test_type=ComplianceTestType.RESILIENCE,
            pass_criteria={
                "bcp_exists": True,
                "bcp_tested_annually": True,
                "rpo_hours": 4,
                "rto_hours": 8,
            },
            evidence_required=["bcp_document", "bcp_test_report"],
            severity="critical",
        ),
    ]
    
    @classmethod
    def get_requirements(
        cls,
        standards: List[ComplianceStandard]
    ) -> List[ComplianceRequirement]:
        """
        규제별 요구사항 조회
        
        Args:
            standards: 규제 표준 목록
            
        Returns:
            요구사항 목록
        """
        requirements = []
        
        for standard in standards:
            if standard == ComplianceStandard.DORA_2025:
                requirements.extend(cls.DORA_REQUIREMENTS)
            elif standard == ComplianceStandard.PCI_DSS_4_0:
                requirements.extend(cls.PCI_DSS_REQUIREMENTS)
            elif standard == ComplianceStandard.SOC2_TYPE2:
                requirements.extend(cls.SOC2_REQUIREMENTS)
        
        return requirements
    
    @classmethod
    def get_all_requirements(cls) -> List[ComplianceRequirement]:
        """모든 요구사항 조회"""
        return cls.DORA_REQUIREMENTS + cls.PCI_DSS_REQUIREMENTS + cls.SOC2_REQUIREMENTS


# =============================================================================
# Compliance Verifier
# =============================================================================

class ComplianceVerifier:
    """
    규제 준수 검증기
    
    기존 governance.py의 API를 활용하면서
    DORA/PCI-DSS/SOC2 특화 로직을 추가합니다.
    """
    
    def __init__(
        self,
        standards: List[ComplianceStandard] = None,
        governance_client: Optional[Any] = None,
    ):
        """
        초기화
        
        Args:
            standards: 검증할 규제 표준 목록
            governance_client: 기존 GovernanceClient (선택적)
        """
        self.standards = standards or []
        self.governance_client = governance_client
        self.requirements = ComplianceMapper.get_requirements(self.standards)
        self.evidence: List[ComplianceEvidence] = []
        self.results: List[ComplianceResult] = []
    
    @classmethod
    def from_dna(
        cls,
        compliance_config: Dict[str, Any],
        governance_client: Optional[Any] = None
    ) -> "ComplianceVerifier":
        """
        Stage DNA의 compliance 설정에서 생성
        
        Args:
            compliance_config: STAGE_DNA["compliance"] 딕셔너리
            governance_client: GovernanceClient (선택적)
            
        Returns:
            ComplianceVerifier 인스턴스
        """
        standards = []
        for std_str in compliance_config.get("standards", []):
            try:
                standards.append(ComplianceStandard(std_str))
            except ValueError:
                logger.warning(f"Unknown compliance standard: {std_str}")
        
        return cls(standards=standards, governance_client=governance_client)
    
    def collect_evidence(
        self,
        requirement_id: str,
        evidence_type: str,
        data: Dict[str, Any],
        auto_verify: bool = False
    ) -> ComplianceEvidence:
        """
        증거 수집
        
        Args:
            requirement_id: 요구사항 ID
            evidence_type: 증거 유형
            data: 증거 데이터
            auto_verify: 자동 검증 여부
            
        Returns:
            ComplianceEvidence 객체
        """
        evidence = ComplianceEvidence(
            evidence_id=str(uuid.uuid4())[:8],
            requirement_id=requirement_id,
            collected_at=datetime.now().isoformat(),
            evidence_type=evidence_type,
            data=data,
            verified=auto_verify,
        )
        
        self.evidence.append(evidence)
        logger.info(f"Collected evidence for {requirement_id}: {evidence_type}")
        
        return evidence
    
    def verify_requirement(
        self,
        requirement: ComplianceRequirement,
        test_results: Dict[str, Any]
    ) -> ComplianceResult:
        """
        요구사항 검증
        
        Args:
            requirement: 검증할 요구사항
            test_results: 테스트 결과 데이터
            
        Returns:
            ComplianceResult 객체
        """
        passed = True
        notes = []
        
        for key, expected in requirement.pass_criteria.items():
            actual = test_results.get(key)
            
            if actual is None:
                passed = False
                notes.append(f"Missing: {key}")
                continue
            
            # 불리언 비교
            if isinstance(expected, bool):
                if actual != expected:
                    passed = False
                    notes.append(f"{key}: expected {expected}, got {actual}")
            
            # 숫자 비교
            elif isinstance(expected, (int, float)):
                if isinstance(actual, (int, float)):
                    # 키 이름에 따른 비교 방향
                    if any(kw in key for kw in ["max_", "minutes", "hours", "time"]):
                        # 최대값: 작을수록 좋음
                        if actual > expected:
                            passed = False
                            notes.append(f"{key}: {actual} > {expected} (max)")
                    elif any(kw in key for kw in ["min_", "percentage", "rate"]):
                        # 최소값: 클수록 좋음
                        if actual < expected:
                            passed = False
                            notes.append(f"{key}: {actual} < {expected} (min)")
                    else:
                        # 기본: 같거나 작음
                        if actual > expected:
                            passed = False
                            notes.append(f"{key}: {actual} > {expected}")
            
            # 리스트 비교 (포함 관계)
            elif isinstance(expected, list):
                actual_list = test_results.get(key, [])
                if not all(item in actual_list for item in expected):
                    passed = False
                    missing = set(expected) - set(actual_list)
                    notes.append(f"{key}: missing {missing}")
            
            # 문자열 비교
            elif isinstance(expected, str):
                if actual != expected:
                    passed = False
                    notes.append(f"{key}: expected '{expected}', got '{actual}'")
        
        # 관련 증거 수집
        related_evidence = [
            e for e in self.evidence
            if e.requirement_id == requirement.requirement_id
        ]
        
        result = ComplianceResult(
            requirement=requirement,
            passed=passed,
            evidence=related_evidence,
            checked_at=datetime.now().isoformat(),
            notes="; ".join(notes) if notes else "All criteria met",
            remediation=self._suggest_remediation(requirement) if not passed else None,
        )
        
        self.results.append(result)
        return result
    
    def _suggest_remediation(self, requirement: ComplianceRequirement) -> str:
        """실패한 요구사항에 대한 개선 방안 제안"""
        suggestions = {
            "DORA-17.1": "Chaos 테스트 파이프라인을 구축하고 정기적으로 실행하세요.",
            "DORA-17.2": "다양한 장애 시나리오 (네트워크, DB, API)를 추가하세요.",
            "DORA-25.1": "인시던트 대응 플레이북을 작성하고 알림 채널을 테스트하세요.",
            "PCI-12.10.1": "인시던트 대응 훈련을 실시하고 대응 시간을 단축하세요.",
            "PCI-11.3.1": "취약점 스캐너를 도입하고 Critical 취약점을 즉시 패치하세요.",
            "SOC2-A1.1": "고가용성 아키텍처를 검토하고 장애 조치 계획을 개선하세요.",
            "SOC2-CC6.1": "변경 관리 프로세스를 문서화하고 승인 워크플로우를 도입하세요.",
        }
        
        return suggestions.get(
            requirement.requirement_id,
            f"{requirement.description}에 대한 개선 조치를 수행하세요."
        )
    
    def verify_all(
        self,
        test_results: Dict[str, Any]
    ) -> ComplianceReport:
        """
        모든 요구사항 검증
        
        Args:
            test_results: 전체 테스트 결과 데이터
            
        Returns:
            ComplianceReport 객체
        """
        self.results = []
        
        for requirement in self.requirements:
            self.verify_requirement(requirement, test_results)
        
        overall_passed = all(r.passed for r in self.results)
        
        # 인증 문구 생성
        if overall_passed:
            cert_statement = (
                f"본 시스템은 {', '.join(s.value for s in self.standards)} 규제 요구사항을 "
                f"모두 충족함을 확인합니다. 검증일: {datetime.now().strftime('%Y-%m-%d')}"
            )
        else:
            failed_count = sum(1 for r in self.results if not r.passed)
            cert_statement = (
                f"본 시스템은 일부 규제 요구사항({failed_count}건)을 충족하지 못했습니다. "
                f"개선 조치가 필요합니다."
            )
        
        # 다음 감사 일정 계산 (분기별)
        next_audit = datetime.now() + timedelta(days=90)
        
        return ComplianceReport(
            report_id=f"compliance-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            standards=self.standards,
            results=self.results,
            overall_passed=overall_passed,
            certification_statement=cert_statement,
            next_audit_date=next_audit.strftime("%Y-%m-%d"),
        )
    
    def check_dora_compliance(
        self,
        test_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        DORA 2025 특화 검증
        
        Args:
            test_results: 테스트 결과
            
        Returns:
            DORA 준수 상태
        """
        dora_reqs = ComplianceMapper.DORA_REQUIREMENTS
        dora_results = []
        
        for req in dora_reqs:
            result = self.verify_requirement(req, test_results)
            dora_results.append(result)
        
        passed_count = sum(1 for r in dora_results if r.passed)
        
        return {
            "standard": "DORA_2025",
            "total_requirements": len(dora_reqs),
            "passed": passed_count,
            "failed": len(dora_reqs) - passed_count,
            "compliance_rate": passed_count / len(dora_reqs) * 100,
            "is_compliant": passed_count == len(dora_reqs),
            "critical_failures": [
                r.requirement.requirement_id for r in dora_results
                if not r.passed and r.requirement.severity == "critical"
            ],
        }


# =============================================================================
# DNA 검증 확장
# =============================================================================

def validate_compliance_dna(compliance_config: Dict[str, Any]) -> List[str]:
    """
    Compliance DNA 설정 검증
    
    Args:
        compliance_config: STAGE_DNA["compliance"] 딕셔너리
        
    Returns:
        경고 메시지 목록
    """
    warnings = []
    
    # 필수 필드 체크
    if "standards" not in compliance_config:
        warnings.append("standards 설정 필요 (예: ['DORA_2025', 'PCI-DSS_4.0'])")
    
    # 규제 표준 검증
    valid_standards = {s.value for s in ComplianceStandard}
    for std in compliance_config.get("standards", []):
        if std not in valid_standards:
            warnings.append(f"Unknown standard: {std}. Valid: {valid_standards}")
    
    # DORA 특화 검증
    if "DORA_2025" in compliance_config.get("standards", []):
        dora_config = compliance_config.get("dora", {})
        if not dora_config.get("resilience_test_frequency"):
            warnings.append("DORA: resilience_test_frequency 설정 권장")
        if not dora_config.get("incident_reporting_hours"):
            warnings.append("DORA: incident_reporting_hours 설정 권장 (최대 72)")
    
    # PCI-DSS 특화 검증
    if "PCI-DSS_4.0" in compliance_config.get("standards", []):
        pci_config = compliance_config.get("pci_dss", {})
        if not pci_config.get("data_encryption_required", True):
            warnings.append("PCI-DSS: data_encryption_required는 True여야 함")
    
    return warnings


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Compliance DNA - 규제 준수 검증"
    )
    parser.add_argument(
        "--standards",
        nargs="+",
        default=["DORA_2025"],
        help="검증할 규제 표준",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="리포트 출력 파일",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="출력 형식",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="시뮬레이션 데이터로 테스트",
    )
    
    args = parser.parse_args()
    
    # 규제 표준 파싱
    standards = []
    for std_str in args.standards:
        try:
            standards.append(ComplianceStandard(std_str))
        except ValueError:
            print(f"Warning: Unknown standard '{std_str}'")
    
    verifier = ComplianceVerifier(standards=standards)
    
    # 시뮬레이션 데이터
    if args.simulate:
        test_results = {
            "chaos_test_completed": True,
            "recovery_time_minutes": 60,
            "scenarios_tested": 6,
            "failure_modes_covered": ["network", "database", "external_api"],
            "test_frequency_days": 30,
            "last_test_within_period": True,
            "incident_response_plan_exists": True,
            "notification_channel_tested": True,
            "max_notification_hours": 24,
            "third_party_monitoring_enabled": True,
            "sla_tracking": True,
            "availability_percentage": 99.95,
            "max_unplanned_downtime_minutes": 300,
            "change_management_enabled": True,
            "approval_workflow_exists": True,
            "rollback_capability": True,
            "vulnerability_scan_completed": True,
            "critical_vulnerabilities": 0,
            "high_vulnerabilities_remediated": True,
            "data_encryption_enabled": True,
            "encryption_algorithm": "AES-256",
        }
    else:
        # 실제 데이터 수집 로직
        test_results = {}
    
    report = verifier.verify_all(test_results)
    
    # 출력
    if args.format == "json":
        output = report.to_json()
    else:
        output = report.to_markdown()
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Report saved to {args.output}")
    else:
        print(output)
    
    # 실패 시 exit code 1
    if not report.overall_passed:
        sys.exit(1)


if __name__ == "__main__":
    import sys
    main()

"""
Compliance DNA Models - 규정 준수 관련 데이터 모델
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class ComplianceStandard(Enum):
    """규정 표준"""
    DORA_2025 = "DORA_2025"       # 디지털 운영 복원력 법
    PCI_DSS = "PCI-DSS"          # 결제 카드 산업 데이터 보안 표준
    SOC2 = "SOC2"                # 서비스 조직 통제
    GDPR = "GDPR"                # 일반 데이터 보호 규정
    HIPAA = "HIPAA"              # 건강보험 이동성 및 책임법
    ISO27001 = "ISO27001"        # 정보보안 관리
    CUSTOM = "CUSTOM"            # 사용자 정의


class ViolationSeverity(Enum):
    """위반 심각도"""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ComplianceCheck:
    """규정 준수 검사 항목"""
    check_id: str
    name: str
    description: str
    standard: ComplianceStandard
    category: str = "general"
    required: bool = True
    enabled: bool = True
    check_function: Optional[str] = None  # 검사 함수 이름
    parameters: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "description": self.description,
            "standard": self.standard.value,
            "category": self.category,
            "required": self.required,
            "enabled": self.enabled,
            "check_function": self.check_function,
            "parameters": self.parameters,
        }


@dataclass
class ComplianceViolation:
    """규정 위반"""
    violation_id: str
    check_id: str
    stage_name: str
    standard: ComplianceStandard
    severity: ViolationSeverity
    message: str
    details: str = ""
    remediation: str = ""
    detected_at: datetime = field(default_factory=datetime.now)
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "violation_id": self.violation_id,
            "check_id": self.check_id,
            "stage_name": self.stage_name,
            "standard": self.standard.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "remediation": self.remediation,
            "detected_at": self.detected_at.isoformat(),
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


@dataclass
class ComplianceReport:
    """규정 준수 리포트"""
    report_id: str
    stage_name: str
    standards: List[ComplianceStandard]
    generated_at: datetime = field(default_factory=datetime.now)
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    violations: List[ComplianceViolation] = field(default_factory=list)
    compliance_score: float = 100.0  # 0-100%
    
    @property
    def is_compliant(self) -> bool:
        """규정 준수 여부"""
        return len([v for v in self.violations if not v.resolved]) == 0
    
    def to_dict(self) -> Dict:
        return {
            "report_id": self.report_id,
            "stage_name": self.stage_name,
            "standards": [s.value for s in self.standards],
            "generated_at": self.generated_at.isoformat(),
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "violations": [v.to_dict() for v in self.violations],
            "compliance_score": self.compliance_score,
            "is_compliant": self.is_compliant,
        }

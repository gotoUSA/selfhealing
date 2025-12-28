# DNA Enterprise Features: FinOps, Compliance, Self-Learning

📅 **작성일**: 2025-12-29  
🎯 **목적**: 대기업 M&A Exit을 위한 엔터프라이즈급 DNA 기능  
📋 **버전**: v1.0.0  
📌 **관련 문서**: [29_STAGE_DNA_EVOLUTION_MASTER.md](29_STAGE_DNA_EVOLUTION_MASTER.md)  
💰 **목표 가치**: CFO + CTO + Compliance 동시 만족

---

## 1. FinOps DNA (Phase 2)

### 1.1 문제 정의

대기업이 자가 치유를 도입할 때 가장 두려워하는 것:

```
┌─────────────────────────────────────────────────────────────────┐
│                    FinOps 위험 시나리오                          │
├─────────────────────────────────────────────────────────────────┤
│ 상황: DLQ에 10만 건의 실패 메시지 적재                          │
│                                                                 │
│ 무제한 복구 시:                                                 │
│   - 서버 100대 Auto-Scale → $5,000/시간                        │
│   - 외부 API 호출 폭증 → $10,000 추가 비용                     │
│   - 총 복구 비용: $15,000 (손해 > 이익)                        │
│                                                                 │
│ FinOps DNA 적용 시:                                             │
│   - 복구 예산 상한: $1,000                                      │
│   - 비용 초과 시 Graceful Degradation                          │
│   - ROI 계산 후 복구 우선순위 조정                              │
│   - 총 비용: $800 (경제적 복구)                                │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 DNA 스키마

```python
STAGE_DNA = {
    "name": "Stage XX - Payment Processing",
    "type": "integration",
    
    # FinOps DNA 설정
    "finops": {
        # 복구 비용 상한
        "max_recovery_budget": 1000.00,      # USD
        "budget_currency": "USD",
        
        # 단위 비용
        "cost_per_retry": 0.001,             # 재시도 1회당
        "cost_per_api_call": 0.01,           # 외부 API 호출당
        "cost_per_compute_minute": 0.05,     # 컴퓨팅 1분당
        
        # 예산 초과 시 동작
        "on_budget_exceeded": "graceful_degradation",  # or "hard_stop", "alert_only"
        
        # ROI 기반 우선순위
        "recovery_priority": {
            "payment": {"value": 100, "priority": 1},     # 고가치
            "notification": {"value": 1, "priority": 3},  # 저가치
            "logging": {"value": 0.1, "priority": 5},     # 최저
        },
        
        # 비용 최적화 설정
        "optimization": {
            "batch_size": 100,          # 배치 처리로 비용 절감
            "off_peak_recovery": True,  # 피크 외 시간에 복구
            "spot_instance_ok": True,   # 스팟 인스턴스 사용 허용
        },
    },
}
```

### 1.3 구현 코드

```python
# load_tests/utils/selfhealing/dna_finops.py
"""
FinOps DNA - 복구 비용 최적화

기능:
1. 복구 비용 실시간 추적
2. 예산 상한 자동 적용
3. ROI 기반 우선순위
4. Graceful Degradation
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import threading
import logging

logger = logging.getLogger(__name__)


class BudgetExceededAction(Enum):
    """예산 초과 시 동작"""
    HARD_STOP = "hard_stop"                    # 즉시 중단
    GRACEFUL_DEGRADATION = "graceful_degradation"  # 점진적 축소
    ALERT_ONLY = "alert_only"                  # 알림만
    PRIORITY_BASED = "priority_based"          # 우선순위 기반 선별


@dataclass
class CostItem:
    """비용 항목"""
    item_type: str
    unit_cost: float
    quantity: int
    total_cost: float
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryBudget:
    """복구 예산"""
    max_budget: float
    currency: str = "USD"
    spent: float = 0.0
    items: List[CostItem] = field(default_factory=list)
    
    @property
    def remaining(self) -> float:
        return max(0, self.max_budget - self.spent)
    
    @property
    def utilization(self) -> float:
        if self.max_budget == 0:
            return 0.0
        return (self.spent / self.max_budget) * 100
    
    @property
    def is_exceeded(self) -> bool:
        return self.spent >= self.max_budget


@dataclass
class RecoveryItem:
    """복구 대상 아이템"""
    item_id: str
    item_type: str
    estimated_cost: float
    business_value: float
    priority: int
    
    @property
    def roi(self) -> float:
        """ROI (투자 대비 수익)"""
        if self.estimated_cost == 0:
            return float('inf')
        return self.business_value / self.estimated_cost


class FinOpsController:
    """FinOps 컨트롤러"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.budget = RecoveryBudget(
            max_budget=config.get("max_recovery_budget", 1000.0),
            currency=config.get("budget_currency", "USD"),
        )
        
        self.cost_rates = {
            "retry": config.get("cost_per_retry", 0.001),
            "api_call": config.get("cost_per_api_call", 0.01),
            "compute_minute": config.get("cost_per_compute_minute", 0.05),
        }
        
        self.on_exceeded = BudgetExceededAction(
            config.get("on_budget_exceeded", "graceful_degradation")
        )
        
        self.priority_config = config.get("recovery_priority", {})
        self._lock = threading.Lock()
    
    def record_cost(
        self,
        item_type: str,
        quantity: int = 1,
        metadata: Dict[str, Any] = None
    ) -> Tuple[bool, float]:
        """비용 기록"""
        unit_cost = self.cost_rates.get(item_type, 0.01)
        total_cost = unit_cost * quantity
        
        with self._lock:
            # 예산 체크
            if self.budget.spent + total_cost > self.budget.max_budget:
                return self._handle_budget_exceeded(total_cost)
            
            # 비용 기록
            item = CostItem(
                item_type=item_type,
                unit_cost=unit_cost,
                quantity=quantity,
                total_cost=total_cost,
                timestamp=datetime.now().isoformat(),
                metadata=metadata or {},
            )
            
            self.budget.items.append(item)
            self.budget.spent += total_cost
            
            return True, total_cost
    
    def _handle_budget_exceeded(
        self,
        attempted_cost: float
    ) -> Tuple[bool, float]:
        """예산 초과 처리"""
        logger.warning(
            f"Budget exceeded! Attempted: ${attempted_cost:.4f}, "
            f"Remaining: ${self.budget.remaining:.4f}"
        )
        
        if self.on_exceeded == BudgetExceededAction.HARD_STOP:
            raise BudgetExceededError(
                f"Recovery budget exceeded: ${self.budget.spent:.2f} / ${self.budget.max_budget:.2f}"
            )
        
        elif self.on_exceeded == BudgetExceededAction.GRACEFUL_DEGRADATION:
            # 남은 예산으로 부분 처리
            if self.budget.remaining > 0:
                partial = self.budget.remaining
                self.budget.spent = self.budget.max_budget
                return True, partial
            return False, 0.0
        
        elif self.on_exceeded == BudgetExceededAction.ALERT_ONLY:
            # 알림만 보내고 계속 진행
            self._send_budget_alert()
            self.budget.spent += attempted_cost
            return True, attempted_cost
        
        return False, 0.0
    
    def _send_budget_alert(self):
        """예산 초과 알림"""
        logger.critical(
            f"[FINOPS ALERT] Budget exceeded!\n"
            f"  Max Budget: ${self.budget.max_budget:.2f}\n"
            f"  Current Spent: ${self.budget.spent:.2f}\n"
            f"  Utilization: {self.budget.utilization:.1f}%"
        )
    
    def prioritize_recovery(
        self,
        items: List[RecoveryItem]
    ) -> List[RecoveryItem]:
        """ROI 기반 복구 우선순위 정렬"""
        # ROI 높은 순으로 정렬
        sorted_items = sorted(items, key=lambda x: (-x.roi, x.priority))
        
        # 예산 내에서 처리 가능한 항목 필터링
        affordable = []
        remaining = self.budget.remaining
        
        for item in sorted_items:
            if item.estimated_cost <= remaining:
                affordable.append(item)
                remaining -= item.estimated_cost
        
        return affordable
    
    def estimate_recovery_cost(
        self,
        dlq_count: int,
        avg_retries: int = 3,
        external_api_rate: float = 0.5
    ) -> Dict[str, float]:
        """복구 비용 예측"""
        retry_cost = dlq_count * avg_retries * self.cost_rates["retry"]
        api_cost = dlq_count * external_api_rate * self.cost_rates["api_call"]
        compute_cost = (dlq_count / 100) * self.cost_rates["compute_minute"]  # 100건당 1분
        
        total = retry_cost + api_cost + compute_cost
        
        return {
            "retry_cost": retry_cost,
            "api_cost": api_cost,
            "compute_cost": compute_cost,
            "total_estimated": total,
            "within_budget": total <= self.budget.remaining,
            "budget_utilization_after": (self.budget.spent + total) / self.budget.max_budget * 100,
        }
    
    def get_cost_report(self) -> Dict[str, Any]:
        """비용 리포트 생성"""
        # 타입별 집계
        by_type = {}
        for item in self.budget.items:
            if item.item_type not in by_type:
                by_type[item.item_type] = {"count": 0, "cost": 0.0}
            by_type[item.item_type]["count"] += item.quantity
            by_type[item.item_type]["cost"] += item.total_cost
        
        return {
            "summary": {
                "max_budget": self.budget.max_budget,
                "spent": self.budget.spent,
                "remaining": self.budget.remaining,
                "utilization": self.budget.utilization,
                "currency": self.budget.currency,
            },
            "by_type": by_type,
            "item_count": len(self.budget.items),
            "is_exceeded": self.budget.is_exceeded,
        }


class BudgetExceededError(Exception):
    """예산 초과 예외"""
    pass


# =============================================================================
# DNA 검증 확장
# =============================================================================

def validate_finops_dna(finops_config: Dict[str, Any]) -> List[str]:
    """FinOps DNA 검증"""
    warnings = []
    
    # 필수 필드 체크
    if "max_recovery_budget" not in finops_config:
        warnings.append("max_recovery_budget 설정 필요")
    
    budget = finops_config.get("max_recovery_budget", 0)
    if budget <= 0:
        warnings.append("max_recovery_budget는 양수여야 함")
    
    # 비용 설정 검증
    for cost_key in ["cost_per_retry", "cost_per_api_call"]:
        if cost_key in finops_config and finops_config[cost_key] < 0:
            warnings.append(f"{cost_key}는 음수일 수 없음")
    
    # 우선순위 검증
    priority = finops_config.get("recovery_priority", {})
    for item, config in priority.items():
        if "value" not in config:
            warnings.append(f"{item}의 value 설정 필요")
        if "priority" not in config:
            warnings.append(f"{item}의 priority 설정 필요")
    
    return warnings


# =============================================================================
# 리포트 생성
# =============================================================================

def generate_finops_report_section(controller: FinOpsController) -> str:
    """FinOps 리포트 섹션 생성"""
    report = controller.get_cost_report()
    
    lines = [
        "",
        "## 💰 FinOps Recovery Cost Report",
        "",
        "### Budget Summary",
        "",
        "| 항목 | 값 |",
        "|-----|-----|",
        f"| Max Budget | ${report['summary']['max_budget']:.2f} |",
        f"| Spent | ${report['summary']['spent']:.2f} |",
        f"| Remaining | ${report['summary']['remaining']:.2f} |",
        f"| Utilization | {report['summary']['utilization']:.1f}% |",
        f"| Status | {'⚠️ EXCEEDED' if report['is_exceeded'] else '✅ OK'} |",
        "",
        "### Cost by Type",
        "",
        "| Type | Count | Cost |",
        "|-----|------|------|",
    ]
    
    for item_type, data in report["by_type"].items():
        lines.append(f"| {item_type} | {data['count']} | ${data['cost']:.4f} |")
    
    return "\n".join(lines)
```

---

## 2. Compliance DNA (Phase 2)

### 2.1 규제 요구사항

```
┌─────────────────────────────────────────────────────────────────┐
│                    2025년 주요 규제                              │
├─────────────────────────────────────────────────────────────────┤
│ DORA (Digital Operational Resilience Act)                       │
│   - EU 금융권 필수                                              │
│   - 제17조: 회복력 테스트 의무화                                │
│   - 제25조: 장애 보고 72시간 내 의무                            │
│                                                                 │
│ PCI-DSS 4.0                                                     │
│   - 결제 시스템 필수                                            │
│   - 요구사항 12.10: 인시던트 대응 테스트                        │
│   - 요구사항 11.3: 취약점 스캔                                  │
│                                                                 │
│ SOC 2 Type II                                                   │
│   - 클라우드 서비스 필수                                        │
│   - 가용성 원칙: 99.9% SLA 증명                                │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 DNA 스키마

```python
STAGE_DNA = {
    "name": "Stage XX - Payment Processing",
    "type": "integration",
    
    # Compliance DNA 설정
    "compliance": {
        # 적용 규제
        "standards": ["DORA_2025", "PCI-DSS_4.0", "SOC2_TYPE2"],
        
        # DORA 관련
        "dora": {
            "resilience_test_frequency": "quarterly",
            "incident_reporting_hours": 72,
            "recovery_time_objective_minutes": 120,
            "third_party_monitoring": True,
        },
        
        # PCI-DSS 관련
        "pci_dss": {
            "data_encryption_required": True,
            "vulnerability_scan_frequency": "monthly",
            "penetration_test_frequency": "annual",
            "incident_response_test": True,
        },
        
        # SOC 2 관련
        "soc2": {
            "availability_sla": 0.999,
            "change_management_required": True,
            "audit_trail_enabled": True,
        },
        
        # 자동 증명 설정
        "auto_certification": {
            "enabled": True,
            "output_format": "pdf",
            "include_evidence": True,
        },
    },
}
```

### 2.3 구현 코드

```python
# load_tests/utils/selfhealing/dna_compliance.py
"""
Compliance DNA - 규제 준수 자동 증명

기능:
1. DORA, PCI-DSS, SOC2 규제 매핑
2. 테스트 결과 → 규제 요구사항 매핑
3. 자동 Compliance 리포트 생성
4. 감사 증거 수집
"""

from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger(__name__)


class ComplianceStandard(Enum):
    """규제 표준"""
    DORA_2025 = "DORA_2025"
    PCI_DSS_4_0 = "PCI-DSS_4.0"
    SOC2_TYPE2 = "SOC2_TYPE2"
    ISO_27001 = "ISO_27001"
    HIPAA = "HIPAA"
    GDPR = "GDPR"


@dataclass
class ComplianceRequirement:
    """규제 요구사항"""
    standard: ComplianceStandard
    requirement_id: str
    description: str
    test_type: str  # resilience, security, availability, audit
    pass_criteria: Dict[str, Any]
    evidence_required: List[str]


@dataclass
class ComplianceEvidence:
    """규제 준수 증거"""
    requirement_id: str
    collected_at: str
    evidence_type: str
    data: Dict[str, Any]
    verified: bool = False


@dataclass
class ComplianceResult:
    """규제 준수 결과"""
    standard: ComplianceStandard
    requirement_id: str
    passed: bool
    evidence: List[ComplianceEvidence]
    notes: str = ""


@dataclass
class ComplianceReport:
    """Compliance 리포트"""
    report_id: str
    generated_at: str
    standards: List[ComplianceStandard]
    results: List[ComplianceResult]
    overall_passed: bool
    certification_statement: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "standards": [s.value for s in self.standards],
            "summary": {
                "total_requirements": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "failed": sum(1 for r in self.results if not r.passed),
            },
            "overall_passed": self.overall_passed,
            "certification_statement": self.certification_statement,
        }


class ComplianceMapper:
    """규제 요구사항 매퍼"""
    
    # DORA 2025 요구사항
    DORA_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-17.1",
            description="ICT 시스템 회복력 테스트 수행",
            test_type="resilience",
            pass_criteria={
                "chaos_test_completed": True,
                "recovery_time_minutes": 120,
            },
            evidence_required=["chaos_test_report", "recovery_time_log"],
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-17.2",
            description="시나리오 기반 테스트 수행",
            test_type="resilience",
            pass_criteria={
                "scenarios_tested": 5,
                "failure_modes_covered": ["network", "db", "external_api"],
            },
            evidence_required=["scenario_list", "test_results"],
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.DORA_2025,
            requirement_id="DORA-25.1",
            description="중대 인시던트 72시간 내 보고 체계",
            test_type="audit",
            pass_criteria={
                "incident_response_plan_exists": True,
                "notification_channel_tested": True,
            },
            evidence_required=["incident_response_plan", "notification_test_log"],
        ),
    ]
    
    # PCI-DSS 4.0 요구사항
    PCI_DSS_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-12.10.1",
            description="인시던트 대응 계획 테스트",
            test_type="security",
            pass_criteria={
                "incident_response_tested": True,
                "response_time_minutes": 30,
            },
            evidence_required=["incident_response_test_report"],
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.PCI_DSS_4_0,
            requirement_id="PCI-11.3.1",
            description="내부 취약점 스캔",
            test_type="security",
            pass_criteria={
                "vulnerability_scan_completed": True,
                "critical_vulnerabilities": 0,
            },
            evidence_required=["vulnerability_scan_report"],
        ),
    ]
    
    # SOC 2 Type II 요구사항
    SOC2_REQUIREMENTS = [
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-A1.1",
            description="가용성 SLA 달성",
            test_type="availability",
            pass_criteria={
                "availability_percentage": 99.9,
                "unplanned_downtime_minutes": 43.2,  # 99.9% = 연 ~8.76시간
            },
            evidence_required=["uptime_report", "incident_log"],
        ),
        ComplianceRequirement(
            standard=ComplianceStandard.SOC2_TYPE2,
            requirement_id="SOC2-CC6.1",
            description="변경 관리 프로세스",
            test_type="audit",
            pass_criteria={
                "change_management_enabled": True,
                "approval_workflow_exists": True,
            },
            evidence_required=["change_log", "approval_records"],
        ),
    ]
    
    @classmethod
    def get_requirements(
        cls,
        standards: List[ComplianceStandard]
    ) -> List[ComplianceRequirement]:
        """규제별 요구사항 조회"""
        requirements = []
        
        for standard in standards:
            if standard == ComplianceStandard.DORA_2025:
                requirements.extend(cls.DORA_REQUIREMENTS)
            elif standard == ComplianceStandard.PCI_DSS_4_0:
                requirements.extend(cls.PCI_DSS_REQUIREMENTS)
            elif standard == ComplianceStandard.SOC2_TYPE2:
                requirements.extend(cls.SOC2_REQUIREMENTS)
        
        return requirements


class ComplianceVerifier:
    """규제 준수 검증기"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.standards = [
            ComplianceStandard(s) for s in config.get("standards", [])
        ]
        self.requirements = ComplianceMapper.get_requirements(self.standards)
        self.evidence: List[ComplianceEvidence] = []
        self.results: List[ComplianceResult] = []
    
    def collect_evidence(
        self,
        requirement_id: str,
        evidence_type: str,
        data: Dict[str, Any]
    ):
        """증거 수집"""
        evidence = ComplianceEvidence(
            requirement_id=requirement_id,
            collected_at=datetime.now().isoformat(),
            evidence_type=evidence_type,
            data=data,
        )
        self.evidence.append(evidence)
        logger.info(f"Collected evidence for {requirement_id}: {evidence_type}")
    
    def verify_requirement(
        self,
        requirement: ComplianceRequirement,
        test_results: Dict[str, Any]
    ) -> ComplianceResult:
        """요구사항 검증"""
        passed = True
        
        for key, expected in requirement.pass_criteria.items():
            actual = test_results.get(key)
            
            if isinstance(expected, bool):
                if actual != expected:
                    passed = False
            elif isinstance(expected, (int, float)):
                if isinstance(actual, (int, float)):
                    if key.endswith("minutes") or key.endswith("time"):
                        # 시간은 작을수록 좋음
                        if actual > expected:
                            passed = False
                    elif key.endswith("percentage"):
                        # 퍼센트는 클수록 좋음
                        if actual < expected:
                            passed = False
                    else:
                        # 기본: 같거나 작음
                        if actual > expected:
                            passed = False
            elif isinstance(expected, list):
                if not all(item in test_results.get(key, []) for item in expected):
                    passed = False
        
        # 관련 증거 수집
        related_evidence = [
            e for e in self.evidence
            if e.requirement_id == requirement.requirement_id
        ]
        
        result = ComplianceResult(
            standard=requirement.standard,
            requirement_id=requirement.requirement_id,
            passed=passed,
            evidence=related_evidence,
            notes=f"Verified against: {requirement.description}",
        )
        
        self.results.append(result)
        return result
    
    def verify_all(
        self,
        test_results: Dict[str, Any]
    ) -> List[ComplianceResult]:
        """모든 요구사항 검증"""
        for requirement in self.requirements:
            self.verify_requirement(requirement, test_results)
        
        return self.results
    
    def generate_report(self) -> ComplianceReport:
        """Compliance 리포트 생성"""
        all_passed = all(r.passed for r in self.results)
        
        # 인증 문구 생성
        if all_passed:
            cert_statement = (
                f"본 테스트 결과는 {', '.join(s.value for s in self.standards)} "
                f"규제 요구사항을 충족함을 증명합니다. "
                f"검증일: {datetime.now().strftime('%Y-%m-%d')}"
            )
        else:
            failed = [r.requirement_id for r in self.results if not r.passed]
            cert_statement = (
                f"다음 요구사항이 충족되지 않았습니다: {', '.join(failed)}. "
                f"추가 조치가 필요합니다."
            )
        
        return ComplianceReport(
            report_id=f"COMP-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            standards=self.standards,
            results=self.results,
            overall_passed=all_passed,
            certification_statement=cert_statement,
        )


# =============================================================================
# 리포트 섹션 생성
# =============================================================================

def generate_compliance_report_section(verifier: ComplianceVerifier) -> str:
    """Compliance 리포트 섹션 생성"""
    report = verifier.generate_report()
    
    lines = [
        "",
        "## ⚖️ Compliance Verification Report",
        "",
        f"**Report ID**: {report.report_id}",
        f"**Generated**: {report.generated_at}",
        f"**Standards**: {', '.join(s.value for s in report.standards)}",
        "",
        "### Results Summary",
        "",
        "| Requirement | Standard | Status |",
        "|-------------|----------|--------|",
    ]
    
    for result in report.results:
        status = "✅ PASS" if result.passed else "❌ FAIL"
        lines.append(f"| {result.requirement_id} | {result.standard.value} | {status} |")
    
    lines.extend([
        "",
        "### Certification Statement",
        "",
        f"> {report.certification_statement}",
        "",
    ])
    
    if report.overall_passed:
        lines.append("**Overall Status**: ✅ **COMPLIANT**")
    else:
        lines.append("**Overall Status**: ❌ **NON-COMPLIANT** - 조치 필요")
    
    return "\n".join(lines)
```

---

## 3. Self-Learning DNA (Phase 3)

### 3.1 개념

```
┌─────────────────────────────────────────────────────────────────┐
│                    Self-Learning DNA 동작 흐름                   │
├─────────────────────────────────────────────────────────────────┤
│ 1. 테스트 결과 수집 (5회 이상)                                  │
│    ↓                                                            │
│ 2. 패턴 분석                                                    │
│    - SLA 위반 빈도                                              │
│    - 복구 시간 추이                                             │
│    - 에러 유형 분포                                             │
│    ↓                                                            │
│ 3. 자동 제안 생성                                               │
│    "지난 5번의 테스트 결과, 현재의 SLA_CRITICAL(500ms)은        │
│    너무 느립니다. 420ms로 하향 조정할 것을 추천합니다."         │
│    ↓                                                            │
│ 4. DNA 자동 업데이트 (승인 시)                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 DNA 스키마

```python
STAGE_DNA = {
    "name": "Stage XX - Payment Processing",
    "type": "integration",
    
    # Self-Learning DNA 설정
    "learning": {
        "enabled": True,
        
        # 학습 설정
        "min_samples": 5,           # 최소 샘플 수
        "learning_rate": 0.1,       # 조정 비율
        "confidence_threshold": 0.8, # 제안 신뢰도 임계값
        
        # 자동 조정 대상
        "auto_tune_targets": [
            "p99_threshold_ms",
            "error_rate_threshold",
            "retry_count",
            "timeout_seconds",
        ],
        
        # 제안 모드
        "suggestion_mode": "auto_apply",  # or "manual_review", "alert_only"
        
        # 제한
        "max_adjustment_per_cycle": 0.2,  # 최대 20% 조정
        "cooldown_hours": 24,             # 조정 후 대기 시간
    },
}
```

### 3.3 구현 코드

```python
# load_tests/utils/selfhealing/dna_learning.py
"""
Self-Learning DNA - 자가 학습 및 최적화

기능:
1. 테스트 결과 기반 패턴 분석
2. SLA 임계값 자동 조정 제안
3. DNA 설정 자동 최적화
4. 성능 추이 예측
"""

from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
import statistics
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class TestSample:
    """테스트 샘플"""
    timestamp: str
    stage_name: str
    metrics: Dict[str, float]
    dna_config: Dict[str, Any]
    passed: bool


@dataclass
class LearningInsight:
    """학습 인사이트"""
    insight_type: str  # threshold, pattern, anomaly
    target_field: str
    current_value: Any
    suggested_value: Any
    confidence: float
    reasoning: str
    evidence: List[Dict[str, Any]]


@dataclass
class LearningReport:
    """학습 리포트"""
    analysis_timestamp: str
    samples_analyzed: int
    insights: List[LearningInsight]
    auto_applied: List[str]
    pending_review: List[str]


class DNALearner:
    """DNA 자가 학습기"""
    
    def __init__(
        self,
        config: Dict[str, Any],
        max_history: int = 100
    ):
        self.config = config
        self.enabled = config.get("enabled", True)
        self.min_samples = config.get("min_samples", 5)
        self.learning_rate = config.get("learning_rate", 0.1)
        self.confidence_threshold = config.get("confidence_threshold", 0.8)
        self.max_adjustment = config.get("max_adjustment_per_cycle", 0.2)
        
        self.history: deque = deque(maxlen=max_history)
        self.insights: List[LearningInsight] = []
        self.last_adjustment: Optional[datetime] = None
        self.cooldown_hours = config.get("cooldown_hours", 24)
    
    def record_sample(self, sample: TestSample):
        """테스트 샘플 기록"""
        self.history.append(sample)
        logger.debug(f"Recorded sample: {sample.stage_name} @ {sample.timestamp}")
    
    def analyze(self) -> LearningReport:
        """패턴 분석 및 인사이트 도출"""
        if len(self.history) < self.min_samples:
            return LearningReport(
                analysis_timestamp=datetime.now().isoformat(),
                samples_analyzed=len(self.history),
                insights=[],
                auto_applied=[],
                pending_review=[f"Insufficient samples ({len(self.history)}/{self.min_samples})"],
            )
        
        samples = list(self.history)
        self.insights = []
        
        # P99 분석
        p99_insight = self._analyze_p99(samples)
        if p99_insight:
            self.insights.append(p99_insight)
        
        # 에러율 분석
        error_insight = self._analyze_error_rate(samples)
        if error_insight:
            self.insights.append(error_insight)
        
        # 복구 시간 분석
        recovery_insight = self._analyze_recovery_time(samples)
        if recovery_insight:
            self.insights.append(recovery_insight)
        
        # 자동 적용 판단
        auto_applied = []
        pending_review = []
        
        for insight in self.insights:
            if insight.confidence >= self.confidence_threshold:
                if self._can_auto_apply():
                    auto_applied.append(insight.target_field)
                else:
                    pending_review.append(
                        f"{insight.target_field} (cooldown: {self.cooldown_hours}h)"
                    )
            else:
                pending_review.append(
                    f"{insight.target_field} (confidence: {insight.confidence:.1%})"
                )
        
        return LearningReport(
            analysis_timestamp=datetime.now().isoformat(),
            samples_analyzed=len(samples),
            insights=self.insights,
            auto_applied=auto_applied,
            pending_review=pending_review,
        )
    
    def _analyze_p99(
        self,
        samples: List[TestSample]
    ) -> Optional[LearningInsight]:
        """P99 레이턴시 분석"""
        p99_values = [
            s.metrics.get("p99", 0) for s in samples
            if "p99" in s.metrics
        ]
        
        if len(p99_values) < self.min_samples:
            return None
        
        # 현재 임계값
        current_threshold = samples[-1].dna_config.get("p99_threshold_ms", 300)
        
        # 통계 분석
        mean_p99 = statistics.mean(p99_values)
        stdev_p99 = statistics.stdev(p99_values) if len(p99_values) > 1 else 0
        
        # 추천값 계산 (평균 + 2*표준편차, 상한 20% 조정)
        suggested = mean_p99 + 2 * stdev_p99
        max_suggested = current_threshold * (1 + self.max_adjustment)
        min_suggested = current_threshold * (1 - self.max_adjustment)
        
        suggested = max(min(suggested, max_suggested), min_suggested)
        
        # 신뢰도 계산
        # 변동성이 낮을수록 신뢰도 높음
        cv = stdev_p99 / mean_p99 if mean_p99 > 0 else 0
        confidence = max(0, 1 - cv)
        
        # 유의미한 차이가 있는 경우만 제안
        if abs(suggested - current_threshold) < current_threshold * 0.05:
            return None
        
        reasoning = self._generate_reasoning(
            "p99_threshold_ms",
            current_threshold,
            suggested,
            mean_p99,
            stdev_p99,
            len(p99_values)
        )
        
        return LearningInsight(
            insight_type="threshold",
            target_field="p99_threshold_ms",
            current_value=current_threshold,
            suggested_value=round(suggested, 0),
            confidence=confidence,
            reasoning=reasoning,
            evidence=[
                {"mean": mean_p99, "stdev": stdev_p99, "samples": len(p99_values)}
            ],
        )
    
    def _analyze_error_rate(
        self,
        samples: List[TestSample]
    ) -> Optional[LearningInsight]:
        """에러율 분석"""
        error_rates = [
            s.metrics.get("error_rate", 0) for s in samples
            if "error_rate" in s.metrics
        ]
        
        if len(error_rates) < self.min_samples:
            return None
        
        current_threshold = samples[-1].dna_config.get("error_rate_threshold", 0.01)
        mean_error = statistics.mean(error_rates)
        
        # 에러율이 임계값의 50% 이하면 임계값 하향 제안
        if mean_error < current_threshold * 0.5:
            suggested = max(mean_error * 2, 0.001)  # 최소 0.1%
            
            return LearningInsight(
                insight_type="threshold",
                target_field="error_rate_threshold",
                current_value=current_threshold,
                suggested_value=round(suggested, 4),
                confidence=0.7,
                reasoning=(
                    f"평균 에러율({mean_error:.2%})이 현재 임계값({current_threshold:.2%})의 "
                    f"50% 미만입니다. 임계값을 {suggested:.2%}로 하향 조정하여 "
                    f"더 엄격한 품질 관리를 권장합니다."
                ),
                evidence=[{"mean_error_rate": mean_error, "samples": len(error_rates)}],
            )
        
        return None
    
    def _analyze_recovery_time(
        self,
        samples: List[TestSample]
    ) -> Optional[LearningInsight]:
        """복구 시간 분석"""
        recovery_times = [
            s.metrics.get("recovery_time_seconds", 0) for s in samples
            if "recovery_time_seconds" in s.metrics
        ]
        
        if len(recovery_times) < self.min_samples:
            return None
        
        mean_recovery = statistics.mean(recovery_times)
        max_recovery = max(recovery_times)
        
        # 복구 시간 트렌드 분석
        recent = recovery_times[-3:]
        older = recovery_times[:-3] if len(recovery_times) > 3 else []
        
        if older:
            trend = statistics.mean(recent) - statistics.mean(older)
            
            if trend > 0:  # 복구 시간 증가 추세
                return LearningInsight(
                    insight_type="pattern",
                    target_field="recovery_time",
                    current_value=mean_recovery,
                    suggested_value=None,
                    confidence=0.6,
                    reasoning=(
                        f"복구 시간이 증가 추세입니다. "
                        f"이전 평균: {statistics.mean(older):.1f}s → "
                        f"최근 평균: {statistics.mean(recent):.1f}s. "
                        f"인프라 최적화를 검토하세요."
                    ),
                    evidence=[
                        {"trend": trend, "recent_avg": statistics.mean(recent)}
                    ],
                )
        
        return None
    
    def _generate_reasoning(
        self,
        field: str,
        current: float,
        suggested: float,
        mean: float,
        stdev: float,
        sample_count: int
    ) -> str:
        """추론 근거 생성"""
        direction = "하향" if suggested < current else "상향"
        change = abs(suggested - current) / current * 100
        
        return (
            f"지난 {sample_count}번의 테스트 결과를 분석했습니다. "
            f"평균: {mean:.1f}ms, 표준편차: {stdev:.1f}ms. "
            f"현재 {field} 값 {current:.0f}ms을(를) "
            f"{suggested:.0f}ms로 {direction} 조정({change:.1f}% 변경)할 것을 권장합니다."
        )
    
    def _can_auto_apply(self) -> bool:
        """자동 적용 가능 여부"""
        if not self.last_adjustment:
            return True
        
        cooldown = timedelta(hours=self.cooldown_hours)
        return datetime.now() - self.last_adjustment >= cooldown
    
    def apply_suggestion(
        self,
        insight: LearningInsight,
        dna: Dict[str, Any]
    ) -> Dict[str, Any]:
        """제안 적용"""
        updated_dna = dna.copy()
        
        if insight.suggested_value is not None:
            updated_dna[insight.target_field] = insight.suggested_value
            self.last_adjustment = datetime.now()
            
            logger.info(
                f"Applied DNA update: {insight.target_field} "
                f"{insight.current_value} → {insight.suggested_value}"
            )
        
        return updated_dna
    
    def get_suggestions_summary(self) -> str:
        """제안 요약"""
        if not self.insights:
            return "현재 제안 사항이 없습니다."
        
        lines = ["## 🧠 Self-Learning Suggestions", ""]
        
        for insight in self.insights:
            icon = "🔴" if insight.confidence >= 0.8 else "🟡"
            lines.append(
                f"{icon} **{insight.target_field}**: "
                f"{insight.current_value} → {insight.suggested_value} "
                f"(신뢰도: {insight.confidence:.1%})"
            )
            lines.append(f"   {insight.reasoning}")
            lines.append("")
        
        return "\n".join(lines)


# =============================================================================
# 리포트 섹션 생성
# =============================================================================

def generate_learning_report_section(learner: DNALearner) -> str:
    """Self-Learning 리포트 섹션 생성"""
    report = learner.analyze()
    
    lines = [
        "",
        "## 🧠 Self-Learning DNA Report",
        "",
        f"**Analyzed Samples**: {report.samples_analyzed}",
        f"**Analysis Time**: {report.analysis_timestamp}",
        "",
    ]
    
    if report.insights:
        lines.extend([
            "### Insights & Suggestions",
            "",
            "| Field | Current | Suggested | Confidence | Status |",
            "|-------|---------|-----------|------------|--------|",
        ])
        
        for insight in report.insights:
            status = "Auto-Applied" if insight.target_field in report.auto_applied else "Pending"
            lines.append(
                f"| {insight.target_field} | {insight.current_value} | "
                f"{insight.suggested_value} | {insight.confidence:.1%} | {status} |"
            )
        
        lines.extend([
            "",
            "### Reasoning",
            "",
        ])
        
        for insight in report.insights:
            lines.append(f"- **{insight.target_field}**: {insight.reasoning}")
    else:
        lines.append("현재 제안 사항이 없습니다.")
    
    return "\n".join(lines)
```

---

## 4. 요약 및 통합

### 4.1 Enterprise DNA 전체 스키마

```python
STAGE_DNA = {
    # 기본 정보
    "name": "Stage XX - Enterprise Payment",
    "type": "platinum",
    "version": "2.0",
    
    # Core DNA
    "required_modules": ["circuit_breaker", "dlq", "health"],
    "optional_modules": ["observability", "chaos"],
    
    # 💰 FinOps DNA
    "finops": {
        "max_recovery_budget": 1000.00,
        "cost_per_retry": 0.001,
        "on_budget_exceeded": "graceful_degradation",
        "recovery_priority": {...},
    },
    
    # ⚖️ Compliance DNA
    "compliance": {
        "standards": ["DORA_2025", "PCI-DSS_4.0"],
        "auto_certification": True,
    },
    
    # 🧠 Learning DNA
    "learning": {
        "enabled": True,
        "min_samples": 5,
        "auto_tune_targets": ["p99_threshold_ms"],
        "suggestion_mode": "manual_review",
    },
}
```

### 4.2 파일 구조

```
load_tests/utils/selfhealing/
├── dna_finops.py       # FinOps 비용 관리
├── dna_compliance.py   # 규제 준수 검증
└── dna_learning.py     # Self-Learning
```

---

## 5. 다음 문서

- [33번 문서](33_DNA_SAFETY_FEATURES.md) - Rollback DNA, Blast Radius DNA

---

**작성자**: GitHub Copilot (Claude Opus 4.5)  
**검토자**: System Architect  
**승인일**: 2025-12-29

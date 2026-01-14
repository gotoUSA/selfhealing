"""
Compliance DNA Service - 규정 준수 관리 서비스
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Any
from threading import Lock

from .models import (
    ComplianceStandard,
    ComplianceCheck,
    ComplianceReport,
    ComplianceViolation,
    ViolationSeverity,
)

logger = logging.getLogger(__name__)


# 기본 규정 검사 항목
#
# TODO: 아래 규정 검사 항목들은 실제 규정 문서를 검토하여 요구사항을 구체화해야 합니다.
# - DORA: EU Regulation 2022/2554 (Digital Operational Resilience Act) 원문 검토 필요
# - PCI-DSS: Payment Card Industry Data Security Standard v4.0 문서 검토 필요
# - SOC2: AICPA Trust Services Criteria 문서 검토 필요
#
# 현재 정의된 check_id와 description은 placeholder이며,
# 실제 규정의 Article/Section 번호 및 구체적 요구사항으로 매핑되어야 합니다.
#
DEFAULT_CHECKS: Dict[ComplianceStandard, List[Dict]] = {
    ComplianceStandard.DORA_2025: [
        {
            "check_id": "DORA-001",
            "name": "ICT Risk Management",
            "description": "ICT 리스크 관리 프레임워크 확인",
            "category": "risk",
            # TODO: DORA Article 5-16 (ICT Risk Management) 실제 요구사항 검토 필요
            # - 리스크 관리 프레임워크 구성 요소
            # - 거버넌스 및 조직 체계 요구사항
        },
        {
            "check_id": "DORA-002",
            "name": "Incident Reporting",
            "description": "사고 보고 체계 확인",
            "category": "incident",
            # TODO: DORA Article 17-23 (ICT-related Incident Management) 실제 요구사항 검토 필요
            # - 사고 분류 체계 및 보고 기한
            # - 주요 ICT 관련 사고 정의 기준
        },
        {
            "check_id": "DORA-003",
            "name": "Resilience Testing",
            "description": "디지털 운영 복원력 테스트 확인",
            "category": "testing",
            # TODO: DORA Article 24-27 (Digital Operational Resilience Testing) 실제 요구사항 검토 필요
            # - 기본 테스트 vs 고급 테스트 (TLPT) 요구사항 구분
            # - 테스트 빈도, 범위, 방법론 규정
            # - Threat-Led Penetration Testing (TLPT) 대상 기관 기준
        },
        {
            "check_id": "DORA-004",
            "name": "Third-Party Risk",
            "description": "제3자 ICT 리스크 관리 확인",
            "category": "vendor",
            # TODO: DORA Article 28-44 (Third-Party Risk) 실제 요구사항 검토 필요
            # - 중요 ICT 서비스 제공자 정의 기준
            # - 계약 조항 필수 요구사항
        },
    ],
    ComplianceStandard.PCI_DSS: [
        {
            "check_id": "PCI-001",
            "name": "Secure Network",
            "description": "보안 네트워크 구성 확인",
            "category": "network",
            # TODO: PCI-DSS v4.0 Requirement 1-2 실제 요구사항 검토 필요
            # - 방화벽/네트워크 보안 통제 구성
            # - 보안 구성 기준
        },
        {
            "check_id": "PCI-002",
            "name": "Cardholder Data Protection",
            "description": "카드소유자 데이터 보호 확인",
            "category": "data",
            # TODO: PCI-DSS v4.0 Requirement 3-4 실제 요구사항 검토 필요
            # - 저장된 카드소유자 데이터 보호
            # - 전송 중 암호화 요구사항
        },
        {
            "check_id": "PCI-003",
            "name": "Access Control",
            "description": "접근 제어 확인",
            "category": "access",
            # TODO: PCI-DSS v4.0 Requirement 7-9 실제 요구사항 검토 필요
            # - 업무상 필요한 경우에만 접근 허용
            # - 사용자 식별 및 인증
            # - 물리적 접근 제한
        },
        {
            "check_id": "PCI-004",
            "name": "Monitoring & Testing",
            "description": "모니터링 및 테스트 확인",
            "category": "monitoring",
            # TODO: PCI-DSS v4.0 Requirement 10-11 실제 요구사항 검토 필요
            # - 네트워크 자원 및 카드소유자 데이터 접근 로깅
            # - 보안 시스템 및 프로세스 정기 테스트
        },
    ],
    ComplianceStandard.SOC2: [
        {
            "check_id": "SOC2-001",
            "name": "Security",
            "description": "보안 통제 확인",
            "category": "security",
            # TODO: SOC2 Type II - Security (CC6.x) Trust Services Criteria 검토 필요
            # - 논리적 및 물리적 접근 통제
            # - 시스템 운영 보안
        },
        {
            "check_id": "SOC2-002",
            "name": "Availability",
            "description": "가용성 확인",
            "category": "availability",
            # TODO: SOC2 Type II - Availability (A1.x) Trust Services Criteria 검토 필요
            # - 시스템 가용성 목표 정의 (SLA)
            # - 재해 복구 및 비즈니스 연속성 계획
        },
        {
            "check_id": "SOC2-003",
            "name": "Confidentiality",
            "description": "기밀성 확인",
            "category": "confidentiality",
            # TODO: SOC2 Type II - Confidentiality (C1.x) Trust Services Criteria 검토 필요
            # - 기밀 정보 식별 및 보호
            # - 기밀 정보 폐기 절차
        },
    ],
}


class ComplianceService:
    """
    Compliance DNA 서비스
    
    규정 준수 상태를 추적하고 리포트를 생성합니다.
    """
    
    _instance: Optional["ComplianceService"] = None
    _lock = Lock()
    
    def __new__(cls) -> "ComplianceService":
        """싱글톤 패턴"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._checks: Dict[str, ComplianceCheck] = {}
        self._violations: List[ComplianceViolation] = []
        self._reports: List[ComplianceReport] = []
        self._check_functions: Dict[str, Callable] = {}
        self._stage_standards: Dict[str, List[ComplianceStandard]] = {}
        self._enabled = True
        self._initialized = True
        
        # 기본 검사 항목 로드
        self._load_default_checks()
        
        # Phase 4: DORA-003 자동 검사 함수 등록
        # Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §18.2
        self._register_resilience_testing_check()
        
        logger.info("ComplianceService initialized")
    
    def _log_compliance_audit(
        self,
        stage_name: str,
        standard: str,
        check_id: Optional[str] = None,
        passed: bool = True,
        violation_id: Optional[str] = None,
        severity: Optional[str] = None,
        message: Optional[str] = None,
        compliance_score: Optional[float] = None,
    ) -> None:
        """Audit 헬퍼를 통해 compliance 이벤트 기록."""
        try:
            from selfhealing.services.audit_helpers import log_compliance_audit
            
            log_compliance_audit(
                stage_name=stage_name,
                standard=standard,
                check_id=check_id,
                passed=passed,
                violation_id=violation_id,
                severity=severity,
                message=message,
                compliance_score=compliance_score,
            )
        except Exception as e:
            logger.debug(f"[ComplianceService] Audit logging failed: {e}")
    
    def _load_default_checks(self) -> None:
        """기본 검사 항목 로드"""
        for standard, checks in DEFAULT_CHECKS.items():
            for check_data in checks:
                check = ComplianceCheck(
                    standard=standard,
                    **check_data,
                )
                self._checks[check.check_id] = check
    
    def _register_resilience_testing_check(self) -> None:
        """
        DORA-003 자동 검사 함수 등록.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §18.2
        """
        self._check_functions["DORA-003"] = self._check_resilience_testing
    
    def _check_resilience_testing(self) -> bool:
        """
        DORA-003: Resilience Testing 자동 검사.
        
        판정 기준:
        - 최근 30일 내 카오스 실험 4회 이상 실행
        - "실패한 실험"도 "복원력 한계를 발견한 성공적 테스트"로 인정
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §18.2
        
        Returns:
            bool: True if compliant, False otherwise
        """
        try:
            from selfhealing.services.chaos import get_chaos_scheduler
            
            scheduler = get_chaos_scheduler()
            
            # 최근 30일 실험 이력 조회
            recent_experiments = scheduler.get_execution_history(limit=500)
            
            if not recent_experiments:
                logger.warning("[Compliance] DORA-003: No chaos experiments in history")
                return False
            
            # 최근 30일 필터링
            from datetime import datetime, timedelta
            from selfhealing.core.timezone import now as tz_now
            
            cutoff = tz_now() - timedelta(days=30)
            recent_30_days = []
            
            for exp in recent_experiments:
                # ExecutionResult의 executed_at 필드 확인
                if hasattr(exp, 'executed_at') and exp.executed_at:
                    try:
                        if isinstance(exp.executed_at, str):
                            exp_time = datetime.fromisoformat(exp.executed_at.replace('Z', '+00:00'))
                        else:
                            exp_time = exp.executed_at
                        
                        # Timezone-naive 비교를 위한 처리
                        if exp_time.tzinfo is None:
                            exp_time = exp_time.replace(tzinfo=cutoff.tzinfo)
                        
                        if exp_time >= cutoff:
                            recent_30_days.append(exp)
                    except (ValueError, TypeError):
                        continue
            
            total_count = len(recent_30_days)
            
            # 최소 실험 횟수: 월 4회 (주 1회)
            min_required = 4
            
            if total_count < min_required:
                logger.warning(
                    f"[Compliance] DORA-003: Insufficient experiments "
                    f"({total_count}/{min_required})"
                )
                return False
            
            # 통과: 실패한 실험도 "복원력 한계 발견"으로 인정
            logger.info(
                f"[Compliance] DORA-003: PASSED - {total_count} experiments in 30 days "
                f"(includes failed experiments as valid resilience testing)"
            )
            return True
            
        except ImportError as e:
            logger.warning(f"[Compliance] DORA-003 check failed (import): {e}")
            return False
        except Exception as e:
            logger.warning(f"[Compliance] DORA-003 check failed: {e}")
            return False

    def register_check(
        self,
        check_id: str,
        name: str,
        description: str,
        standard: ComplianceStandard,
        category: str = "general",
        check_function: Optional[Callable[[], bool]] = None,
    ) -> ComplianceCheck:
        """
        규정 검사 항목 등록
        
        Args:
            check_id: 검사 ID
            name: 검사 이름
            description: 설명
            standard: 규정 표준
            category: 카테고리
            check_function: 검사 함수
        
        Returns:
            ComplianceCheck: 등록된 검사 항목
        """
        check = ComplianceCheck(
            check_id=check_id,
            name=name,
            description=description,
            standard=standard,
            category=category,
            check_function=check_id if check_function else None,
        )
        self._checks[check_id] = check
        
        if check_function:
            self._check_functions[check_id] = check_function
        
        return check
    
    def set_stage_standards(
        self,
        stage_name: str,
        standards: List[ComplianceStandard],
    ) -> None:
        """
        Stage별 적용 규정 설정
        
        Args:
            stage_name: Stage 이름
            standards: 적용할 규정 목록
        """
        self._stage_standards[stage_name] = standards
        logger.info(f"Standards set for {stage_name}: {[s.value for s in standards]}")
    
    def run_check(
        self,
        check_id: str,
        stage_name: str,
        context: Optional[Dict] = None,
    ) -> Optional[ComplianceViolation]:
        """
        개별 규정 검사 실행
        
        Args:
            check_id: 검사 ID
            stage_name: Stage 이름
            context: 검사 컨텍스트
        
        Returns:
            ComplianceViolation: 위반 발견 시 위반 정보, 아니면 None
        """
        check = self._checks.get(check_id)
        if not check or not check.enabled:
            return None
        
        # 검사 함수 실행
        passed = True
        details = ""
        
        if check_id in self._check_functions:
            try:
                passed = self._check_functions[check_id]()
            except Exception as e:
                passed = False
                details = str(e)
        else:
            # 기본 검사 (항상 통과)
            passed = True
        
        if not passed:
            violation = ComplianceViolation(
                violation_id=str(uuid.uuid4())[:8],
                check_id=check_id,
                stage_name=stage_name,
                standard=check.standard,
                severity=ViolationSeverity.HIGH if check.required else ViolationSeverity.MEDIUM,
                message=f"{check.name} 검사 실패",
                details=details,
                remediation=f"{check.description}을 확인하세요",
            )
            self._violations.append(violation)
            logger.warning(f"Compliance violation: {check_id} in {stage_name}")
            
            # Audit 로깅 (Phase 3)
            self._log_compliance_audit(
                stage_name=stage_name,
                standard=check.standard.value,
                check_id=check_id,
                passed=False,
                violation_id=violation.violation_id,
                severity=violation.severity.value if hasattr(violation.severity, 'value') else str(violation.severity),
                message=violation.message,
            )
            return violation
        
        # 검사 통과 시에도 Audit 로깅 (Phase 3)
        self._log_compliance_audit(
            stage_name=stage_name,
            standard=check.standard.value,
            check_id=check_id,
            passed=True,
        )
        
        return None
    
    def run_all_checks(
        self,
        stage_name: str,
        standards: Optional[List[ComplianceStandard]] = None,
    ) -> ComplianceReport:
        """
        모든 규정 검사 실행
        
        Args:
            stage_name: Stage 이름
            standards: 검사할 규정 목록 (None이면 Stage 설정 사용)
        
        Returns:
            ComplianceReport: 규정 준수 리포트
        """
        if standards is None:
            standards = self._stage_standards.get(stage_name, [])
        
        if not standards:
            standards = [ComplianceStandard.DORA_2025]  # 기본값
        
        violations = []
        passed = 0
        failed = 0
        
        for check_id, check in self._checks.items():
            if check.standard not in standards:
                continue
            if not check.enabled:
                continue
            
            violation = self.run_check(check_id, stage_name)
            if violation:
                violations.append(violation)
                failed += 1
            else:
                passed += 1
        
        total = passed + failed
        score = (passed / total * 100) if total > 0 else 100.0
        
        report = ComplianceReport(
            report_id=str(uuid.uuid4())[:8],
            stage_name=stage_name,
            standards=standards,
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            violations=violations,
            compliance_score=score,
        )
        
        self._reports.append(report)
        logger.info(
            f"Compliance report generated: {stage_name}, "
            f"score={score:.1f}%, violations={len(violations)}"
        )
        
        return report
    
    def get_violations(
        self,
        stage_name: Optional[str] = None,
        standard: Optional[ComplianceStandard] = None,
        unresolved_only: bool = False,
    ) -> List[ComplianceViolation]:
        """
        위반 목록 조회
        
        Args:
            stage_name: Stage 이름 필터
            standard: 규정 표준 필터
            unresolved_only: 미해결 위반만
        
        Returns:
            List[ComplianceViolation]: 위반 목록
        """
        violations = self._violations
        
        if stage_name:
            violations = [v for v in violations if v.stage_name == stage_name]
        if standard:
            violations = [v for v in violations if v.standard == standard]
        if unresolved_only:
            violations = [v for v in violations if not v.resolved]
        
        return violations
    
    def resolve_violation(self, violation_id: str) -> bool:
        """
        위반 해결 처리
        
        Args:
            violation_id: 위반 ID
        
        Returns:
            bool: 성공 여부
        """
        for violation in self._violations:
            if violation.violation_id == violation_id:
                violation.resolved = True
                violation.resolved_at = datetime.now(timezone.utc)
                logger.info(f"Violation resolved: {violation_id}")
                return True
        return False
    
    def get_reports(
        self,
        stage_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[ComplianceReport]:
        """
        리포트 목록 조회
        
        Args:
            stage_name: Stage 이름 필터
            limit: 최대 개수
        
        Returns:
            List[ComplianceReport]: 리포트 목록
        """
        reports = self._reports[-limit:]
        
        if stage_name:
            reports = [r for r in reports if r.stage_name == stage_name]
        
        return reports
    
    def get_compliance_status(self, stage_name: str) -> Dict:
        """
        Stage 규정 준수 상태 조회
        
        Args:
            stage_name: Stage 이름
        
        Returns:
            Dict: 상태 정보
        """
        standards = self._stage_standards.get(stage_name, [])
        violations = self.get_violations(stage_name=stage_name, unresolved_only=True)
        reports = self.get_reports(stage_name=stage_name, limit=1)
        
        return {
            "stage_name": stage_name,
            "standards": [s.value for s in standards],
            "unresolved_violations": len(violations),
            "is_compliant": len(violations) == 0,
            "last_report": reports[0].to_dict() if reports else None,
        }
    
    def enable(self) -> None:
        """서비스 활성화"""
        self._enabled = True
        
    def disable(self) -> None:
        """서비스 비활성화"""
        self._enabled = False
    
    def clear(self) -> None:
        """모든 데이터 초기화 (테스트용)"""
        self._violations.clear()
        self._reports.clear()
        self._stage_standards.clear()

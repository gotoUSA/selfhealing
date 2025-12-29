"""
DNA Analyzer - 실질적으로 유용한 Stage DNA 분석 시스템

이 모듈은 다음을 자동으로 수행합니다:
1. 코드 분석 → 실제 사용하는 Self-Healing 모듈 자동 감지
2. 테스트 결과 분석 → 필요한 모듈 추천
3. 오버엔지니어링 감지 → 불필요한 모듈 알림
4. Gap 분석 → 새로운 기능 필요 여부 제안

사용법:
    from load_tests.utils.selfhealing.dna_analyzer import DNAAnalyzer
    
    analyzer = DNAAnalyzer()
    result = analyzer.analyze_stage_file("load_tests/scenarios/integration/stage14.py")
    print(result.get_recommendations())
"""

import ast
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Any, Tuple
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# Self-Healing 모듈 시그니처 정의
# 각 모듈이 어떤 import, 클래스, 함수를 사용하는지 정의
# =============================================================================

MODULE_SIGNATURES: Dict[str, Dict[str, Any]] = {
    "circuit_breaker": {
        "imports": [
            "circuit_breaker",
            "CircuitBreaker",
            "CircuitBreakerManager",
        ],
        "patterns": [
            r"circuit_?breaker",
            r"cb_manager",
            r"\.is_open\(",
            r"\.record_failure\(",
            r"\.record_success\(",
        ],
        "api_endpoints": [
            "/api/self-healing/circuit-breaker/",
        ],
        "use_cases": [
            "외부 서비스 호출 실패 처리",
            "연쇄 장애 방지",
            "빠른 실패 (fail-fast)",
        ],
    },
    "dlq": {
        "imports": [
            "dlq",
            "DLQManager",
            "DeadLetterQueue",
        ],
        "patterns": [
            r"dlq",
            r"dead_?letter",
            r"\.enqueue\(",
            r"\.replay\(",
            r"/dlq/",
        ],
        "api_endpoints": [
            "/api/self-healing/dlq/",
        ],
        "use_cases": [
            "실패한 메시지 재처리",
            "비동기 작업 복구",
            "데이터 유실 방지",
        ],
    },
    "health": {
        "imports": [
            "health",
            "HealthCheck",
            "HealthManager",
        ],
        "patterns": [
            r"health",
            r"liveness",
            r"readiness",
            r"/health/",
        ],
        "api_endpoints": [
            "/api/self-healing/health/",
        ],
        "use_cases": [
            "서비스 상태 확인",
            "헬스체크 엔드포인트",
        ],
    },
    "error_budget": {
        "imports": [
            "error_budget",
            "ErrorBudget",
            "ErrorBudgetManager",
        ],
        "patterns": [
            r"error_?budget",
            r"budget_?gate",
            r"slo",
            r"sli",
        ],
        "api_endpoints": [
            "/api/self-healing/error-budget/",
        ],
        "use_cases": [
            "에러 예산 관리",
            "SLO 기반 배포 결정",
            "점진적 롤아웃",
        ],
    },
    "observability": {
        "imports": [
            "observability",
            "metrics",
            "tracing",
            "logging",
        ],
        "patterns": [
            r"observ",
            r"metric",
            r"trace",
            r"span",
            r"prometheus",
            r"grafana",
        ],
        "api_endpoints": [
            "/api/self-healing/metrics/",
        ],
        "use_cases": [
            "메트릭 수집",
            "분산 추적",
            "로그 집계",
        ],
    },
    "chaos": {
        "imports": [
            "chaos",
            "ChaosManager",
            "FaultInjector",
        ],
        "patterns": [
            r"chaos",
            r"fault_?inject",
            r"failure_?inject",
            r"kill_?switch",
        ],
        "api_endpoints": [
            "/api/self-healing/chaos/",
        ],
        "use_cases": [
            "장애 주입 테스트",
            "복원력 검증",
            "카오스 엔지니어링",
        ],
    },
    "emergency": {
        "imports": [
            "emergency",
            "EmergencyMode",
            "EmergencyManager",
        ],
        "patterns": [
            r"emergency",
            r"kill_?switch",
            r"shutdown",
            r"disable_?all",
        ],
        "api_endpoints": [
            "/api/self-healing/emergency/",
        ],
        "use_cases": [
            "비상 모드 활성화",
            "전체 시스템 보호",
            "긴급 차단",
        ],
    },
    "rate_limiter": {
        "imports": [
            "rate_limiter",
            "RateLimiter",
            "throttle",
        ],
        "patterns": [
            r"rate_?limit",
            r"throttl",
            r"quota",
            r"429",
        ],
        "api_endpoints": [
            "/api/self-healing/config/rate-limit/",
        ],
        "use_cases": [
            "요청 제한",
            "과부하 방지",
            "공정한 리소스 분배",
        ],
    },
    "reconciliation": {
        "imports": [
            "reconciliation",
            "Reconciler",
            "DataReconciliation",
        ],
        "patterns": [
            r"reconcil",
            r"sync",
            r"consistency",
            r"drift",
        ],
        "api_endpoints": [
            "/api/self-healing/governance/reconcile/",
        ],
        "use_cases": [
            "데이터 정합성 검증",
            "설정 동기화",
            "드리프트 감지",
        ],
    },
    "governance": {
        "imports": [
            "governance",
            "GovernanceManager",
            "ApprovalWorkflow",
        ],
        "patterns": [
            r"governance",
            r"approval",
            r"rbac",
            r"4.?eyes",
        ],
        "api_endpoints": [
            "/api/self-healing/governance/",
        ],
        "use_cases": [
            "변경 승인 워크플로우",
            "접근 제어",
            "감사 추적",
        ],
    },
    "xtest": {
        "imports": [
            "xtest",
            "XTestMode",
        ],
        "patterns": [
            r"x.?test",
            r"X-Test-Mode",
            r"test_?mode",
        ],
        "api_endpoints": [
            "/api/self-healing/xtest/",
        ],
        "use_cases": [
            "테스트 모드 활성화",
            "Rate Limit 바이패스",
            "장애 주입 테스트",
        ],
    },
}


# =============================================================================
# 테스트 결과 패턴 → 모듈 추천 매핑
# =============================================================================

RESULT_TO_MODULE_RECOMMENDATIONS: Dict[str, Dict[str, Any]] = {
    "timeout": {
        "symptoms": [
            r"timeout",
            r"timed?\s*out",
            r"deadline\s*exceeded",
            r"read timeout",
            r"connect timeout",
        ],
        "recommended_modules": ["circuit_breaker", "rate_limiter"],
        "reason": "타임아웃 발생 시 Circuit Breaker로 빠른 실패 처리, Rate Limiter로 과부하 방지",
    },
    "connection_error": {
        "symptoms": [
            r"connection\s*(error|refused|reset)",
            r"ECONNREFUSED",
            r"ECONNRESET",
            r"network\s*(error|unreachable)",
        ],
        "recommended_modules": ["circuit_breaker", "health"],
        "reason": "연결 오류 시 Circuit Breaker로 연쇄 장애 방지, Health Check로 사전 감지",
    },
    "rate_limited": {
        "symptoms": [
            r"429",
            r"too\s*many\s*requests",
            r"rate\s*limit",
            r"throttl",
        ],
        "recommended_modules": ["rate_limiter", "adaptive_jitter"],
        "reason": "Rate Limit 발생 시 자체 Rate Limiter로 사전 제어, Adaptive Jitter로 부하 분산",
    },
    "data_inconsistency": {
        "symptoms": [
            r"inconsisten",
            r"mismatch",
            r"out\s*of\s*sync",
            r"drift",
            r"stale\s*data",
        ],
        "recommended_modules": ["reconciliation", "l2_storage"],
        "reason": "데이터 불일치 시 Reconciliation으로 정합성 복구, L2 Storage로 백업 관리",
    },
    "retry_exhausted": {
        "symptoms": [
            r"retry\s*(exhausted|exceeded|limit)",
            r"max\s*retries",
            r"all\s*retries\s*failed",
        ],
        "recommended_modules": ["dlq", "error_budget"],
        "reason": "재시도 소진 시 DLQ로 나중에 재처리, Error Budget으로 임계점 관리",
    },
    "memory_pressure": {
        "symptoms": [
            r"out\s*of\s*memory",
            r"memory\s*(error|pressure|exhausted)",
            r"OOM",
            r"heap\s*exhausted",
        ],
        "recommended_modules": ["state_cache", "emergency"],
        "reason": "메모리 압박 시 State Cache로 효율적 캐싱, Emergency 모드로 긴급 보호",
    },
    "cascade_failure": {
        "symptoms": [
            r"cascade",
            r"domino",
            r"propagat",
            r"upstream\s*failure",
            r"downstream\s*failure",
        ],
        "recommended_modules": ["circuit_breaker", "chaos", "emergency"],
        "reason": "연쇄 장애 시 Circuit Breaker로 격리, Chaos로 사전 테스트, Emergency로 긴급 차단",
    },
}


class RecommendationType(Enum):
    """추천 유형"""
    ADD = "add"              # 추가 권장
    REMOVE = "remove"        # 제거 권장 (오버엔지니어링)
    NEW_FEATURE = "new"      # 새 기능 개발 필요
    UPGRADE = "upgrade"      # 기존 모듈 업그레이드 필요


@dataclass
class ModuleUsage:
    """모듈 사용 정보"""
    module_name: str
    is_imported: bool = False
    is_used_in_code: bool = False
    api_calls_detected: int = 0
    confidence: float = 0.0  # 0.0 ~ 1.0
    evidence: List[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """개선 추천"""
    type: RecommendationType
    module_name: str
    reason: str
    priority: str = "medium"  # low, medium, high, critical
    evidence: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "module": self.module_name,
            "reason": self.reason,
            "priority": self.priority,
            "evidence": self.evidence,
        }


@dataclass
class AnalysisResult:
    """분석 결과"""
    stage_file: str
    declared_modules: Set[str] = field(default_factory=set)
    detected_modules: Set[str] = field(default_factory=set)
    module_usages: Dict[str, ModuleUsage] = field(default_factory=dict)
    recommendations: List[Recommendation] = field(default_factory=list)
    test_results_analyzed: bool = False
    gaps: List[str] = field(default_factory=list)
    analysis_error: Optional[str] = None  # Graceful degradation용
    
    @property
    def is_valid(self) -> bool:
        """분석이 성공했는지 여부"""
        return self.analysis_error is None
    
    @property
    def missing_modules(self) -> Set[str]:
        """선언했지만 사용 안 하는 모듈 (오버엔지니어링)"""
        return self.declared_modules - self.detected_modules
    
    # legacy alias
    @property
    def over_engineered(self) -> Set[str]:
        """선언했지만 사용 안 하는 모듈 (오버엔지니어링)"""
        return self.missing_modules
    
    @property
    def undeclared_modules(self) -> Set[str]:
        """사용하지만 선언 안 한 모듈"""
        return self.detected_modules - self.declared_modules
    
    # legacy alias
    @property
    def under_declared(self) -> Set[str]:
        """사용하지만 선언 안 한 모듈"""
        return self.undeclared_modules
    
    def get_summary(self) -> str:
        """요약 출력"""
        lines = [
            f"📊 Stage DNA 분석 결과: {self.stage_file}",
            f"{'=' * 60}",
            "",
        ]
        
        if self.analysis_error:
            lines.append(f"❌ 분석 오류: {self.analysis_error}")
            return "\n".join(lines)
        
        lines.extend([
            f"📝 선언된 모듈: {sorted(self.declared_modules) or '없음'}",
            f"🔍 감지된 모듈: {sorted(self.detected_modules) or '없음'}",
            "",
        ])
        
        if self.missing_modules:
            lines.append(f"⚠️ 오버엔지니어링 (선언했지만 미사용): {sorted(self.missing_modules)}")
        
        if self.undeclared_modules:
            lines.append(f"🔴 미선언 (사용하지만 선언 안 함): {sorted(self.undeclared_modules)}")
        
        if self.recommendations:
            lines.append("")
            lines.append("💡 추천사항:")
            for rec in self.recommendations:
                icon = {
                    RecommendationType.ADD: "➕",
                    RecommendationType.REMOVE: "➖",
                    RecommendationType.NEW_FEATURE: "🆕",
                    RecommendationType.UPGRADE: "⬆️",
                }.get(rec.type, "•")
                lines.append(f"  {icon} [{rec.priority.upper()}] {rec.module_name}: {rec.reason}")
        
        if self.gaps:
            lines.append("")
            lines.append("🚧 Gap 분석 (새 기능 필요):")
            for gap in self.gaps:
                lines.append(f"  • {gap}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict:
        return {
            "stage_file": self.stage_file,
            "declared_modules": list(self.declared_modules),
            "detected_modules": list(self.detected_modules),
            "missing_modules": list(self.missing_modules),
            "undeclared_modules": list(self.undeclared_modules),
            "recommendations": [r.to_dict() for r in self.recommendations],
            "gaps": self.gaps,
        }


class DNAAnalyzer:
    """
    Stage DNA 분석기
    
    코드를 분석하여 실제 사용하는 모듈을 감지하고,
    테스트 결과를 기반으로 개선 추천을 제공합니다.
    
    Graceful Degradation:
    - 파일 없음: 빈 결과 반환
    - 구문 오류: 정규식 기반 분석으로 fallback
    - 완전 실패: 예외 없이 빈 결과 반환
    """
    
    def __init__(self, fail_silently: bool = True):
        """
        Args:
            fail_silently: True면 에러 시 빈 결과 반환, False면 예외 발생
        """
        self.module_signatures = MODULE_SIGNATURES
        self.result_recommendations = RESULT_TO_MODULE_RECOMMENDATIONS
        self.fail_silently = fail_silently
    
    def analyze_stage_file(self, file_path: str) -> AnalysisResult:
        """
        Stage 파일 분석
        
        Args:
            file_path: Stage 파일 경로
        
        Returns:
            AnalysisResult: 분석 결과 (실패 시에도 빈 결과 반환)
        """
        result = AnalysisResult(stage_file=file_path)
        
        try:
            if not os.path.exists(file_path):
                logger.warning(f"File not found: {file_path}")
                result.analysis_error = f"파일을 찾을 수 없음: {file_path}"
                return result
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 1. STAGE_DNA에서 선언된 모듈 추출
            try:
                result.declared_modules = self._extract_declared_modules(content)
            except Exception as e:
                logger.warning(f"Failed to extract declared modules: {e}")
                result.declared_modules = set()
            
            # 2. 코드에서 실제 사용하는 모듈 감지
            try:
                result.detected_modules, result.module_usages = self._detect_used_modules(content)
            except Exception as e:
                logger.warning(f"Failed to detect used modules: {e}")
                result.detected_modules = set()
                result.module_usages = {}
            
            # 3. 오버엔지니어링 감지 (선언했지만 미사용)
            for module in result.missing_modules:
                result.recommendations.append(Recommendation(
                    type=RecommendationType.REMOVE,
                    module_name=module,
                    reason=f"선언되었지만 코드에서 사용되지 않음 - 제거 권장",
                    priority="low",
                    evidence=["선언만 있고 import/사용 패턴 없음"],
                ))
            
            # 4. 미선언 모듈 경고 (사용하지만 선언 안 함)
            for module in result.undeclared_modules:
                usage = result.module_usages.get(module)
                result.recommendations.append(Recommendation(
                    type=RecommendationType.ADD,
                    module_name=module,
                    reason=f"코드에서 사용 중이지만 STAGE_DNA에 선언되지 않음",
                    priority="medium",
                    evidence=usage.evidence if usage else [],
                ))
            
        except Exception as e:
            logger.error(f"Analysis failed for {file_path}: {e}")
            result.analysis_error = str(e)
            if not self.fail_silently:
                raise
        
        return result
    
    def analyze_test_results(
        self,
        result: AnalysisResult,
        test_output: str,
        error_logs: Optional[str] = None,
    ) -> AnalysisResult:
        """
        테스트 결과 분석하여 모듈 추천
        
        Args:
            result: 기존 분석 결과
            test_output: 테스트 실행 출력
            error_logs: 에러 로그 (선택)
        
        Returns:
            AnalysisResult: 업데이트된 분석 결과
        """
        result.test_results_analyzed = True
        combined_text = test_output + (error_logs or "")
        
        for issue_type, config in self.result_recommendations.items():
            # 증상 패턴 매칭
            matched_symptoms = []
            for pattern in config["symptoms"]:
                matches = re.findall(pattern, combined_text, re.IGNORECASE)
                if matches:
                    matched_symptoms.extend(matches[:3])  # 최대 3개
            
            if matched_symptoms:
                # 아직 없는 모듈만 추천
                for module in config["recommended_modules"]:
                    if module not in result.detected_modules and module not in result.declared_modules:
                        # 이미 추천했는지 확인
                        already_recommended = any(
                            r.module_name == module and r.type == RecommendationType.ADD
                            for r in result.recommendations
                        )
                        if not already_recommended:
                            result.recommendations.append(Recommendation(
                                type=RecommendationType.ADD,
                                module_name=module,
                                reason=config["reason"],
                                priority="high",
                                evidence=[f"'{s}' 패턴 감지됨" for s in matched_symptoms[:3]],
                            ))
        
        return result
    
    def detect_gaps(
        self,
        result: AnalysisResult,
        test_output: str,
    ) -> AnalysisResult:
        """
        기존 모듈로 해결할 수 없는 Gap 분석
        
        Args:
            result: 기존 분석 결과
            test_output: 테스트 실행 출력
        
        Returns:
            AnalysisResult: Gap 분석이 추가된 결과
        """
        # 기존 모듈로 커버되지 않는 패턴들
        gap_patterns = {
            "distributed_lock": {
                "patterns": [r"race\s*condition", r"concurrent\s*modify", r"lock\s*contention"],
                "description": "분산 락 메커니즘이 필요할 수 있음 (Redis/Zookeeper 기반)",
            },
            "saga_pattern": {
                "patterns": [r"rollback\s*failed", r"compensat", r"saga", r"long\s*running\s*transaction"],
                "description": "Saga 패턴을 통한 분산 트랜잭션 관리가 필요할 수 있음",
            },
            "event_sourcing": {
                "patterns": [r"event\s*replay", r"audit\s*trail", r"event\s*store"],
                "description": "이벤트 소싱 패턴이 필요할 수 있음",
            },
            "bulkhead": {
                "patterns": [r"resource\s*exhausted", r"pool\s*exhausted", r"isolat"],
                "description": "Bulkhead 패턴으로 리소스 격리가 필요할 수 있음",
            },
            "backpressure": {
                "patterns": [r"backpressure", r"queue\s*full", r"buffer\s*overflow", r"producer.*faster.*consumer"],
                "description": "백프레셔 메커니즘이 필요할 수 있음",
            },
        }
        
        for gap_name, config in gap_patterns.items():
            for pattern in config["patterns"]:
                if re.search(pattern, test_output, re.IGNORECASE):
                    gap_msg = f"[{gap_name}] {config['description']}"
                    if gap_msg not in result.gaps:
                        result.gaps.append(gap_msg)
                        result.recommendations.append(Recommendation(
                            type=RecommendationType.NEW_FEATURE,
                            module_name=gap_name,
                            reason=config["description"],
                            priority="medium",
                            evidence=[f"'{pattern}' 패턴 감지됨"],
                        ))
                    break
        
        return result
    
    def _extract_declared_modules(self, content: str) -> Set[str]:
        """STAGE_DNA에서 선언된 모듈 추출"""
        declared = set()
        
        try:
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "STAGE_DNA":
                            try:
                                dna_dict = ast.literal_eval(node.value)
                                declared.update(dna_dict.get("required_modules", []))
                                declared.update(dna_dict.get("optional_modules", []))
                            except (ValueError, TypeError):
                                pass
        except SyntaxError:
            pass
        
        return declared
    
    def _detect_used_modules(self, content: str) -> Tuple[Set[str], Dict[str, ModuleUsage]]:
        """코드에서 실제 사용하는 모듈 감지"""
        detected = set()
        usages: Dict[str, ModuleUsage] = {}
        
        for module_name, signatures in self.module_signatures.items():
            usage = ModuleUsage(module_name=module_name)
            
            # import 문 체크
            for import_sig in signatures.get("imports", []):
                if re.search(rf"(from|import).*{import_sig}", content, re.IGNORECASE):
                    usage.is_imported = True
                    usage.evidence.append(f"import: {import_sig}")
            
            # 코드 패턴 체크
            for pattern in signatures.get("patterns", []):
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    usage.is_used_in_code = True
                    usage.evidence.append(f"pattern: {pattern} ({len(matches)}회)")
            
            # API 엔드포인트 체크
            for endpoint in signatures.get("api_endpoints", []):
                if endpoint in content:
                    usage.api_calls_detected += content.count(endpoint)
                    usage.evidence.append(f"API: {endpoint}")
            
            # 신뢰도 계산
            confidence = 0.0
            if usage.is_imported:
                confidence += 0.5
            if usage.is_used_in_code:
                confidence += 0.3
            if usage.api_calls_detected > 0:
                confidence += 0.2
            usage.confidence = min(confidence, 1.0)
            
            if usage.confidence > 0.3:  # 30% 이상 신뢰도면 감지된 것으로 판단
                detected.add(module_name)
            
            usages[module_name] = usage
        
        return detected, usages
    
    def generate_optimal_dna(self, result: AnalysisResult) -> Dict[str, Any]:
        """
        분석 결과를 기반으로 최적의 STAGE_DNA 생성
        
        Args:
            result: 분석 결과
        
        Returns:
            Dict: 최적화된 STAGE_DNA
        """
        # 실제 사용하는 모듈만 포함
        required = list(result.detected_modules)
        
        # 추천된 모듈 중 ADD 타입 추가
        for rec in result.recommendations:
            if rec.type == RecommendationType.ADD and rec.priority in ["high", "critical"]:
                if rec.module_name not in required:
                    required.append(rec.module_name)
        
        # Stage 유형 추론
        stage_type = self._infer_stage_type(result)
        
        return {
            "name": os.path.basename(result.stage_file),
            "type": stage_type,
            "required_modules": sorted(required),
            "optional_modules": [],
            "_generated_by": "DNAAnalyzer",
            "_analysis_confidence": sum(
                u.confidence for u in result.module_usages.values()
            ) / max(len(result.module_usages), 1),
        }
    
    def _infer_stage_type(self, result: AnalysisResult) -> str:
        """코드 기반 Stage 유형 추론"""
        detected = result.detected_modules
        
        if "chaos" in detected or "xtest" in detected:
            return "chaos"
        elif "error_budget" in detected:
            return "load"
        elif "dlq" in detected or "observability" in detected:
            return "integration"
        elif "health" in detected and len(detected) <= 2:
            return "smoke"
        else:
            return "integration"  # 기본값


# =============================================================================
# CLI 인터페이스
# =============================================================================

def analyze_stage(file_path: str, test_output: Optional[str] = None) -> AnalysisResult:
    """
    Stage 파일 분석 (간편 함수)
    
    Args:
        file_path: Stage 파일 경로
        test_output: 테스트 출력 (선택)
    
    Returns:
        AnalysisResult: 분석 결과
    """
    analyzer = DNAAnalyzer()
    result = analyzer.analyze_stage_file(file_path)
    
    if test_output:
        result = analyzer.analyze_test_results(result, test_output)
        result = analyzer.detect_gaps(result, test_output)
    
    return result


if __name__ == "__main__":
    import argparse
    import glob
    
    parser = argparse.ArgumentParser(description="Stage DNA Analyzer")
    parser.add_argument(
        "--file",
        type=str,
        help="분석할 Stage 파일 경로",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="load_tests/scenarios",
        help="Stage 파일 디렉토리",
    )
    parser.add_argument(
        "--test-output",
        type=str,
        help="테스트 출력 파일 경로",
    )
    parser.add_argument(
        "--generate-dna",
        action="store_true",
        help="최적화된 STAGE_DNA 생성",
    )
    
    args = parser.parse_args()
    
    analyzer = DNAAnalyzer()
    
    if args.file:
        files = [args.file]
    else:
        files = glob.glob(os.path.join(args.dir, "**", "stage*.py"), recursive=True)
    
    test_output = ""
    if args.test_output and os.path.exists(args.test_output):
        with open(args.test_output, "r") as f:
            test_output = f.read()
    
    for file_path in files:
        print(f"\n{'=' * 60}")
        result = analyzer.analyze_stage_file(file_path)
        
        if test_output:
            result = analyzer.analyze_test_results(result, test_output)
            result = analyzer.detect_gaps(result, test_output)
        
        print(result.get_summary())
        
        if args.generate_dna:
            print("\n📋 최적화된 STAGE_DNA:")
            import json
            optimal_dna = analyzer.generate_optimal_dna(result)
            print(json.dumps(optimal_dna, indent=2, ensure_ascii=False))

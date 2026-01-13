"""
Chaos Experiment Isolation Helpers

가상 격리 패턴 구현 헬퍼 함수.
Reference: 34_CHAOS_SAFETY_MECHANISMS.md §3

작성일: 2026-01-14
"""

from typing import Any, Dict, Optional
import logging

from .constants import CHAOS_DOMAIN_PREFIX, CHAOS_METADATA_FLAGS

logger = logging.getLogger(__name__)


def get_isolated_domain(base_domain: str) -> str:
    """
    도메인에 chaos 접두어 추가.
    
    가상 격리를 위해 실험 데이터를 별도 도메인에 저장합니다.
    이미 접두어가 있는 경우 그대로 반환합니다.
    
    Args:
        base_domain: 원본 도메인 (예: "payment")
    
    Returns:
        격리된 도메인 (예: "chaos_test:payment")
    
    Example:
        >>> get_isolated_domain("payment")
        'chaos_test:payment'
        >>> get_isolated_domain("chaos_test:payment")  # 중복 방지
        'chaos_test:payment'
    """
    if base_domain.startswith(CHAOS_DOMAIN_PREFIX):
        return base_domain
    return f"{CHAOS_DOMAIN_PREFIX}{base_domain}"


def strip_isolation_prefix(isolated_domain: str) -> str:
    """
    도메인에서 chaos 접두어 제거.
    
    Args:
        isolated_domain: 격리된 도메인 (예: "chaos_test:payment")
    
    Returns:
        원본 도메인 (예: "payment")
    """
    if isolated_domain.startswith(CHAOS_DOMAIN_PREFIX):
        return isolated_domain[len(CHAOS_DOMAIN_PREFIX):]
    return isolated_domain


def is_chaos_domain(domain: str) -> bool:
    """
    도메인이 Chaos 실험용 격리 도메인인지 확인.
    
    Args:
        domain: 확인할 도메인
    
    Returns:
        True if chaos domain, False otherwise
    """
    return domain.startswith(CHAOS_DOMAIN_PREFIX)


def get_isolation_metadata(
    experiment_id: str,
    experiment_type: str,
    additional: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    가상 격리 메타데이터 생성.
    
    실험 데이터에 필수 격리 플래그를 포함한 메타데이터를 생성합니다.
    이 플래그들은 Error Budget, SLA 계산 등에서 자동 제외 조건으로 사용됩니다.
    
    Args:
        experiment_id: 실험 ID (UUID 또는 고유 식별자)
        experiment_type: 실험 타입 (예: "replay_flood")
        additional: 추가 메타데이터 (선택)
    
    Returns:
        격리 플래그가 포함된 메타데이터 딕셔너리
        - is_synthetic: True (합성 데이터 표시)
        - is_chaos_experiment: True (카오스 실험 표시)
        - chaos_experiment_id: 실험 ID
        - chaos_experiment_type: 실험 타입
        - chaos_domain_prefix: 사용된 도메인 접두어
    
    Example:
        >>> get_isolation_metadata("exp-123", "replay_flood")
        {
            'is_synthetic': True,
            'is_chaos_experiment': True,
            'chaos_experiment_id': 'exp-123',
            'chaos_experiment_type': 'replay_flood',
            'chaos_domain_prefix': 'chaos_test:'
        }
    """
    metadata: Dict[str, Any] = {
        **CHAOS_METADATA_FLAGS,
        "chaos_experiment_id": experiment_id,
        "chaos_experiment_type": experiment_type,
        "chaos_domain_prefix": CHAOS_DOMAIN_PREFIX,
    }
    
    if additional:
        metadata.update(additional)
    
    return metadata


def should_exclude_from_metrics(metadata: Optional[Dict[str, Any]]) -> bool:
    """
    메타데이터 기반으로 메트릭에서 제외 여부 결정.
    
    Error Budget, SLA, 통계 계산 시 호출하여
    Chaos 실험 데이터를 제외해야 하는지 확인합니다.
    
    Args:
        metadata: DLQ 엔트리 또는 이벤트의 메타데이터
    
    Returns:
        True if should be excluded from metrics, False otherwise
    """
    if not metadata:
        return False
    
    # is_chaos_experiment 플래그 확인 (기존 구현과 호환)
    if metadata.get("is_chaos_experiment"):
        return True
    
    # is_synthetic 플래그 확인
    if metadata.get("is_synthetic"):
        return True
    
    return False


def cleanup_chaos_entries(
    experiment_id: str,
    isolated_domain: str,
    max_entries: int = 10000,
) -> int:
    """
    실험 종료 후 카오스 엔트리 자동 정리.
    
    실험의 rollback 단계에서 호출되어 생성된 합성 데이터를 정리합니다.
    
    Args:
        experiment_id: 정리할 실험 ID
        isolated_domain: 격리된 도메인 (chaos_test:xxx 형식)
        max_entries: 최대 정리 개수 (기본값: 10,000)
    
    Returns:
        삭제된 엔트리 수
    
    Raises:
        ImportError: DLQ 서비스를 임포트할 수 없는 경우
    """
    try:
        # 지연 임포트 (순환 참조 방지)
        from selfhealing.services.dlq import get_dlq_service
        
        dlq = get_dlq_service()
        repo = dlq.repository
        
        # chaos_test: 도메인 엔트리 조회
        chaos_entries = repo.query(
            domain=isolated_domain,
            limit=max_entries,
        )
        
        purged_count = 0
        for entry in chaos_entries:
            # is_chaos_experiment 플래그 확인 (이중 검증)
            if entry.metadata and entry.metadata.get("is_chaos_experiment"):
                # 해당 실험 ID 확인
                if entry.metadata.get("chaos_experiment_id") == experiment_id:
                    repo.delete(entry.id)
                    purged_count += 1
        
        logger.info(
            f"[ChaosIsolation] Purged {purged_count} entries "
            f"for experiment {experiment_id} in domain {isolated_domain}"
        )
        
        # === Self-Cleanup: FinOps 비용 환불 ===
        # "실험이 끝나면 자원만 치우는 게 아니라, 재무적 비용 지표까지도 정확히 원복시킨다"
        if purged_count > 0:
            _record_cleanup_cost_refund(experiment_id, purged_count)
        
        return purged_count
        
    except ImportError as e:
        logger.error(f"[ChaosIsolation] Cannot import DLQ service: {e}")
        raise
    except Exception as e:
        logger.error(f"[ChaosIsolation] Cleanup failed: {e}")
        return 0


def _record_cleanup_cost_refund(
    experiment_id: str,
    cleaned_entries: int,
) -> None:
    """
    Self-Cleanup: 정리된 엔트리에 대한 비용을 FinOps에 마이너스 기록.
    
    "우리는 실험이 끝나면 자원만 치우는 게 아니라, 
    재무적 비용 지표까지도 정확히 원복시킨다"
    
    Reference: FinOpsService.record_cost()는 Decimal 타입이므로 음수 기록 가능
    코드 근거: finops/service.py:97-130
    
    Args:
        experiment_id: 정리된 실험 ID
        cleaned_entries: 삭제된 엔트리 수
    """
    try:
        from decimal import Decimal
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        
        # 엔트리당 비용 (DLQ 엔트리 생성 비용)
        per_entry_cost = finops._operation_costs.get("dlq_enqueue", Decimal("0.005"))
        refund_amount = per_entry_cost * cleaned_entries
        
        finops.record_cost(
            operation="chaos_cleanup_refund",
            stage_name=finops.CHAOS_BUDGET_STAGE_NAME,
            cost=-refund_amount,  # 마이너스 = 환불 (Self-Cleanup)
            success=True,
            metadata={
                "experiment_id": experiment_id,
                "cleaned_entries": cleaned_entries,
                "self_cleanup": True,
            },
        )
        logger.info(
            f"[FinOps] Self-Cleanup refund: -${refund_amount} "
            f"for {cleaned_entries} entries"
        )
    except Exception as e:
        logger.debug(f"[FinOps] Cleanup refund skipped: {e}")


def cleanup_all_chaos_domains(max_entries_per_domain: int = 10000) -> Dict[str, int]:
    """
    모든 Chaos 도메인의 엔트리 정리.
    
    관리자가 수동으로 모든 Chaos 실험 잔여 데이터를 정리할 때 사용합니다.
    
    Args:
        max_entries_per_domain: 도메인당 최대 정리 개수
    
    Returns:
        도메인별 삭제 개수 딕셔너리
    """
    try:
        from selfhealing.services.dlq import get_dlq_service
        
        dlq = get_dlq_service()
        repo = dlq.repository
        
        # 모든 도메인 조회
        all_domains = repo.get_all_domains()
        
        results: Dict[str, int] = {}
        for domain in all_domains:
            if is_chaos_domain(domain):
                entries = repo.query(domain=domain, limit=max_entries_per_domain)
                deleted = 0
                for entry in entries:
                    if should_exclude_from_metrics(entry.metadata):
                        repo.delete(entry.id)
                        deleted += 1
                results[domain] = deleted
        
        total_deleted = sum(results.values())
        logger.info(
            f"[ChaosIsolation] Total cleanup: {total_deleted} entries "
            f"across {len(results)} chaos domains"
        )
        return results
        
    except Exception as e:
        logger.error(f"[ChaosIsolation] Full cleanup failed: {e}")
        return {}

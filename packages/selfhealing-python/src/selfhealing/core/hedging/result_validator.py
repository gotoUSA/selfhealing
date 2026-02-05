"""
Hedging Result Validator - 비동기 결과 정합성 검증.

헷징으로 선택된 결과와 다른 후보들의 결과를 비동기적으로 비교하여
불일치를 감지합니다. 첫 응답 속도에는 영향을 주지 않습니다.

핵심 원칙:
1. 첫 응답 즉시 반환 (속도 유지)
2. 다른 응답 도착 후 백그라운드 비교
3. 불일치 시 로깅/메트릭만 (채택 결과 변경 안 함)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from selfhealing.core.hedging.metrics import record_result_mismatch

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ResultMismatchRecord:
    """
    결과 불일치 기록.

    두 후보 간의 결과 불일치 정보를 저장합니다.
    """

    operation_id: str
    """요청 식별자."""

    winner_source: str
    """채택된 결과의 출처 (예: "primary", "secondary")."""

    winner_value_hash: str
    """채택된 결과의 해시 (전체 값 저장 대신)."""

    other_source: str
    """다른 결과의 출처."""

    other_value_hash: str
    """다른 결과의 해시."""

    mismatch_type: Literal["value", "type", "structure"]
    """불일치 유형: value(값 다름), type(타입 다름), structure(구조 다름)."""

    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """감지 시각."""

    latency_diff_ms: float = 0.0
    """두 응답 간 지연시간 차이."""

    winner_region: str = ""
    """승리 결과의 리전 (예: ap-northeast-2)."""

    other_region: str = ""
    """다른 결과의 리전 (예: us-east-1)."""

    estimated_replication_lag_ms: float | None = None
    """추정 복제 지연 (latency_diff_ms 기반)."""


class HedgingResultValidator:
    """
    헷징 결과 비동기 검증기.

    헷징으로 선택된 결과와 다른 후보들의 결과를 백그라운드에서 비교하여
    불일치를 감지하고 기록합니다. 첫 응답 속도에는 영향을 주지 않습니다.

    Usage:
        validator = HedgingResultValidator(
            comparator=lambda a, b: a == b,
            on_mismatch=lambda record: metrics.inc("hedging_mismatch"),
        )

        # 비동기 검증 (즉시 반환, 백그라운드 비교)
        validator.validate_async(
            operation_id="op-123",
            winner_source="primary",
            winner_value=result1,
            winner_latency_ms=100,
            other_results={"secondary": (result2, 200)},
        )
    """

    def __init__(
        self,
        comparator: Callable[[T, T], bool] | None = None,
        on_mismatch: Callable[[ResultMismatchRecord], None] | None = None,
        enabled: bool = True,
        sample_rate: float = 0.1,
        policy: str = "any_mismatch",
        skip_on_high_load: bool = True,
        escalate_on_structure: bool = True,
        persist_critical: bool = False,
        current_load_level_getter: Callable[[], str] | None = None,
    ):
        """
        Args:
            comparator: 두 결과 비교 함수. None이면 == 사용.
            on_mismatch: 불일치 발견 시 콜백.
            enabled: 검증 활성화 여부.
            sample_rate: 샘플링 비율 (0.0~1.0).
            policy: 검증 정책 (any_mismatch, all_same, majority_wins).
            skip_on_high_load: HIGH/CRITICAL 부하 시 검증 생략.
            escalate_on_structure: 구조적 불일치 시 에스컬레이션.
            persist_critical: 구조적 불일치 영속적 저장.
            current_load_level_getter: 현재 부하 레벨 반환 함수.
        """
        self._comparator = comparator or (lambda a, b: a == b)
        self._on_mismatch = on_mismatch
        self._enabled = enabled
        self._sample_rate = sample_rate
        self._policy = policy
        self._skip_on_high_load = skip_on_high_load
        self._escalate_on_structure = escalate_on_structure
        self._persist_critical = persist_critical
        self._get_load_level = current_load_level_getter

        # 백그라운드 실행용 스레드 풀
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="hedging_validator",
        )

        # 불일치 기록
        self._mismatch_history: list[ResultMismatchRecord] = []
        self._lock = threading.Lock()
        self._max_history = 100

    def _should_skip_due_to_load(self) -> bool:
        """
        부하 레벨에 따라 검증 생략 여부 결정.

        HIGH, CRITICAL 레벨에서는 검증을 생략하여
        시스템 부하를 줄입니다.
        """
        if not self._skip_on_high_load:
            return False

        if not self._get_load_level:
            return False

        try:
            level = self._get_load_level().lower()
            return level in ("high", "critical")
        except Exception:
            return False

    def should_validate(self) -> bool:
        """
        샘플링에 따라 검증 여부 결정.

        Returns:
            검증 수행 여부
        """
        if not self._enabled:
            return False

        # 부하 체크
        if self._should_skip_due_to_load():
            logger.debug("[HedgingValidator] Skipped due to high load")
            return False

        if self._sample_rate >= 1.0:
            return True

        return random.random() < self._sample_rate

    def validate_async(
        self,
        operation_id: str,
        winner_source: str,
        winner_value: T,
        winner_latency_ms: float,
        other_results: dict[str, tuple[T, float]],
        winner_region: str = "",
        other_regions: dict[str, str] | None = None,
    ) -> None:
        """
        비동기로 결과 비교.

        이 메서드는 즉시 반환되고, 비교는 백그라운드에서 실행됩니다.
        첫 응답 속도에 영향을 주지 않습니다.

        Args:
            operation_id: 요청 식별자
            winner_source: 승리 후보 이름
            winner_value: 승리 값
            winner_latency_ms: 승리 지연시간
            other_results: {source: (value, latency_ms)} 다른 결과들
            winner_region: 승리 리전 (선택)
            other_regions: {source: region} 매핑 (선택)
        """
        if not self.should_validate():
            return

        if not other_results:
            return

        # 백그라운드에서 비교
        self._executor.submit(
            self._do_validate,
            operation_id,
            winner_source,
            winner_value,
            winner_latency_ms,
            other_results,
            winner_region,
            other_regions or {},
        )

    def _do_validate(
        self,
        operation_id: str,
        winner_source: str,
        winner_value: T,
        winner_latency_ms: float,
        other_results: dict[str, tuple[T, float]],
        winner_region: str,
        other_regions: dict[str, str],
    ) -> None:
        """실제 비교 수행 (백그라운드)."""
        for other_source, (other_value, other_latency_ms) in other_results.items():
            try:
                is_equal = self._comparator(winner_value, other_value)

                if not is_equal:
                    other_region = other_regions.get(other_source, "")
                    mismatch_type = self._detect_mismatch_type(winner_value, other_value)

                    record = ResultMismatchRecord(
                        operation_id=operation_id,
                        winner_source=winner_source,
                        winner_value_hash=self._hash_value(winner_value),
                        other_source=other_source,
                        other_value_hash=self._hash_value(other_value),
                        mismatch_type=mismatch_type,
                        latency_diff_ms=other_latency_ms - winner_latency_ms,
                        winner_region=winner_region,
                        other_region=other_region,
                        estimated_replication_lag_ms=(
                            abs(other_latency_ms - winner_latency_ms) if winner_region and other_region else None
                        ),
                    )

                    self._record_mismatch(record)

            except Exception as e:
                logger.warning(f"[HedgingValidator] Comparison failed: {e}")

    def _hash_value(self, value: Any) -> str:
        """
        값의 해시 생성 (로깅용).

        전체 값을 저장하는 대신 해시로 저장하여 메모리를 절약합니다.
        """
        try:
            serialized = json.dumps(value, sort_keys=True, default=str)
            return hashlib.md5(serialized.encode()).hexdigest()[:8]
        except Exception:
            return str(hash(str(value)))[:8]

    def _detect_mismatch_type(self, a: Any, b: Any) -> Literal["value", "type", "structure"]:
        """
        불일치 유형 감지.

        type: 타입이 다름
        structure: dict/list의 키/길이가 다름
        value: 값만 다름
        """
        if type(a) != type(b):
            return "type"
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a.keys()) != set(b.keys()):
                return "structure"
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return "structure"
        return "value"

    def _record_mismatch(self, record: ResultMismatchRecord) -> None:
        """불일치 기록 저장 및 콜백 실행."""
        # 1. 메모리 기록
        with self._lock:
            self._mismatch_history.append(record)
            if len(self._mismatch_history) > self._max_history:
                self._mismatch_history = self._mismatch_history[-self._max_history :]

        # 2. 경고 로깅
        logger.warning(
            f"[HedgingValidator] Result mismatch detected: "
            f"op={record.operation_id}, "
            f"winner={record.winner_source}({record.winner_region or 'local'}), "
            f"other={record.other_source}({record.other_region or 'local'}), "
            f"type={record.mismatch_type}, "
            f"lag_ms={record.estimated_replication_lag_ms}"
        )

        # 3. Prometheus 메트릭
        record_result_mismatch(record.mismatch_type)

        # 4. 콜백 실행
        if self._on_mismatch:
            try:
                self._on_mismatch(record)
            except Exception as e:
                logger.warning(f"[HedgingValidator] Callback error: {e}")

        # 5. 구조적 불일치 시 추가 처리
        if record.mismatch_type in ("type", "structure"):
            self._handle_critical_mismatch(record)

    def _handle_critical_mismatch(self, record: ResultMismatchRecord) -> None:
        """
        구조적 불일치 처리.

        구조적 불일치는 데이터 동기화 문제나 버그를 나타낼 수 있으므로
        에스컬레이션 및 영속적 저장을 수행합니다.
        """
        # 에스컬레이션 시도 (선택적)
        if self._escalate_on_structure:
            try:
                from selfhealing.meta.escalation import (
                    EscalationLevel,
                    EscalationManager,
                )

                mgr = EscalationManager()
                mgr.escalate(
                    level=EscalationLevel.WARNING,
                    component="hedging",
                    message=(f"Result {record.mismatch_type} mismatch: " f"{record.winner_source} vs {record.other_source}"),
                    details={
                        "operation_id": record.operation_id,
                        "mismatch_type": record.mismatch_type,
                        "winner_region": record.winner_region,
                        "other_region": record.other_region,
                    },
                )
                logger.info(f"[HedgingValidator] Escalated to Meta-Watchdog: " f"{record.operation_id}")
            except ImportError:
                pass  # 에스컬레이션 모듈 없음
            except Exception as e:
                logger.warning(f"[HedgingValidator] Escalation failed: {e}")

        # 영속적 저장 시도 (선택적)
        if self._persist_critical:
            try:
                from selfhealing.audit.persistence.disk_buffer import get_disk_buffer

                buffer = get_disk_buffer()
                buffer.put(
                    {
                        "type": "hedging_mismatch",
                        "operation_id": record.operation_id,
                        "mismatch_type": record.mismatch_type,
                        "winner_source": record.winner_source,
                        "other_source": record.other_source,
                        "winner_region": record.winner_region,
                        "other_region": record.other_region,
                        "detected_at": record.detected_at.isoformat(),
                    }
                )
                logger.debug(f"[HedgingValidator] Persisted to DiskBuffer: " f"{record.operation_id}")
            except ImportError:
                pass  # 영속화 모듈 없음
            except Exception as e:
                logger.warning(f"[HedgingValidator] DiskBuffer write failed: {e}")

    def get_mismatch_stats(self) -> dict:
        """
        불일치 통계 반환.

        Returns:
            total, by_type, by_region_pair, recent_5 등을 포함한 딕셔너리
        """
        with self._lock:
            if not self._mismatch_history:
                return {"total": 0}

            by_type: dict[str, int] = {}
            by_region_pair: dict[str, int] = {}

            for record in self._mismatch_history:
                # 유형별 집계
                by_type[record.mismatch_type] = by_type.get(record.mismatch_type, 0) + 1

                # 리전 쌍별 집계
                if record.winner_region and record.other_region:
                    pair = f"{record.winner_region}<->{record.other_region}"
                    by_region_pair[pair] = by_region_pair.get(pair, 0) + 1

            return {
                "total": len(self._mismatch_history),
                "by_type": by_type,
                "by_region_pair": by_region_pair,
                "recent_5": [
                    {
                        "op": r.operation_id,
                        "winner": f"{r.winner_source}({r.winner_region})",
                        "other": f"{r.other_source}({r.other_region})",
                        "type": r.mismatch_type,
                    }
                    for r in self._mismatch_history[-5:]
                ],
            }

    def shutdown(self) -> None:
        """검증기 종료. 대기 중인 작업 완료 후 종료."""
        self._executor.shutdown(wait=True)

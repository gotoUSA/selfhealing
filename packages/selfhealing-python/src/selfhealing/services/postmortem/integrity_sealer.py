"""
IntegritySealer - HashChain 기반 Postmortem 무결성 봉인.

Postmortem 생성 후 HashChainManager를 통해 불변성을 봉인하여
데이터 위변조를 감지할 수 있도록 합니다.

Features:
- Postmortem 데이터에 무결성 필드 추가 (sequence, prev_hash, current_hash)
- 봉인 시각 기록
- 체인 검증 API

Usage:
    sealer = get_integrity_sealer()
    sealed_postmortem = sealer.seal(postmortem_data)
    # sealed_postmortem에 integrity_* 필드 추가됨
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class IntegritySealer:
    """
    Postmortem 무결성 봉인기.

    HashChainManager를 사용하여 Postmortem 데이터에 무결성 필드를 추가합니다.

    무결성 필드:
    - integrity_sequence: 체인 시퀀스 번호
    - integrity_prev_hash: 이전 레코드 해시
    - integrity_hash: 현재 레코드 해시
    - sealed_at: 봉인 시각 (ISO format)

    Usage:
        sealer = get_integrity_sealer()
        postmortem = {"incident_id": "AUTO-payment-...", ...}
        sealed = sealer.seal(postmortem)
        # sealed에 무결성 필드 추가됨
    """

    def __init__(self, state_file: Path | None = None):
        """
        Initialize IntegritySealer.

        Args:
            state_file: HashChain 상태 파일 경로 (없으면 기본 경로 사용)
        """
        self._hash_chain_manager = None
        self._state_file = state_file

    def _get_hash_chain_manager(self):
        """HashChainManager lazy 로드."""
        if self._hash_chain_manager is None:
            from selfhealing.audit.integrity.local_manager import HashChainManager

            if self._state_file:
                self._hash_chain_manager = HashChainManager(state_file=self._state_file)
            else:
                # 기본 상태 파일 경로
                try:
                    from selfhealing.settings import get_config

                    config = get_config()
                    base_dir = getattr(config, "data_dir", Path("./data"))
                    state_file = Path(base_dir) / "postmortem_chain_state.json"
                except Exception:
                    state_file = Path("./data/postmortem_chain_state.json")

                self._hash_chain_manager = HashChainManager(state_file=state_file)

        return self._hash_chain_manager

    def seal(self, postmortem: dict[str, Any]) -> dict[str, Any]:
        """
        Postmortem 데이터에 무결성 필드 추가.

        Args:
            postmortem: Postmortem 데이터 딕셔너리

        Returns:
            무결성 필드가 추가된 Postmortem 딕셔너리
        """
        try:
            hash_chain = self._get_hash_chain_manager()

            # 무결성 필드 추가
            sealed = hash_chain.add_integrity(postmortem.copy())

            # 봉인 시각 추가
            sealed_at = datetime.now(timezone.utc).isoformat()

            # integrity 필드를 최상위로 이동
            integrity_info = sealed.pop("integrity", {})

            sealed["integrity_sequence"] = integrity_info.get("sequence")
            sealed["integrity_prev_hash"] = integrity_info.get("previous_hash")
            sealed["integrity_hash"] = integrity_info.get("current_hash")
            sealed["sealed_at"] = sealed_at

            logger.info(
                "integrity_sealer.postmortem_sealed",
                postmortem=postmortem.get('incident_id'),
                integrity_info=integrity_info.get('sequence'),
            )

            return sealed

        except Exception as e:
            logger.exception(
                "integrity_sealer.failed_seal_postmortem",
                error=e,
            )
            # 봉인 실패해도 원본 반환 (무결성 필드 없음)
            return postmortem

    def verify(self, postmortem: dict[str, Any]) -> tuple[bool, str]:
        """
        Postmortem 무결성 검증.

        단일 레코드의 해시를 재계산하여 저장된 해시와 비교합니다.

        Args:
            postmortem: 검증할 Postmortem 데이터

        Returns:
            (is_valid, message) 튜플
        """
        try:
            from selfhealing.audit.integrity.models import compute_hash

            # 무결성 필드 추출
            stored_hash = postmortem.get("integrity_hash")
            if not stored_hash:
                return False, "No integrity_hash found"

            # 해시 재계산을 위해 무결성 필드 제거
            data_for_hash = postmortem.copy()
            data_for_hash.pop("integrity_hash", None)
            data_for_hash.pop("sealed_at", None)

            # integrity 필드 재구성 (compute_hash가 기대하는 형식)
            data_for_hash["integrity"] = {
                "sequence": postmortem.get("integrity_sequence"),
                "previous_hash": postmortem.get("integrity_prev_hash"),
                "timestamp": postmortem.get("sealed_at"),
            }
            data_for_hash.pop("integrity_sequence", None)
            data_for_hash.pop("integrity_prev_hash", None)

            # 해시 재계산
            computed_hash = compute_hash(data_for_hash)

            if computed_hash == stored_hash:
                return True, "Integrity verified"
            else:
                return False, f"Hash mismatch: stored={stored_hash[:16]}..., computed={computed_hash[:16]}..."

        except Exception as e:
            return False, f"Verification error: {e}"

    def verify_chain(
        self,
        postmortems: list[dict[str, Any]],
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """
        Postmortem 체인 무결성 검증.

        연속된 Postmortem들의 해시 체인이 유효한지 확인합니다.

        Args:
            postmortems: 시퀀스 순서로 정렬된 Postmortem 리스트

        Returns:
            (is_valid, message, broken_records) 튜플
        """
        broken_records = []

        if not postmortems:
            return True, "Empty chain", []

        # 시퀀스 순서 정렬
        sorted_postmortems = sorted(
            postmortems,
            key=lambda p: p.get("integrity_sequence", 0),
        )

        prev_hash = "GENESIS"

        for pm in sorted_postmortems:
            # 1. 개별 레코드 무결성 검증
            is_valid, msg = self.verify(pm)
            if not is_valid:
                broken_records.append(
                    {
                        "incident_id": pm.get("incident_id"),
                        "sequence": pm.get("integrity_sequence"),
                        "error": msg,
                        "error_type": "hash_mismatch",
                    }
                )
                continue

            # 2. 체인 연결 검증
            stored_prev_hash = pm.get("integrity_prev_hash")
            if stored_prev_hash != prev_hash:
                broken_records.append(
                    {
                        "incident_id": pm.get("incident_id"),
                        "sequence": pm.get("integrity_sequence"),
                        "error": f"Chain broken: expected prev_hash={prev_hash[:16]}..., got={stored_prev_hash[:16] if stored_prev_hash else 'None'}...",
                        "error_type": "chain_broken",
                    }
                )

            # 다음 레코드를 위해 현재 해시 저장
            prev_hash = pm.get("integrity_hash", "")

        if broken_records:
            return (
                False,
                f"Chain verification failed: {len(broken_records)} broken records",
                broken_records,
            )

        return True, f"Chain verified: {len(sorted_postmortems)} records", []

    def get_chain_state(self) -> dict[str, Any]:
        """현재 체인 상태 조회."""
        try:
            hash_chain = self._get_hash_chain_manager()
            return hash_chain.get_state()
        except Exception as e:
            return {"error": str(e)}

    def reset(self) -> None:
        """체인 상태 초기화 (테스트용 - 주의!)."""
        try:
            hash_chain = self._get_hash_chain_manager()
            hash_chain.reset()
            logger.warning("integrity_sealer.chain_state_reset")
        except Exception as e:
            logger.exception(
                "integrity_sealer.failed_reset_chain",
                error=e,
            )


# 싱글톤 인스턴스
_integrity_sealer: IntegritySealer | None = None


def get_integrity_sealer() -> IntegritySealer:
    """Get singleton IntegritySealer instance."""
    global _integrity_sealer
    if _integrity_sealer is None:
        _integrity_sealer = IntegritySealer()
    return _integrity_sealer


def reset_integrity_sealer() -> None:
    """Reset singleton (for testing)."""
    global _integrity_sealer
    _integrity_sealer = None


__all__ = [
    "IntegritySealer",
    "get_integrity_sealer",
    "reset_integrity_sealer",
]

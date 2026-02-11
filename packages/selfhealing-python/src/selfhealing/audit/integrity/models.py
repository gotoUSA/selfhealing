"""
Integrity Models and Core Functions.

Contains:
- IntegrityInfo: Dataclass for integrity information
- compute_hash: SHA-256 hash computation for dictionaries
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class IntegrityInfo:
    """Integrity information for a log entry."""

    sequence: int
    previous_hash: str
    current_hash: str
    timestamp: str


def compute_hash(data: dict[str, Any]) -> str:
    """
    Compute SHA-256 hash of a dictionary.

    Args:
        data: Dictionary to hash

    Returns:
        SHA-256 hash string
    """
    # Sort keys for deterministic hashing
    json_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(json_str.encode()).hexdigest()


def canonical_json_bytes(data: dict[str, Any]) -> bytes:
    """
    정규화된 JSON 직렬화 (Merkle 해시 계산용).

    MerkleSpotChecker의 빌드/검증에서 동일한 바이트열을 보장합니다.
    기존 compute_hash()는 하위 호환성을 위해 변경하지 않습니다.

    직렬화 규칙:
        sort_keys=True: 키 순서 결정적
        default=str: datetime 등 비직렬화 타입 처리
        separators=(",", ":"): 공백 없는 컴팩트 출력
        ensure_ascii=False: UTF-8 원본 보존
    """
    return json.dumps(
        data,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


__all__ = ["IntegrityInfo", "compute_hash", "canonical_json_bytes"]

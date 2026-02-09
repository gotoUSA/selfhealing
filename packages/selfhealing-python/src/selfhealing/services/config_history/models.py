"""
Configuration History - Data Models.
"""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ConfigVersion:
    """설정 버전 정보."""

    version: int
    timestamp: float
    config_type: str
    values: dict[str, Any]
    changed_by: str
    reason: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigVersion":
        """딕셔너리에서 생성."""
        return cls(**data)

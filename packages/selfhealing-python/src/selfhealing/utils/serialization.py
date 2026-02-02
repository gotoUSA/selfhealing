# packages/selfhealing-python/src/selfhealing/utils/serialization.py
"""
고속 JSON 직렬화 유틸리티.

orjson 사용 가능 시 자동 활용, 없으면 표준 json 폴백.
WAL 및 Audit 파이프라인에서 성능 최적화를 위해 사용.

주요 특징:
- orjson: 10-20배 빠른 직렬화
- 자동 폴백: orjson 미설치 시 표준 json 사용
- bytes 반환: 파일/네트워크 I/O에 적합

Usage:
    from selfhealing.utils.serialization import fast_dumps, fast_loads

    # 직렬화
    data = {"event": "config_change", "key": "max_retries"}
    encoded = fast_dumps(data)  # bytes

    # 역직렬화
    decoded = fast_loads(encoded)  # dict
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "fast_dumps",
    "fast_loads",
    "FAST_JSON_AVAILABLE",
]

# orjson 사용 가능 여부 확인
try:
    import orjson

    def fast_dumps(obj: Any) -> bytes:
        """
        고속 JSON 직렬화 (bytes 반환).

        orjson이 설치된 경우 사용하며, 표준 json 대비 10-20배 빠름.

        Args:
            obj: 직렬화할 객체

        Returns:
            UTF-8 인코딩된 JSON bytes
        """
        return orjson.dumps(obj)

    def fast_loads(data: bytes | str) -> Any:
        """
        고속 JSON 역직렬화.

        Args:
            data: JSON bytes 또는 문자열

        Returns:
            역직렬화된 Python 객체
        """
        return orjson.loads(data)

    FAST_JSON_AVAILABLE = True

except ImportError:
    def fast_dumps(obj: Any) -> bytes:
        """
        표준 JSON 직렬화 (bytes 반환).

        orjson 미설치 시 표준 라이브러리 사용.

        Args:
            obj: 직렬화할 객체

        Returns:
            UTF-8 인코딩된 JSON bytes
        """
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def fast_loads(data: bytes | str) -> Any:
        """
        표준 JSON 역직렬화.

        Args:
            data: JSON bytes 또는 문자열

        Returns:
            역직렬화된 Python 객체
        """
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    FAST_JSON_AVAILABLE = False


def fast_dumps_str(obj: Any) -> str:
    """
    고속 JSON 직렬화 (문자열 반환).

    Args:
        obj: 직렬화할 객체

    Returns:
        JSON 문자열
    """
    result = fast_dumps(obj)
    if isinstance(result, bytes):
        return result.decode("utf-8")
    return result

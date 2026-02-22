"""
Toxiproxy Client for Chaos Engineering

업계 표준 Toxiproxy를 사용한 네트워크 장애 주입 클라이언트.
Netflix, Shopify, GitHub 등에서 사용하는 Chaos Engineering 패턴.

사용 예:
    client = ToxiproxyClient("http://toxiproxy:8474")

    # Redis 연결 끊기
    client.add_toxic("redis", "timeout", {"timeout": 0})

    # Redis 느리게 만들기 (1초 지연)
    client.add_toxic("redis", "latency", {"latency": 1000})

    # 장애 제거
    client.remove_toxic("redis", "timeout")
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, List
import requests

logger = logging.getLogger(__name__)


class ToxicType(str, Enum):
    """Toxiproxy 장애 타입"""

    # 연결 장애
    TIMEOUT = "timeout"  # 연결 타임아웃 (완전 차단)
    RESET_PEER = "reset_peer"  # 연결 리셋 (TCP RST)

    # 지연 장애
    LATENCY = "latency"  # 고정 지연
    SLOW_CLOSE = "slow_close"  # 느린 연결 종료

    # 데이터 장애
    BANDWIDTH = "bandwidth"  # 대역폭 제한
    SLICER = "slicer"  # 패킷 분할
    LIMIT_DATA = "limit_data"  # 데이터 제한 후 연결 종료


class ToxicStream(str, Enum):
    """독성 적용 방향"""

    UPSTREAM = "upstream"  # 클라이언트 → 서버
    DOWNSTREAM = "downstream"  # 서버 → 클라이언트


@dataclass
class Toxic:
    """Toxic 설정"""

    name: str
    type: ToxicType
    stream: ToxicStream = ToxicStream.DOWNSTREAM
    toxicity: float = 1.0  # 0.0 ~ 1.0 (적용 확률)
    attributes: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value,
            "stream": self.stream.value,
            "toxicity": self.toxicity,
            "attributes": self.attributes or {},
        }


@dataclass
class Proxy:
    """Toxiproxy 프록시 정보"""

    name: str
    listen: str
    upstream: str
    enabled: bool = True
    toxics: List[Toxic] = None


class ToxiproxyClient:
    """Toxiproxy HTTP API 클라이언트"""

    def __init__(self, base_url: str = "http://localhost:8474"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    # =========================================================================
    # Proxy 관리
    # =========================================================================

    def list_proxies(self) -> Dict[str, Proxy]:
        """모든 프록시 목록 조회"""
        resp = self.session.get(f"{self.base_url}/proxies")
        resp.raise_for_status()

        result = {}
        for name, data in resp.json().items():
            result[name] = Proxy(
                name=data["name"],
                listen=data["listen"],
                upstream=data["upstream"],
                enabled=data["enabled"],
            )
        return result

    def create_proxy(self, name: str, listen: str, upstream: str) -> Proxy:
        """새 프록시 생성"""
        resp = self.session.post(f"{self.base_url}/proxies", json={"name": name, "listen": listen, "upstream": upstream})
        resp.raise_for_status()
        data = resp.json()
        return Proxy(name=data["name"], listen=data["listen"], upstream=data["upstream"])

    def delete_proxy(self, name: str) -> bool:
        """프록시 삭제"""
        resp = self.session.delete(f"{self.base_url}/proxies/{name}")
        return resp.status_code == 204

    def enable_proxy(self, name: str) -> bool:
        """프록시 활성화"""
        resp = self.session.post(f"{self.base_url}/proxies/{name}", json={"enabled": True})
        return resp.status_code == 200

    def disable_proxy(self, name: str) -> bool:
        """프록시 비활성화 (완전 차단)"""
        resp = self.session.post(f"{self.base_url}/proxies/{name}", json={"enabled": False})
        return resp.status_code == 200

    # =========================================================================
    # Toxic (장애) 관리
    # =========================================================================

    def list_toxics(self, proxy_name: str) -> List[Dict[str, Any]]:
        """프록시의 모든 toxic 목록"""
        resp = self.session.get(f"{self.base_url}/proxies/{proxy_name}/toxics")
        resp.raise_for_status()
        return resp.json()

    def add_toxic(
        self,
        proxy_name: str,
        toxic_type: ToxicType | str,
        attributes: Dict[str, Any],
        name: str = None,
        stream: ToxicStream = ToxicStream.DOWNSTREAM,
        toxicity: float = 1.0,
    ) -> Dict[str, Any]:
        """
        프록시에 장애 추가

        Args:
            proxy_name: 프록시 이름 (예: "redis", "postgres")
            toxic_type: 장애 타입
            attributes: 장애 속성 (타입별로 다름)
            name: 장애 이름 (기본값: 자동 생성)
            stream: 적용 방향
            toxicity: 적용 확률 (0.0 ~ 1.0)

        Returns:
            생성된 toxic 정보

        Examples:
            # Redis 완전 차단
            client.add_toxic("redis", "timeout", {"timeout": 0})

            # PostgreSQL 1초 지연
            client.add_toxic("postgres", "latency", {"latency": 1000, "jitter": 100})

            # 50% 확률로 연결 리셋
            client.add_toxic("redis", "reset_peer", {"timeout": 0}, toxicity=0.5)
        """
        if isinstance(toxic_type, ToxicType):
            toxic_type = toxic_type.value

        if name is None:
            name = f"{proxy_name}_{toxic_type}_{int(time.time())}"

        payload = {
            "name": name,
            "type": toxic_type,
            "stream": stream.value if isinstance(stream, ToxicStream) else stream,
            "toxicity": toxicity,
            "attributes": attributes,
        }

        resp = self.session.post(f"{self.base_url}/proxies/{proxy_name}/toxics", json=payload)
        resp.raise_for_status()
        logger.info(f"Added toxic: {proxy_name}/{name} ({toxic_type})")
        return resp.json()

    def remove_toxic(self, proxy_name: str, toxic_name: str) -> bool:
        """장애 제거"""
        resp = self.session.delete(f"{self.base_url}/proxies/{proxy_name}/toxics/{toxic_name}")
        if resp.status_code == 204:
            logger.info(f"Removed toxic: {proxy_name}/{toxic_name}")
            return True
        return False

    def remove_all_toxics(self, proxy_name: str) -> int:
        """프록시의 모든 장애 제거"""
        toxics = self.list_toxics(proxy_name)
        removed = 0
        for toxic in toxics:
            if self.remove_toxic(proxy_name, toxic["name"]):
                removed += 1
        return removed

    # =========================================================================
    # 편의 메서드 - 일반적인 장애 시나리오
    # =========================================================================

    def simulate_connection_timeout(self, proxy_name: str, name: str = None) -> Dict:
        """연결 타임아웃 시뮬레이션 (완전 차단)"""
        return self.add_toxic(
            proxy_name, ToxicType.TIMEOUT, {"timeout": 0}, name=name or f"{proxy_name}_timeout"  # 0 = 무한 대기
        )

    def simulate_latency(self, proxy_name: str, latency_ms: int, jitter_ms: int = 0, name: str = None) -> Dict:
        """네트워크 지연 시뮬레이션"""
        return self.add_toxic(
            proxy_name, ToxicType.LATENCY, {"latency": latency_ms, "jitter": jitter_ms}, name=name or f"{proxy_name}_latency"
        )

    def simulate_connection_reset(self, proxy_name: str, probability: float = 1.0, name: str = None) -> Dict:
        """연결 리셋 시뮬레이션"""
        return self.add_toxic(
            proxy_name, ToxicType.RESET_PEER, {"timeout": 0}, name=name or f"{proxy_name}_reset", toxicity=probability
        )

    def simulate_bandwidth_limit(self, proxy_name: str, rate_kb: int, name: str = None) -> Dict:
        """대역폭 제한 시뮬레이션"""
        return self.add_toxic(proxy_name, ToxicType.BANDWIDTH, {"rate": rate_kb}, name=name or f"{proxy_name}_bandwidth")

    # =========================================================================
    # Partial Partition 시나리오
    # =========================================================================

    def simulate_redis_down(self) -> Dict:
        """Redis만 다운 시뮬레이션"""
        return self.simulate_connection_timeout("redis", "redis_partition")

    def simulate_db_down(self) -> Dict:
        """PostgreSQL만 다운 시뮬레이션"""
        return self.simulate_connection_timeout("postgres", "postgres_partition")

    def simulate_redis_slow(self, latency_ms: int = 2000) -> Dict:
        """Redis 느림 시뮬레이션"""
        return self.simulate_latency("redis", latency_ms, name="redis_slow")

    def simulate_db_slow(self, latency_ms: int = 3000) -> Dict:
        """PostgreSQL 느림 시뮬레이션"""
        return self.simulate_latency("postgres", latency_ms, name="postgres_slow")

    def recover_all(self) -> Dict[str, int]:
        """모든 장애 제거 (복구)"""
        result = {}
        for proxy_name in self.list_proxies():
            removed = self.remove_all_toxics(proxy_name)
            result[proxy_name] = removed
        logger.info(f"Recovered all proxies: {result}")
        return result

    # =========================================================================
    # Context Manager 지원
    # =========================================================================

    def chaos_context(self, proxy_name: str, toxic_type: ToxicType, attributes: Dict):
        """
        Context manager for temporary chaos injection

        Usage:
            with client.chaos_context("redis", ToxicType.TIMEOUT, {"timeout": 0}):
                # Redis가 다운된 상태에서 테스트
                response = app.get("/api/products/")
            # 자동으로 복구됨
        """
        return ChaosContext(self, proxy_name, toxic_type, attributes)


class ChaosContext:
    """Chaos 테스트를 위한 컨텍스트 매니저"""

    def __init__(self, client: ToxiproxyClient, proxy_name: str, toxic_type: ToxicType, attributes: Dict):
        self.client = client
        self.proxy_name = proxy_name
        self.toxic_type = toxic_type
        self.attributes = attributes
        self.toxic_name = None

    def __enter__(self):
        result = self.client.add_toxic(self.proxy_name, self.toxic_type, self.attributes)
        self.toxic_name = result["name"]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.toxic_name:
            self.client.remove_toxic(self.proxy_name, self.toxic_name)
        return False


# =============================================================================
# 편의 함수
# =============================================================================


def get_toxiproxy_client(url: str = None) -> ToxiproxyClient:
    """환경변수 또는 기본값으로 클라이언트 생성"""
    import os

    url = url or os.environ.get("TOXIPROXY_URL", "http://localhost:8474")
    return ToxiproxyClient(url)

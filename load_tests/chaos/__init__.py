"""
load_tests.chaos - 카오스 엔지니어링

제어된 장애 주입 모듈:
- FaultInjector: 애플리케이션 레벨 장애 주입
- ToxiproxyClient: 네트워크 레벨 장애 주입 (업계 표준)
"""

from .fault_injector import FaultInjector, FaultType
from .toxiproxy_client import (
    ToxiproxyClient,
    ToxicType,
    ToxicStream,
    Toxic,
    Proxy,
    ChaosContext,
    get_toxiproxy_client,
)

__all__ = [
    # Application-level fault injection
    "FaultInjector",
    "FaultType",
    # Network-level fault injection (Toxiproxy)
    "ToxiproxyClient",
    "ToxicType",
    "ToxicStream",
    "Toxic",
    "Proxy",
    "ChaosContext",
    "get_toxiproxy_client",
]

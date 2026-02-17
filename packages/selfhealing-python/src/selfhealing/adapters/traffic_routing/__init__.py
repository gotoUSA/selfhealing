"""
Traffic Routing Adapters.

TrafficRoutingAdapter 인터페이스의 구현체들을 제공합니다.

기본 제공:
- LoggingTrafficRoutingAdapter: 로깅 + 앱 레벨 이벤트 발행만 수행

프로덕션 구현체(Route53, GCP LB 등)는 호스트 앱에서 구현하여
ProviderRegistry.register_traffic_routing()으로 등록합니다.
"""

from selfhealing.adapters.traffic_routing.logging_adapter import (
    LoggingTrafficRoutingAdapter,
)

__all__ = [
    "LoggingTrafficRoutingAdapter",
]

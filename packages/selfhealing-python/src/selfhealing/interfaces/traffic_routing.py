"""
Traffic Routing Adapter Interface.

리전 장애 시 트래픽 전환을 위한 추상 인터페이스.

구현 예:
- AWS Route53 (boto3)
- GCP Global Load Balancer (google-cloud-compute)
- Cloudflare DNS (cloudflare)
- Kubernetes Ingress (kubernetes)
- 앱 레벨 라우팅 (ServiceLocalityRouter 연동)

기본 구현은 로깅만 수행합니다 (LoggingTrafficRoutingAdapter).
프로덕션에서는 사용자가 ProviderRegistry에 어댑터를 등록합니다.

Usage:
    from selfhealing.interfaces.traffic_routing import (
        TrafficRoutingAdapter,
        RoutingChange,
    )

    # 커스텀 어댑터 구현
    class Route53TrafficRouter(TrafficRoutingAdapter):
        def __init__(self, hosted_zone_id: str):
            self._client = boto3.client('route53')
            self._zone_id = hosted_zone_id

        def switch_primary(self, from_region, to_region) -> RoutingChange:
            self._client.change_resource_record_sets(...)
            return RoutingChange(success=True, ...)

        def rollback(self, routing_change) -> bool:
            ...

        def get_current_routing(self) -> dict:
            ...

    # 등록
    from selfhealing.factory import ProviderRegistry
    ProviderRegistry.register_traffic_routing("route53", Route53TrafficRouter)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoutingChange:
    """
    트래픽 라우팅 변경 결과.

    switch_primary() 호출 결과를 담으며,
    rollback() 호출 시 이전 상태 복원에 필요한 정보를 포함합니다.

    Attributes:
        success: 전환 성공 여부
        from_region: 이전 Primary 리전
        to_region: 새 Primary 리전
        details: 전환 상세 정보
        rollback_info: 롤백용 이전 상태 정보
    """

    success: bool
    """전환 성공 여부."""

    from_region: str
    """이전 Primary 리전."""

    to_region: str
    """새 Primary 리전."""

    details: dict[str, Any] = field(default_factory=dict)
    """전환 상세 정보."""

    rollback_info: dict[str, Any] | None = None
    """롤백용 이전 상태 정보."""


class TrafficRoutingAdapter(ABC):
    """
    트래픽 라우팅 어댑터 인터페이스.

    리전 장애 시 DNS/LB 레벨에서 트래픽을 전환하는 추상 인터페이스입니다.
    selfhealing 패키지는 외부 클라우드 SDK를 포함하지 않으므로,
    프로덕션 어댑터는 호스트 앱에서 구현하여 ProviderRegistry에 등록합니다.

    기본 구현 (LoggingTrafficRoutingAdapter):
        DNS/LB 변경 없이 앱 레벨에서만 동작합니다.
        RedisEventBus로 REGION_PRIMARY_CHANGED 이벤트를 발행하여
        ServiceLocalityRouter가 라우팅 테이블을 갱신합니다.

    Example (AWS Route53):
        class Route53TrafficRouter(TrafficRoutingAdapter):
            def __init__(self, hosted_zone_id: str):
                self._client = boto3.client('route53')
                self._zone_id = hosted_zone_id

            def switch_primary(self, from_region, to_region) -> RoutingChange:
                self._client.change_resource_record_sets(
                    HostedZoneId=self._zone_id,
                    ChangeBatch={...}
                )
                return RoutingChange(
                    success=True,
                    from_region=from_region,
                    to_region=to_region,
                    details={"dns_updated": True},
                )

            def rollback(self, routing_change) -> bool:
                return self.switch_primary(
                    routing_change.to_region,
                    routing_change.from_region,
                ).success

            def get_current_routing(self) -> dict:
                return {"hosted_zone": self._zone_id}

    Example (K8s Ingress):
        class K8sIngressTrafficRouter(TrafficRoutingAdapter):
            def switch_primary(self, from_region, to_region) -> RoutingChange:
                # kubectl patch ingress ...
                ...

    등록:
        ProviderRegistry.register_traffic_routing("route53", Route53TrafficRouter)
    """

    @abstractmethod
    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        """
        Primary 리전 전환.

        DNS/LB 레벨에서 트래픽을 to_region으로 전환합니다.

        Args:
            from_region: 현재 Primary 리전
            to_region: 새 Primary 리전

        Returns:
            RoutingChange 결과 (롤백 정보 포함)
        """
        pass

    @abstractmethod
    def rollback(self, routing_change: RoutingChange) -> bool:
        """
        라우팅 변경 롤백.

        Args:
            routing_change: switch_primary() 반환값

        Returns:
            True if 롤백 성공
        """
        pass

    @abstractmethod
    def get_current_routing(self) -> dict[str, Any]:
        """현재 라우팅 상태 조회."""
        pass

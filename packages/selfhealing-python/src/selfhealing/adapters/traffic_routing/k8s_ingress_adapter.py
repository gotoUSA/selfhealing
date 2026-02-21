"""
K8s Ingress Traffic Routing Adapter — 단일 클러스터 참조 구현.

동일 K8s 클러스터 내 backend Service 전환(Blue/Green)용 어댑터.
Ingress spec.rules의 backend.service.name을 직접 교체하여
실제 트래픽을 전환합니다.

적용 범위:
- 단일 클러스터 내 서비스 격리 (Blue/Green, Canary)
- 리전 간 Failover에는 Route53/Global LB 어댑터를 사용할 것

요구사항:
- kubernetes 패키지 설치
- Ingress에 대한 get/patch RBAC 권한
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from selfhealing.interfaces.traffic_routing import (
    RoutingChange,
    TrafficRoutingAdapter,
)

logger = logging.getLogger(__name__)


class K8sIngressTrafficRoutingAdapter(TrafficRoutingAdapter):
    """
    K8s Ingress 기반 트래픽 라우팅 어댑터 (단일 클러스터 참조 구현).

    Ingress spec.rules의 backend.service.name을 직접 교체하여
    동일 클러스터 내 서비스 간 트래픽을 전환합니다.
    이 어댑터의 적용 범위는 단일 K8s 클러스터 내 서비스 전환(Blue/Green)이며,
    물리적 리전 간 Failover에는 Route53/Global LB 어댑터를 사용해야 합니다.
    """

    def __init__(
        self,
        ingress_name: str = "selfhealing-ingress",
        namespace: str = "selfhealing",
        region_service_map: dict[str, str] | None = None,
    ):
        """
        Args:
            ingress_name: Ingress 리소스 이름
            namespace: 네임스페이스
            region_service_map: 리전 → backend Service 이름 매핑
                예: {"ap-northeast-2": "app-apne2", "us-west-2": "app-usw2"}
        """
        self._ingress_name = ingress_name
        self._namespace = namespace
        self._region_service_map = region_service_map or {}
        self._networking_v1: Any = None
        self._is_available = False
        self._current_primary: str | None = None
        self._initialize_client()

    def _initialize_client(self) -> None:
        """K8s 클라이언트 초기화."""
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

            self._networking_v1 = client.NetworkingV1Api()
            self._is_available = True
        except ImportError:
            logger.warning("[K8sIngressTrafficRouter] kubernetes package not installed")
        except Exception as e:
            logger.warning(f"[K8sIngressTrafficRouter] Init failed: {e}")

    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        """
        Ingress backend Service를 to_region의 Service로 전환.

        기존 Ingress의 spec.rules를 읽어 backend.service.name만 선택적으로
        교체합니다. 기존 host/path 규칙, TLS 설정 등은 보존됩니다.
        추적용 annotation(selfhealing.io/primary-region)도 함께 업데이트합니다.

        Args:
            from_region: 현재 Primary 리전
            to_region: 새 Primary 리전

        Returns:
            RoutingChange 결과
        """
        if not self._is_available:
            return RoutingChange(
                success=False,
                from_region=from_region,
                to_region=to_region,
                details={"error": "K8s client not available"},
            )

        target_service = self._region_service_map.get(to_region)
        if not target_service:
            return RoutingChange(
                success=False,
                from_region=from_region,
                to_region=to_region,
                details={"error": f"No service mapped for region: {to_region}"},
            )

        try:
            # 현재 Ingress 상태 저장 (롤백용)
            current_ingress = self._networking_v1.read_namespaced_ingress(
                name=self._ingress_name,
                namespace=self._namespace,
            )

            # 롤백용 이전 상태 스냅샷
            rollback_info = {
                "previous_annotations": dict(current_ingress.metadata.annotations or {}),
                "previous_rules": copy.deepcopy(current_ingress.spec.rules),
                "previous_region": from_region,
            }

            # region_service_map에 등록된 서비스명 집합 (교체 대상 식별)
            known_services = set(self._region_service_map.values())

            # spec.rules의 backend.service.name 선택적 교체
            # 기존 host/path 규칙 구조는 보존, service.name만 변경
            patched_rules = copy.deepcopy(current_ingress.spec.rules)
            replaced_count = 0
            for rule in patched_rules or []:
                if rule.http is None:
                    continue
                for path_entry in rule.http.paths or []:
                    if path_entry.backend and path_entry.backend.service and path_entry.backend.service.name in known_services:
                        path_entry.backend.service.name = target_service
                        replaced_count += 1

            if replaced_count == 0:
                return RoutingChange(
                    success=False,
                    from_region=from_region,
                    to_region=to_region,
                    details={"error": ("No matching backend service found in " f"Ingress rules (known: {known_services})")},
                )

            # Ingress 패치: spec.rules + 추적용 annotation
            patch = {
                "metadata": {
                    "annotations": {
                        "selfhealing.io/primary-region": to_region,
                        "selfhealing.io/failover-timestamp": (datetime.now(timezone.utc).isoformat()),
                    }
                },
                "spec": {
                    "rules": patched_rules,
                },
            }

            self._networking_v1.patch_namespaced_ingress(
                name=self._ingress_name,
                namespace=self._namespace,
                body=patch,
            )

            self._current_primary = to_region
            logger.info(
                f"[K8sIngressTrafficRouter] Switched: "
                f"{from_region} → {to_region} "
                f"(ingress={self._ingress_name}, "
                f"replaced={replaced_count} backends)"
            )

            # 앱 레벨 이벤트 발행 — 실패해도 RoutingChange에 영향 없음
            self._publish_routing_event(from_region, to_region)

            return RoutingChange(
                success=True,
                from_region=from_region,
                to_region=to_region,
                details={
                    "level": "infrastructure",
                    "dns_updated": False,
                    "ingress_updated": True,
                    "ingress": self._ingress_name,
                    "target_service": target_service,
                    "replaced_backends": replaced_count,
                },
                rollback_info=rollback_info,
            )

        except Exception as e:
            logger.error(f"[K8sIngressTrafficRouter] Switch failed: {e}")
            return RoutingChange(
                success=False,
                from_region=from_region,
                to_region=to_region,
                details={"error": str(e)},
            )

    def rollback(self, routing_change: RoutingChange) -> bool:
        """RoutingChange의 rollback_info로 이전 상태 복원."""
        if not routing_change.rollback_info:
            return False
        previous_region = routing_change.rollback_info.get("previous_region")
        if not previous_region:
            return False
        result = self.switch_primary(routing_change.to_region, previous_region)
        return result.success

    def get_current_routing(self) -> dict[str, Any]:
        """현재 Ingress 라우팅 상태 조회."""
        if not self._is_available:
            return {"available": False}
        try:
            ingress = self._networking_v1.read_namespaced_ingress(
                name=self._ingress_name,
                namespace=self._namespace,
            )
            annotations = ingress.metadata.annotations or {}
            return {
                "available": True,
                "ingress_name": self._ingress_name,
                "primary_region": annotations.get("selfhealing.io/primary-region", "unknown"),
                "last_failover": annotations.get("selfhealing.io/failover-timestamp"),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _publish_routing_event(self, from_region: str, to_region: str) -> None:
        """
        앱 레벨 라우팅 이벤트 발행 (ServiceLocalityRouter 갱신용).

        이 메서드의 예외는 상위 switch_primary()의 RoutingChange 결과에
        영향을 주지 않는다. 인프라 라우팅(Ingress 패치)이 성공하면
        switch_primary()는 success=True를 반환한다.
        """
        try:
            from selfhealing.services.event_bus.bus import (
                EventType,
                SelfHealingEvent,
            )
            from selfhealing.services.event_bus.redis_bus import get_event_bus

            bus = get_event_bus(distributed=True)
            bus.publish(
                SelfHealingEvent(
                    event_type=EventType.REGION_PRIMARY_CHANGED,
                    data={
                        "key": "region_primary",
                        "value": to_region,
                        "previous": from_region,
                    },
                    source="failover",
                )
            )
        except Exception as e:
            logger.error(f"[K8sIngressTrafficRouter] Event publish failed: {e}")

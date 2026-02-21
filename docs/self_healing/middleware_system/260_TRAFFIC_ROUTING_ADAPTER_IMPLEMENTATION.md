# 260. TrafficRoutingAdapter 구현체 설계

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `adapters/traffic_routing/` — DNS/LB 레벨 어댑터 구현체

---

## 0. 요약

현재 `TrafficRoutingAdapter`의 유일한 구현체인 `LoggingTrafficRoutingAdapter`는 **앱 레벨 이벤트 발행만** 수행하며 DNS/LB를 변경하지 않는다. 프로덕션 환경에서 `RegionFailover._update_traffic_routing()`이 실제 트래픽을 전환하려면, 호스트 앱에서 구체적 어댑터를 구현하여 `ProviderRegistry`에 등록해야 한다.

본 문서는:
1. 기존 인터페이스와 호출 체인을 코드 근거로 정리
2. K8s Ingress 어댑터 구현 가이드 제공
3. 등록/사용 방법 명시

> **중요**: `selfhealing` 패키지는 외부 클라우드 SDK를 포함하지 않는다는 원칙에 따라,
> Route53/GCP LB 같은 클라우드 어댑터는 **호스트 앱에서 구현**한다.
> 패키지 내에는 K8s Ingress 기반 참조 구현만 제공한다.

---

## 1. 현재 상태 분석

### 1.1 인터페이스 — `TrafficRoutingAdapter`

**파일**: `packages/selfhealing-python/src/selfhealing/interfaces/traffic_routing.py` L131–L165

```python
class TrafficRoutingAdapter(ABC):
    @abstractmethod
    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        """DNS/LB 레벨에서 트래픽을 to_region으로 전환."""
        pass

    @abstractmethod
    def rollback(self, routing_change: RoutingChange) -> bool:
        """라우팅 변경 롤백."""
        pass

    @abstractmethod
    def get_current_routing(self) -> dict[str, Any]:
        """현재 라우팅 상태 조회."""
        pass
```

**`RoutingChange` 데이터클래스** (L55–L80):

```python
@dataclass
class RoutingChange:
    success: bool
    from_region: str
    to_region: str
    details: dict[str, Any] = field(default_factory=dict)
    rollback_info: dict[str, Any] | None = None
```

### 1.2 기존 구현체 — `LoggingTrafficRoutingAdapter`

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/traffic_routing/logging_adapter.py` L31–L107

```python
class LoggingTrafficRoutingAdapter(TrafficRoutingAdapter):
    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        logger.warning(
            f"[TrafficRouting] App-level routing update: "
            f"{from_region} → {to_region} "
            f"(DNS/LB adapter not configured)"
        )
        # RedisEventBus로 REGION_PRIMARY_CHANGED 이벤트 발행
        bus = get_event_bus(distributed=True)
        bus.publish(SelfHealingEvent(
            event_type=EventType.REGION_PRIMARY_CHANGED,
            data={"key": "region_primary", "value": to_region, "previous": from_region},
            source="failover",
        ))
        return RoutingChange(
            success=True,
            from_region=from_region,
            to_region=to_region,
            details={"level": "app_only", "dns_updated": False},
        )
```

**한계**: DNS/LB 변경 없음. `ServiceLocalityRouter`의 앱 레벨 라우팅만 갱신.

### 1.3 호출 체인 — `RegionFailover`

**파일**: `packages/selfhealing-python/src/selfhealing/multiregion/failover.py`

**`_update_traffic_routing()` L322–L341**:
```python
def _update_traffic_routing(self, target_region: str) -> None:
    adapter = self._get_traffic_routing_adapter()
    result = adapter.switch_primary(self._current_primary, target_region)
    if not result.success:
        raise RuntimeError(f"Traffic routing switch failed: {result.details}")
    self._last_routing_change = result
```

**`_get_traffic_routing_adapter()` L343–L368** — 조회 순서:
```
1) self._traffic_adapter (생성자 주입)
2) ProviderRegistry.get_traffic_routing()
3) LoggingTrafficRoutingAdapter() (최종 폴백)
```

### 1.4 ProviderRegistry 등록/조회

**파일**: `packages/selfhealing-python/src/selfhealing/factory.py`

```python
# 등록 (L44 class ProviderRegistry)
@classmethod
def register_traffic_routing(cls, name: str, adapter_class: type) -> None: ...

# 조회
@classmethod
def get_traffic_routing(cls, name=None, singleton=True) -> TrafficRoutingAdapter: ...
```

**기본 등록**: `_auto_register_adapters()` (L700)에서 `traffic_routing="logging"` 등록.

---

## 2. K8s Ingress 어댑터 — 참조 구현

### 2.1 파일 위치

```
packages/selfhealing-python/src/selfhealing/adapters/traffic_routing/
├── __init__.py
├── logging_adapter.py       # 기존
└── k8s_ingress_adapter.py   # 신규
```

### 2.2 구현 코드

```python
"""
K8s Ingress Traffic Routing Adapter.

K8s Ingress 리소스의 annotation/backend 수정을 통해
리전 간 트래픽을 전환합니다.

요구사항:
- kubernetes 패키지 설치
- Ingress에 대한 get/patch RBAC 권한
"""

from __future__ import annotations

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
    K8s Ingress 기반 트래픽 라우팅 어댑터.

    Ingress annotation을 수정하여 리전 간 트래픽을 전환합니다.
    NGINX Ingress Controller의 canary annotation 또는
    backend service 전환 방식을 지원합니다.
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
            logger.warning(
                "[K8sIngressTrafficRouter] kubernetes package not installed"
            )
        except Exception as e:
            logger.warning(f"[K8sIngressTrafficRouter] Init failed: {e}")

    def switch_primary(
        self, from_region: str, to_region: str
    ) -> RoutingChange:
        """
        Ingress backend Service를 to_region의 Service로 전환.

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
                details={
                    "error": f"No service mapped for region: {to_region}"
                },
            )

        try:
            # 현재 Ingress 상태 저장 (롤백용)
            current_ingress = self._networking_v1.read_namespaced_ingress(
                name=self._ingress_name,
                namespace=self._namespace,
            )
            rollback_info = {
                "previous_annotations": dict(
                    current_ingress.metadata.annotations or {}
                ),
                "previous_region": from_region,
            }

            # Ingress annotation 업데이트
            patch = {
                "metadata": {
                    "annotations": {
                        "selfhealing.io/primary-region": to_region,
                        "selfhealing.io/failover-timestamp": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }
                }
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
                f"(ingress={self._ingress_name})"
            )

            # 앱 레벨 이벤트도 발행 (LoggingAdapter 동작 포함)
            self._publish_routing_event(from_region, to_region)

            return RoutingChange(
                success=True,
                from_region=from_region,
                to_region=to_region,
                details={
                    "level": "infrastructure",
                    "dns_updated": True,
                    "ingress": self._ingress_name,
                    "target_service": target_service,
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
                "primary_region": annotations.get(
                    "selfhealing.io/primary-region", "unknown"
                ),
                "last_failover": annotations.get(
                    "selfhealing.io/failover-timestamp"
                ),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _publish_routing_event(
        self, from_region: str, to_region: str
    ) -> None:
        """앱 레벨 라우팅 이벤트 발행 (ServiceLocalityRouter 갱신용)."""
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
```

### 2.3 등록 방법

**방법 1: `_auto_register_adapters()` 추가** (factory.py L700)

```python
# factory.py _auto_register_adapters() 내부
from selfhealing.adapters.traffic_routing.k8s_ingress_adapter import (
    K8sIngressTrafficRoutingAdapter,
)
cls.register_traffic_routing("k8s_ingress", K8sIngressTrafficRoutingAdapter)
```

**방법 2: 호스트 앱 settings.py에서 등록** (권장)

```python
# Django settings.py 또는 AppConfig.ready()
from selfhealing.factory import ProviderRegistry
from selfhealing.adapters.traffic_routing.k8s_ingress_adapter import (
    K8sIngressTrafficRoutingAdapter,
)

ProviderRegistry.register_traffic_routing("k8s_ingress", K8sIngressTrafficRoutingAdapter)
```

**활성화**: 환경변수 `SELFHEALING_TRAFFIC_ROUTING_ADAPTER=k8s_ingress` 또는 ProviderRegistry에서 이름으로 조회.

---

## 3. 호스트 앱 구현 가이드 — Route53

패키지 내에 포함하지 않는 클라우드 SDk 기반 어댑터의 구현 가이드:

```python
# 호스트 앱 코드 (selfhealing 패키지 외부)
import boto3
from selfhealing.interfaces.traffic_routing import (
    RoutingChange,
    TrafficRoutingAdapter,
)

class Route53TrafficRouter(TrafficRoutingAdapter):
    def __init__(self, hosted_zone_id: str, record_name: str):
        self._client = boto3.client("route53")
        self._zone_id = hosted_zone_id
        self._record_name = record_name

    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        # Route53 Failover Record 업데이트
        ...

    def rollback(self, routing_change: RoutingChange) -> bool:
        return self.switch_primary(
            routing_change.to_region, routing_change.from_region
        ).success

    def get_current_routing(self) -> dict:
        return {"hosted_zone": self._zone_id, "record": self._record_name}

# 등록
from selfhealing.factory import ProviderRegistry
ProviderRegistry.register_traffic_routing("route53", Route53TrafficRouter)
```

이 패턴은 이미 `interfaces/traffic_routing.py`의 docstring (L26–L38)에 Route53 예제로 문서화되어 있다.

---

## 4. 호출 체인 정리

```
RegionFailover.trigger_failover()
  → _execute_failover()
    → _update_traffic_routing(target_region)
      → _get_traffic_routing_adapter()
         1) self._traffic_adapter (생성자 주입)
         2) ProviderRegistry.get_traffic_routing()  ← 여기서 "k8s_ingress" 반환
         3) LoggingTrafficRoutingAdapter()           ← 최종 폴백
      → adapter.switch_primary(current, target)
        → K8sIngressTrafficRoutingAdapter.switch_primary()
          → K8s Ingress patch + EventBus 이벤트 발행
    → _verify_data_consistency()
    → 상태 업데이트, 콜백, 알림
```

---

## 5. 변경 범위

| 파일 | 변경 | 상태 |
|------|------|------|
| `adapters/traffic_routing/k8s_ingress_adapter.py` | 신규 생성 | 선택적 |
| `adapters/traffic_routing/__init__.py` | export 추가 | 선택적 |
| `factory.py` `_auto_register_adapters()` | 등록 추가 (또는 호스트 앱에서) | 선택적 |

> **참고**: 이 어댑터는 **선택적 구현**이다.
> K8s 환경이 아니거나 DNS/LB 전환이 불필요한 경우,
> 기존 `LoggingTrafficRoutingAdapter`(앱 레벨 전환)만으로 충분하다.

---

## 6. RBAC 추가 요구사항 (K8s Ingress 어댑터 사용 시)

```yaml
# selfhealing-rbac.yaml에 추가
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "patch"]
```

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `interfaces/traffic_routing.py` | 인터페이스 정의 (변경 없음) |
| `adapters/traffic_routing/logging_adapter.py` | 기존 기본 구현 (변경 없음) |
| `multiregion/failover.py` | 호출부 (변경 없음) |
| `factory.py` | ProviderRegistry 등록 (선택적 변경) |

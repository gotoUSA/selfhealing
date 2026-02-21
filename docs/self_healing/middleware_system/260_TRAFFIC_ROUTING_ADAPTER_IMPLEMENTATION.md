# 260. TrafficRoutingAdapter 구현체 설계

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Implemented
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `adapters/traffic_routing/` — DNS/LB 레벨 어댑터 구현체

---

## 0. 요약

현재 `TrafficRoutingAdapter`의 유일한 구현체인 `LoggingTrafficRoutingAdapter`는 **앱 레벨 이벤트 발행만** 수행하며 DNS/LB를 변경하지 않는다. 프로덕션 환경에서 `RegionFailover._update_traffic_routing()`이 실제 트래픽을 전환하려면, 호스트 앱에서 구체적 어댑터를 구현하여 `ProviderRegistry`에 등록해야 한다.

본 문서는:
1. 기존 인터페이스와 호출 체인을 코드 근거로 정리
2. **DNS/Global LB 어댑터를 프로덕션 권장 아키텍처로 제시** (Route53 등)
3. K8s Ingress 어댑터를 단일 클러스터 내 참조 구현으로 제공
4. Secondary 리전의 Failover 주도 구조와 `failover.py` 코드 변경 명세
5. 이벤트 전파 보장형 전달(Guaranteed Delivery) 아키텍처 정의
6. 등록/사용 방법 명시

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

### 1.5 현재 Failover 주체의 구조적 한계

**문제**: 현재 `RegionFailover._check_and_failover()`는 **Primary 리전에서만** 실행된다.

**파일**: `multiregion/failover.py` L537–L553

```python
def _check_and_failover(self) -> None:
    """건강 상태 확인 및 자동 페일오버."""
    # Primary 리전 건강 확인 (자기 자신이 Primary일 때만)
    if not self._settings.is_primary():
        return
    # ...
    # (자기 자신이 죽은 경우는 감지 불가)
```

`_settings.is_primary()`가 `False`인 Secondary 리전은 조기 리턴하므로,
**Primary 리전 전체가 다운되면 아무도 `TrafficRoutingAdapter.switch_primary()`를 호출하지 않는다.**

이 문제의 해결은 2항에서 정의한다.

---

## 2. Secondary 리전의 Failover 주도 — `failover.py` 코드 변경

### 2.1 문제 정의

현재 `_check_and_failover()`는 Primary에서만 피어 리전 장애를 감시한다.
Primary(ap-northeast-2)가 완전히 다운되면:

1. Primary의 `RegionFailover` 루프 → 함께 사망
2. Secondary(us-west-2)의 `_check_and_failover()` → `is_primary()=False`로 조기 리턴
3. **누구도 페일오버를 트리거하지 않음**

### 2.2 필요한 변경 — `_check_and_failover()` 확장

Secondary 리전도 Primary의 건강 상태를 감시하고, Primary가 UNREACHABLE일 때
`QuorumWitness.try_acquire_primary()`를 통해 승격을 시도해야 한다.

`RegionHealthMonitor.get_all_health_states()`는 이미 모든 피어 리전을
주기적으로 체크하므로 (L108–L180), Secondary에서도 Primary의 상태를 알 수 있다.

**변경 대상**: `multiregion/failover.py` — `_check_and_failover()` L537

```python
def _check_and_failover(self) -> None:
    """건강 상태 확인 및 자동 페일오버."""
    all_health = self._health_monitor.get_all_health_states()

    if self._settings.is_primary():
        # Primary: 피어 리전 장애 감시 (기존 동작 유지)
        for region, health in all_health.items():
            if health.status == RegionHealthStatus.UNREACHABLE:
                logger.warning(f"[Failover] Peer region unreachable: {region}")
    else:
        # Secondary: Primary 건강 감시 → 승격 시도
        self._check_primary_and_promote(all_health)
```

### 2.3 새 메서드 — `_check_primary_and_promote()`

```python
def _check_primary_and_promote(
    self, all_health: dict[str, RegionHealth]
) -> None:
    """
    Secondary 리전에서 Primary 장애 감지 시 승격을 시도.

    승격 조건:
    1. Primary 리전이 UNREACHABLE 상태
    2. QuorumWitness 락 획득 성공 (Split-brain 방지)
    3. 페일오버 쿨다운 미초과

    QuorumWitness.try_acquire_primary()는 DynamoDB Global Table의
    조건부 쓰기(ConditionExpression)로 단 하나의 리전만 승격을 보장한다.
    """
    primary_region = self._current_primary

    # Primary 상태가 UNREACHABLE인지 확인
    primary_health = all_health.get(primary_region)
    if primary_health is None:
        return
    if primary_health.status != RegionHealthStatus.UNREACHABLE:
        return

    # 쿨다운 확인
    if not self._can_failover():
        return

    # Quorum 획득 시도 (Split-brain 방지)
    if not self._quorum_witness:
        logger.warning(
            "[Failover] QuorumWitness not configured, "
            "cannot promote from Secondary"
        )
        return

    if not self._quorum_witness.try_acquire_primary():
        logger.info(
            "[Failover] Quorum denied: another region "
            "may already be promoting"
        )
        return

    # 승격 성공 → 페일오버 실행
    logger.warning(
        f"[Failover] Secondary promoting: "
        f"{self._settings.current_region} taking over from "
        f"{primary_region}"
    )
    self._execute_failover(
        target_region=self._settings.current_region,
        reason=f"primary_unreachable:{primary_region}",
    )
```

### 2.4 Quorum 기반 Split-brain 방지

승격 시 `QuorumWitness.try_acquire_primary()` (`quorum.py` L157–L199)가
DynamoDB 조건부 쓰기로 **단 하나의 리전만** Primary 락을 획득할 수 있다:

```python
# quorum.py — 조건부 쓰기
self._dynamodb.put_item(
    TableName=self._table_name,
    Item={...},
    # 키가 없거나 TTL 만료 시에만 쓰기 성공
    ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
    ExpressionAttributeValues={":now": {"N": str(now)}},
)
```

여러 Secondary가 동시에 승격을 시도해도, DynamoDB의 강한 일관성 보장으로
**단 하나만 성공**한다. 실패한 리전은 `ConditionalCheckFailedException`을 받고 중단.

### 2.5 승격 후 호출 체인 (전체 Failover 흐름)

```
Secondary(us-west-2)의 RegionFailover._check_and_failover()
  → _check_primary_and_promote(all_health)
    → Primary(ap-northeast-2) UNREACHABLE 확인
    → QuorumWitness.try_acquire_primary() → 성공
    → _execute_failover(target=us-west-2, reason="primary_unreachable:ap-northeast-2")
      → _update_traffic_routing(us-west-2)
        → adapter.switch_primary("ap-northeast-2", "us-west-2")
          → Route53 Failover Record 전환 (또는 Global LB)
      → _verify_data_consistency(us-west-2)
      → 상태: FAILED_OVER, 콜백, 알림 전송
```

> **핵심**: 새로운 Primary(기존 Secondary)의 Failover 엔진이
> `TrafficRoutingAdapter.switch_primary()`를 실행한다.
> 어댑터가 DNS/Global LB를 변경하므로 장애 리전의 K8s API Server에 접근할 필요가 없다.

---

## 3. DNS/Global LB 어댑터 — 프로덕션 권장 아키텍처

### 3.1 왜 DNS/Global LB가 메인인가

리전 간 트래픽 전환의 진실의 원천(SSOT)은 **DNS**이다.

| 계층 | 장애 리전 K8s API 접근 | 리전 독립성 | 전파 방식 |
|------|----------------------|------------|----------|
| **DNS (Route53 등)** | 불필요 | 완전 독립 | DNS TTL (최대 60초) |
| K8s Ingress | **필요** | 동일 클러스터 한정 | 즉시 (단, 같은 클러스터) |
| App EventBus | 불필요 | Redis 의존 | Redis Pub/Sub |

`interfaces/traffic_routing.py`의 모듈 docstring (L6–L11)에서도 Route53을
첫 번째 구현례로 제시하고 있다:

```python
"""
구현 예:
- AWS Route53 (boto3)           ← 첫 번째
- GCP Global Load Balancer
- Cloudflare DNS
- Kubernetes Ingress            ← 네 번째
- 앱 레벨 라우팅
"""
```

### 3.2 Route53 어댑터 — 호스트 앱 구현 스펙

> `selfhealing` 패키지는 `boto3`를 포함하지 않으므로 호스트 앱에서 구현한다.
> 아래는 호스트 앱이 반드시 준수해야 할 **구현 스펙**이다.

```python
# 호스트 앱 코드 (selfhealing 패키지 외부)
import boto3
from selfhealing.interfaces.traffic_routing import (
    RoutingChange,
    TrafficRoutingAdapter,
)

class Route53TrafficRouter(TrafficRoutingAdapter):
    """
    Route53 Failover Record 기반 트래픽 전환.

    리전별 Health Check ID와 Failover Record를 사전 구성해야 함.
    switch_primary()는 지정 리전의 Health Check를 강제 HEALTHY로,
    이전 리전을 UNHEALTHY로 변경하여 DNS Failover를 유도한다.
    """

    def __init__(
        self,
        hosted_zone_id: str,
        record_name: str,
        region_health_check_map: dict[str, str] | None = None,
    ):
        self._client = boto3.client("route53")
        self._zone_id = hosted_zone_id
        self._record_name = record_name
        # 리전 → Health Check ID 매핑
        self._health_checks = region_health_check_map or {}

    def switch_primary(self, from_region: str, to_region: str) -> RoutingChange:
        """
        Route53 Failover Record 업데이트.

        방식: to_region의 Failover Record를 PRIMARY로,
        from_region을 SECONDARY로 변경.

        RoutingChange.rollback_info에 이전 상태를 저장하여
        rollback() 시 역방향 전환 가능.
        """
        try:
            # 현재 상태 저장 (롤백용)
            current = self._client.list_resource_record_sets(
                HostedZoneId=self._zone_id,
                StartRecordName=self._record_name,
                MaxItems="10",
            )
            rollback_info = {
                "previous_records": current["ResourceRecordSets"],
                "previous_region": from_region,
            }

            # Failover Record 업데이트
            self._client.change_resource_record_sets(
                HostedZoneId=self._zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": self._record_name,
                                "Type": "A",
                                "SetIdentifier": to_region,
                                "Failover": "PRIMARY",
                                "AliasTarget": {
                                    # 리전별 ELB/ALB DNS
                                },
                            },
                        }
                    ],
                },
            )

            # 앱 레벨 이벤트 발행 (Design Decision: 실패해도 DNS 전환은 유효)
            self._publish_routing_event(from_region, to_region)

            return RoutingChange(
                success=True,
                from_region=from_region,
                to_region=to_region,
                details={
                    "level": "dns",
                    "dns_updated": True,
                    "hosted_zone": self._zone_id,
                },
                rollback_info=rollback_info,
            )
        except Exception as e:
            return RoutingChange(
                success=False,
                from_region=from_region,
                to_region=to_region,
                details={"error": str(e)},
            )

    def rollback(self, routing_change: RoutingChange) -> bool:
        if not routing_change.rollback_info:
            return False
        previous_region = routing_change.rollback_info.get("previous_region")
        if not previous_region:
            return False
        return self.switch_primary(
            routing_change.to_region, previous_region
        ).success

    def get_current_routing(self) -> dict:
        return {
            "hosted_zone": self._zone_id,
            "record": self._record_name,
        }

    def _publish_routing_event(self, from_region: str, to_region: str) -> None:
        """앱 레벨 라우팅 이벤트 발행 — 6항 Design Decision 참조."""
        try:
            from selfhealing.services.event_bus.bus import (
                EventType, SelfHealingEvent,
            )
            from selfhealing.services.event_bus.redis_bus import get_event_bus

            bus = get_event_bus(distributed=True)
            bus.publish(SelfHealingEvent(
                event_type=EventType.REGION_PRIMARY_CHANGED,
                data={
                    "key": "region_primary",
                    "value": to_region,
                    "previous": from_region,
                },
                source="failover",
            ))
        except Exception as e:
            # Design Decision: 이벤트 발행 실패가 DNS 전환 결과에 영향을 주지 않음
            logger.error(f"[Route53TrafficRouter] Event publish failed: {e}")
```

**등록** (호스트 앱 `AppConfig.ready()` 또는 `settings.py`):

```python
from selfhealing.factory import ProviderRegistry
ProviderRegistry.register_traffic_routing("route53", Route53TrafficRouter)
```

**활성화**: 환경변수 `SELFHEALING_TRAFFIC_ROUTING_ADAPTER=route53` 또는 ProviderRegistry에서 이름으로 조회.

### 3.3 어댑터 계층 선택 가이드

| 시나리오 | 권장 어댑터 | 이유 |
|----------|-----------|------|
| **프로덕션 멀티 리전 Failover** | Route53 / GCP Global LB | 장애 리전 K8s 접근 불요, DNS가 SSOT |
| 단일 클러스터 내 Blue/Green | K8s Ingress (4항 참조) | 같은 클러스터 내 서비스 전환 |
| 개발/테스트 환경 | LoggingAdapter (기본) | DNS 설정 없이 앱 레벨 동작 확인 |

---

## 4. K8s Ingress 어댑터 — 단일 클러스터 참조 구현

> **적용 범위**: 동일 K8s 클러스터 내 서비스 전환 (Blue/Green, Canary)에만 유효.
> 물리적 리전 간 Failover에는 3항의 DNS/Global LB 어댑터를 사용해야 한다.

### 4.1 파일 위치

```
packages/selfhealing-python/src/selfhealing/adapters/traffic_routing/
├── __init__.py
├── logging_adapter.py       # 기존
└── k8s_ingress_adapter.py   # 신규 (참조 구현)
```

### 4.2 구현 코드

```python
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

            # 롤백용 이전 상태 스냅샷
            rollback_info = {
                "previous_annotations": dict(
                    current_ingress.metadata.annotations or {}
                ),
                "previous_rules": copy.deepcopy(
                    current_ingress.spec.rules
                ),
                "previous_region": from_region,
            }

            # region_service_map에 등록된 서비스명 집합 (교체 대상 식별)
            known_services = set(self._region_service_map.values())

            # spec.rules의 backend.service.name 선택적 교체
            # 기존 host/path 규칙 구조는 보존, service.name만 변경
            patched_rules = copy.deepcopy(current_ingress.spec.rules)
            replaced_count = 0
            for rule in (patched_rules or []):
                if rule.http is None:
                    continue
                for path_entry in (rule.http.paths or []):
                    if (
                        path_entry.backend
                        and path_entry.backend.service
                        and path_entry.backend.service.name in known_services
                    ):
                        path_entry.backend.service.name = target_service
                        replaced_count += 1

            if replaced_count == 0:
                return RoutingChange(
                    success=False,
                    from_region=from_region,
                    to_region=to_region,
                    details={
                        "error": (
                            "No matching backend service found in "
                            f"Ingress rules (known: {known_services})"
                        )
                    },
                )

            # Ingress 패치: spec.rules + 추적용 annotation
            patch = {
                "metadata": {
                    "annotations": {
                        "selfhealing.io/primary-region": to_region,
                        "selfhealing.io/failover-timestamp": (
                            datetime.now(timezone.utc).isoformat()
                        ),
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

            # 앱 레벨 이벤트 발행 — Design Decision: 실패해도 RoutingChange에 영향 없음
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
        """
        앱 레벨 라우팅 이벤트 발행 (ServiceLocalityRouter 갱신용).

        Design Decision (6항 참조):
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
            logger.error(
                f"[K8sIngressTrafficRouter] Event publish failed: {e}"
            )
```

### 4.3 등록 방법

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

---

## 5. 이벤트 전파 보장형 전달 아키텍처 — `RedisEventBus` 개선

### 5.1 현재 문제

`switch_primary()` 성공 후 `REGION_PRIMARY_CHANGED` 이벤트를 `RedisEventBus`로 발행한다.
그러나 리전 Failover가 필요할 정도의 대장애 상황에서는 Redis도 다운될 가능성이 높다.

**현재 `RedisEventBus.publish()`** (`services/event_bus/redis_bus.py` L265–L291):

```python
def publish(self, event, propagate_to_redis=True):
    # 로컬 핸들러에 전달 (항상 성공)
    self._local_bus.publish(event)

    # Redis로 전파 (실패 시 경고 로그만)
    if propagate_to_redis and self._redis_client:
        try:
            channel = self._get_channel_for_event(event.event_type)
            self._redis_client.publish(channel, json.dumps(event.to_dict(), default=str))
        except Exception as e:
            logger.warning(f"[RedisEventBus] Failed to publish to Redis: {e}")
```

**영향 분석**:

| 상황 | 로컬 인스턴스 | 타 인스턴스 |
|------|-------------|-----------|
| Redis 정상 | ✅ 로컬 버스 수신 | ✅ Redis Pub/Sub 수신 |
| Redis 장애 | ✅ 로컬 버스 수신 | ❌ 라우팅 갱신 누락 |

타 인스턴스의 `ServiceLocalityRouter`가 라우팅 갱신을 받지 못하면
**라우팅 불일치(Split-brain routing)** 가 발생한다.

### 5.2 폴백 전파 체인 설계

> **설계 선택**: Kafka 기반 폴백을 채택한다.
>
> **이유**: `k8s/kafka-mirrormaker2-config.yaml`에 이미 Seoul↔Global 리전 간
> Kafka MirrorMaker2 미러링이 구성되어 있다. `selfhealing.audit.*` 토픽 패턴으로
> 리전 간 이벤트 복제가 동작 중이므로, 라우팅 이벤트 토픽(`selfhealing.routing.events`)을
> 추가하면 별도 인프라 없이 리전 간 보장형 전달이 가능하다.
>
> WAL 기반 폴백은 audit 모듈(`audit/event_buffer.py`의 `WALConfig` + `WriteAheadLog`)에
> 이미 패턴이 존재하지만, WAL은 단일 인스턴스의 로컬 영속화이므로
> 타 인스턴스 전파에는 적합하지 않다. WAL은 Kafka마저 실패할 때의
> 최종 안전망으로만 사용한다.

**폴백 체인**:

```
Redis Pub/Sub 발행 시도
  → 실패 시: Kafka 토픽(selfhealing.routing.events) 폴백 발행
    → 실패 시: 로컬 WAL 기록 (audit/wal.py 패턴 재활용)
      → 인프라 복구 시: WAL 재생 → Kafka/Redis 재발행
```

### 5.3 구현 책임 분리

이벤트 전파 보장은 **`TrafficRoutingAdapter`의 책임이 아니라 이벤트 버스 계층의 책임**이다.
어댑터가 Redis/Kafka/WAL 폴백 로직을 직접 품는 것은 단일 책임 원칙(SRP)을 위반한다.

**변경 대상**: `services/event_bus/redis_bus.py` — `RedisEventBus.publish()` 확장

```python
def publish(self, event, propagate_to_redis=True):
    # 1. 로컬 핸들러 (항상 성공)
    self._local_bus.publish(event)

    if not propagate_to_redis:
        return

    # 2. Redis 시도
    if self._redis_client:
        try:
            channel = self._get_channel_for_event(event.event_type)
            self._redis_client.publish(
                channel, json.dumps(event.to_dict(), default=str)
            )
            return  # 성공 시 종료
        except Exception as e:
            logger.warning(f"[RedisEventBus] Redis publish failed: {e}")

    # 3. Kafka 폴백 (크리티컬 이벤트만)
    if self._is_critical_event(event):
        try:
            self._publish_to_kafka_fallback(event)
            return
        except Exception as e:
            logger.error(f"[RedisEventBus] Kafka fallback failed: {e}")

    # 4. 최종 안전망: 로컬 WAL 기록
    if self._is_critical_event(event):
        self._write_to_wal(event)
```

크리티컬 이벤트 판별:

```python
_CRITICAL_EVENT_TYPES = {
    EventType.REGION_PRIMARY_CHANGED,
    EventType.EMERGENCY_ACTIVATED,
    EventType.EMERGENCY_DEACTIVATED,
    EventType.KILL_SWITCH_ACTIVATED,
}

def _is_critical_event(self, event: SelfHealingEvent) -> bool:
    return event.event_type in self._CRITICAL_EVENT_TYPES
```

### 5.4 Kafka MirrorMaker2 토픽 추가

기존 `k8s/kafka-mirrormaker2-config.yaml`의 `topicsPattern`에 라우팅 이벤트 추가:

```yaml
# 현재: selfhealing\.audit\..*
# 변경: selfhealing\.(audit|routing)\..*
topicsPattern: "selfhealing\\.(audit|routing)\\..*"
```

이렇게 하면 `selfhealing.routing.events` 토픽이 Seoul↔Global 간 자동 미러링된다.

---

## 6. Design Decision — 이벤트 발행 예외 격리

### 6.1 원칙

**인프라 라우팅 성공 후 이벤트 발행 실패가 전체 Failover를 실패로 마킹해서는 안 된다.**

DNS/LB 전환 또는 Ingress 패치가 성공했다면 트래픽은 이미 새 리전으로 흐르고 있다.
이벤트 발행(`_publish_routing_event`)은 앱 레벨 라우팅 테이블 동기화를 위한 **부가 작업**이며,
이것의 실패가 `RoutingChange.success`를 `False`로 바꿔서는 안 된다.

### 6.2 코드 근거 — 이미 구현된 격리 패턴

**`LoggingTrafficRoutingAdapter.switch_primary()`** (`logging_adapter.py` L55–L82):

```python
# 이벤트 발행이 try/except 블록 안에 격리됨
try:
    bus = get_event_bus(distributed=True)
    bus.publish(SelfHealingEvent(...))
except Exception as e:
    logger.error(f"[TrafficRouting] Event publish failed: {e}")

# 예외와 무관하게 항상 success=True 반환
return RoutingChange(success=True, from_region=from_region, to_region=to_region, ...)
```

기존 `LoggingTrafficRoutingAdapter`에서 이미 이벤트 발행 예외가 `RoutingChange` 결과에
영향을 주지 않도록 격리되어 있다. 이 패턴을 **모든 어댑터 구현의 필수 계약**으로 정의한다.

### 6.3 구현 요구사항

모든 `TrafficRoutingAdapter` 구현체는 다음을 준수해야 한다:

1. `_publish_routing_event()` 내부의 예외는 반드시 `try/except`로 포착
2. 예외 발생 시 로깅만 수행하고 상위로 전파하지 않음
3. 인프라 라우팅(DNS/LB/Ingress) 성공 시 `RoutingChange(success=True)` 반환

이 계약은 `interfaces/traffic_routing.py`의 `TrafficRoutingAdapter` docstring에도
명시해야 한다 (향후 구현자가 격리를 깨뜨리는 것을 방지).

---

## 7. 호출 체인 정리

### 7.1 Primary 장애 시 — Secondary 주도 Failover (2항 변경 반영)

```
Secondary(us-west-2)의 RegionFailover._check_and_failover()
  → _check_primary_and_promote(all_health)
    → Primary(ap-northeast-2) UNREACHABLE 확인
    → QuorumWitness.try_acquire_primary() → DynamoDB 조건부 쓰기 성공
    → _execute_failover(target=us-west-2, reason="primary_unreachable:...")
      → _update_traffic_routing(us-west-2)
        → _get_traffic_routing_adapter()
           1) self._traffic_adapter (생성자 주입)
           2) ProviderRegistry.get_traffic_routing()  ← "route53" 반환
           3) LoggingTrafficRoutingAdapter()           ← 최종 폴백
        → adapter.switch_primary("ap-northeast-2", "us-west-2")
          → Route53 Failover Record 전환 (DNS TTL 전파)
          → _publish_routing_event() (Redis/Kafka/WAL 폴백 체인)
      → _verify_data_consistency(us-west-2)
      → 상태: FAILED_OVER, 콜백, 알림 전송
```

### 7.2 수동 페일오버 — 동일 리전 내 서비스 전환 (K8s Ingress)

```
RegionFailover.trigger_failover(reason="blue-green-switch")
  → _execute_failover()
    → _update_traffic_routing(target_region)
      → K8sIngressTrafficRoutingAdapter.switch_primary()
        → Ingress spec.rules backend.service.name 교체
        → _publish_routing_event() (Redis 발행)
    → 상태 업데이트, 콜백, 알림
```

---

## 8. 변경 범위

| 파일 | 변경 | 상태 |
|------|------|------|
| `multiregion/failover.py` `_check_and_failover()` | Secondary 승격 로직 추가 | **필수** |
| `multiregion/failover.py` `_check_primary_and_promote()` | 신규 메서드 | **필수** |
| `services/event_bus/redis_bus.py` `publish()` | Kafka/WAL 폴백 체인 추가 | **필수** |
| `adapters/traffic_routing/k8s_ingress_adapter.py` | 신규 생성 (참조 구현) | 선택적 |
| `adapters/traffic_routing/__init__.py` | export 추가 | 선택적 |
| `factory.py` `_auto_register_adapters()` | 등록 추가 (또는 호스트 앱에서) | 선택적 |
| `k8s/kafka-mirrormaker2-config.yaml` | topicsPattern 확장 | 이벤트 보장 시 필수 |

> **참고**: K8s Ingress 어댑터는 **선택적 구현**이다.
> K8s 환경이 아니거나 DNS/LB 전환이 불필요한 경우,
> 기존 `LoggingTrafficRoutingAdapter`(앱 레벨 전환)만으로 충분하다.

---

## 9. RBAC 추가 요구사항 (K8s Ingress 어댑터 사용 시)

```yaml
# selfhealing-rbac.yaml에 추가
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "patch"]
```

---

## 10. GitOps 연동 및 제약사항

### 10.1 문제 — Imperative 패치와 GitOps Drift 충돌

K8s Ingress를 API로 직접 패치(Imperative update)하면,
ArgoCD/Flux 같은 선언적(Declarative) 관리 도구가 이를 **Drift(편차)로 인식**하고
Git에 정의된 원래 상태로 강제 롤백할 수 있다.

`adapters/deployment/kubernetes.py` L407–L409에서 ArgoCD 배포 **감지 코드**는 존재하지만,
ArgoCD Sync 일시중지나 Drift 허용 어노테이션 주입은 **미구현 상태**이다:

```python
# kubernetes.py — ArgoCD 배포 감지 (읽기 전용)
if replicaset.metadata.annotations.get("argocd.argoproj.io/sync-wave"):
    return "argocd"
```

### 10.2 K8s Ingress 어댑터 사용 시 필수 선행 조건

K8s Ingress 어댑터(4항)를 ArgoCD 관리 환경에서 사용하려면,
대상 Ingress 매니페스트에 **Drift 허용 어노테이션을 사전 주입**해야 한다:

```yaml
# Git 리포지토리의 Ingress 매니페스트에 추가
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: selfhealing-ingress
  namespace: selfhealing
  annotations:
    # ArgoCD가 이 리소스의 spec 변경을 Drift로 감지하지 않도록 설정
    argocd.argoproj.io/compare-options: IgnoreExtraneous
    # 또는 특정 필드만 무시 (jq path)
    argocd.argoproj.io/ignore-differences: |
      jsonPointers:
        - /spec/rules
        - /metadata/annotations/selfhealing.io~1primary-region
        - /metadata/annotations/selfhealing.io~1failover-timestamp
```

이 어노테이션이 없으면 ArgoCD의 `syncPolicy.automated.selfHeal: true` 설정이
Ingress를 Git 상태로 강제 복원하여 **Failover를 무효화**한다.

### 10.3 권장 — DNS/Global LB로 GitOps 충돌 원천 차단

Route53/Global LB 어댑터(3항)는 K8s 리소스를 수정하지 않으므로,
**GitOps Drift 문제가 원천적으로 발생하지 않는다.**

| 어댑터 | GitOps 충돌 위험 | 추가 인프라 설정 |
|--------|-----------------|----------------|
| Route53 / Global LB | ❌ 없음 | Route53 Hosted Zone |
| K8s Ingress | ⚠️ ArgoCD Drift 복원 위험 | ArgoCD 어노테이션 선행 주입 필수 |
| LoggingAdapter | ❌ 없음 | 없음 |

**결론**: GitOps 환경에서의 안전성을 포함하여, 프로덕션 멀티 리전 Failover에는
K8s 외부 DNS/Global LB 레벨의 라우팅 전환이 **강력히 권장**된다.

---

## 11. 관련 문서

| 문서 | 관계 |
|------|------|
| `interfaces/traffic_routing.py` | 인터페이스 정의 (변경 없음) |
| `adapters/traffic_routing/logging_adapter.py` | 기존 기본 구현 (변경 없음) |
| `multiregion/failover.py` | 호출부 (Secondary 승격 로직 추가) |
| `multiregion/quorum.py` | Quorum Witness (변경 없음, 승격 시 사용) |
| `services/event_bus/redis_bus.py` | 이벤트 버스 (Kafka/WAL 폴백 체인 추가) |
| `factory.py` | ProviderRegistry 등록 (선택적 변경) |
| `audit/event_buffer.py`, `audit/wal.py` | WAL 패턴 참조 (재활용) |
| `k8s/kafka-mirrormaker2-config.yaml` | MirrorMaker2 토픽 패턴 확장 |
| `adapters/deployment/kubernetes.py` | ArgoCD 감지 코드 (읽기 전용, 참조) |

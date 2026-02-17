# 237. Multi-Region 약점 3가지 개선 구현 계획

> **문서 번호**: 237
> **관련 모듈**: `selfhealing.multiregion.*`, `selfhealing.core.*`, `selfhealing.services.event_bus.*`
> **선행 문서**: 178_MULTI_REGION_ACTIVE_ACTIVE.md
> **상태**: 구현 계획

---

## 1. 개요

현재 Multi-Region Active-Active 아키텍처에서 식별된 3가지 약점과 각각의 개선 방안을 정리한다.
모든 개선안은 **기존 코드베이스 내 검증된 컴포넌트 조합**으로 구현하며, 외부 의존성(Service Discovery 등)을 추가하지 않는다.

| # | 약점 | 핵심 원인 | 개선 전략 |
|---|------|----------|----------|
| 1 | 수동 피어 관리 | `peer_regions` JSON 고정, `RegionReplicator._targets` 갱신 불가 | Redis 동적 레지스트리 + `refresh_targets()` |
| 2 | 비정상 종료 인스턴스 감지 지연 | polling 전용(10s×3=30s 최악) | TTL heartbeat(15s) + push 모델(정상 종료 0s) |
| 3 | Failover TODO 미구현 | `_verify_data_consistency()`, `_update_traffic_routing()` 스텁 | 기존 컴포넌트 조합 + `TrafficRoutingAdapter` 인터페이스 |

---

## 2. 약점 1: 수동 피어 관리 → 동적 피어 레지스트리

### 2.1 현재 구조 분석

**`multiregion/config.py`** — `MultiRegionSettings`:

```python
# Line 127-130: peer_regions는 환경변수 JSON 문자열
peer_regions: str = Field(
    default="[]",
    description='피어 리전 JSON (예: [{"region": "us-east-1", "redis_url": "...", ...}])',
)
```

```python
# Line 280-301: get_peer_endpoints()는 self.peer_regions를 매번 파싱
def get_peer_endpoints(self) -> list[RegionEndpoint]:
    data = json.loads(self.peer_regions)
    return [RegionEndpoint(...) for r in data]
```

```python
# Line 309-313: @lru_cache(maxsize=1) — 인스턴스 자체는 프로세스 수명 동안 고정
@lru_cache(maxsize=1)
def get_multiregion_settings() -> MultiRegionSettings:
    return MultiRegionSettings()
```

**`multiregion/health_monitor.py`** — `RegionHealthMonitor.check_all_regions()`:

```python
# Line 360-363: 매 루프마다 get_peer_endpoints() 호출 → 동적 변경 자동 반영
for endpoint in self._settings.get_peer_endpoints():
    health = self.check_region(endpoint)
```

**`multiregion/replicator.py`** — `RegionReplicator.__init__()`:

```python
# Line 372-373: _targets를 __init__에서 1회 빌드, 갱신 메서드 없음
for endpoint in self._settings.get_peer_endpoints():
    self._targets.append(RedisReplicationTarget(endpoint))
```

### 2.2 문제점

| 컴포넌트 | `get_peer_endpoints()` 호출 시점 | 동적 반영 |
|----------|-------------------------------|----------|
| `RegionHealthMonitor` | 매 루프 반복 (Line 360) | **O** — `get_peer_endpoints()` 반환값이 변하면 자동 반영 |
| `RegionReplicator` | `__init__` 1회 (Line 372-373) | **X** — `self._targets`가 고정, 갱신 메서드 없음 |
| `RegionFailover` | `_select_failover_target()` 내 간접 호출 | **O** — 매 호출 시 `get_peer_endpoints()` 재파싱 |

**핵심**: `RegionReplicator._targets`만 동적 반영이 불가능하다.
`get_peer_endpoints()` 자체를 Redis 기반으로 전환하면 `HealthMonitor`와 `Failover`는 자동 해결되지만, `Replicator`는 `refresh_targets()` 메서드를 별도로 추가해야 한다.

### 2.3 개선 설계

#### 2.3.1 Redis 동적 피어 레지스트리

기존 `RedisStateBackend`(core/state_backend.py Line 148-258)를 활용한다.

```
Redis Key: selfhealing:state:multiregion:peers
Value: JSON — RegionEndpoint 배열
TTL: 없음 (명시적 삭제만)
```

**변경 대상**: `MultiRegionSettings.get_peer_endpoints()`

```python
# 현재 (config.py Line 280-301)
def get_peer_endpoints(self) -> list[RegionEndpoint]:
    data = json.loads(self.peer_regions)  # ← 고정 JSON
    return [RegionEndpoint(...) for r in data]

# 개선안
def get_peer_endpoints(self) -> list[RegionEndpoint]:
    # 1차: Redis 동적 레지스트리 시도
    dynamic = self._load_dynamic_peers()
    if dynamic is not None:
        return dynamic
    # 2차: 환경변수 JSON 폴백 (기존 동작 유지)
    data = json.loads(self.peer_regions)
    return [RegionEndpoint(...) for r in data]
```

`_load_dynamic_peers()` 구현:

```python
def _load_dynamic_peers(self) -> list[RegionEndpoint] | None:
    """Redis에서 동적 피어 목록 로드. 실패 시 None 반환."""
    try:
        from selfhealing.core.state_backend import get_state_backend
        backend = get_state_backend()
        data = backend.get("multiregion:peers")
        if data and "endpoints" in data:
            return [
                RegionEndpoint(
                    region=r["region"],
                    redis_url=r.get("redis_url", ""),
                    kafka_bootstrap=r.get("kafka_bootstrap", ""),
                    api_endpoint=r.get("api_endpoint", ""),
                    priority=r.get("priority", 100),
                )
                for r in data["endpoints"]
            ]
    except Exception as e:
        logger.debug(f"[MultiRegion] Dynamic peer lookup failed: {e}")
    return None
```

**설계 근거**:
- `RedisStateBackend`는 `selfhealing:state:` 접두사 + JSON 직렬화 지원 (Line 169, 204-211)
- `get_state_backend()` 팩토리 함수는 `SELFHEALING_STATE_BACKEND=redis` 설정 시 자동으로 Redis 반환 (Line 332-355)
- `FileStateBackend` 폴백 시에도 동일 인터페이스로 동작 (단, 단일 서버 한정)

#### 2.3.2 RegionReplicator.refresh_targets()

```python
# replicator.py에 추가
def refresh_targets(self) -> int:
    """
    피어 엔드포인트 목록 갱신.

    Returns:
        갱신 후 타겟 수
    """
    new_endpoints = self._settings.get_peer_endpoints()
    current_regions = {t.endpoint.region for t in self._targets}
    new_regions = {e.region for e in new_endpoints}

    # 추가된 리전
    for endpoint in new_endpoints:
        if endpoint.region not in current_regions:
            self._targets.append(RedisReplicationTarget(endpoint))
            logger.info(f"[Replicator] Added target: {endpoint.region}")

    # 제거된 리전
    self._targets = [
        t for t in self._targets
        if t.endpoint.region in new_regions
    ]

    return len(self._targets)
```

#### 2.3.3 자동 갱신 통합

`GlobalConfigPropagator`(services/config/propagator.py)를 통해 피어 변경 이벤트를 수신:

```python
# RegionReplicator._run_loop() 또는 별도 구독
from selfhealing.services.event_bus.redis_bus import get_event_bus, EventType

bus = get_event_bus(distributed=True)
bus.subscribe(EventType.CONFIG_UPDATED, self._on_config_updated)

def _on_config_updated(self, event):
    if event.data.get("key") == "multiregion:peers":
        self.refresh_targets()
```

### 2.4 영향 범위

| 파일 | 변경 유형 | 라인 |
|------|----------|------|
| `multiregion/config.py` | `_load_dynamic_peers()` 추가, `get_peer_endpoints()` 수정 | 280-301 |
| `multiregion/replicator.py` | `refresh_targets()` 추가 | 신규 |
| 테스트 | 동적 피어 갱신 시나리오 | 신규 |

### 2.5 호환성

- `SELFHEALING_STATE_BACKEND=file`(기본값)인 경우 `_load_dynamic_peers()`가 항상 `None` 반환 → 환경변수 JSON 폴백 → **기존 동작 100% 유지**
- Redis 백엔드 사용 시에도 Redis 키 미존재 시 환경변수 폴백
- 기존 `peer_regions` 환경변수 설정을 제거할 필요 없음

---

## 3. 약점 2: 비정상 종료 인스턴스 감지 지연 → Push + TTL 하이브리드

### 3.1 현재 구조 분석

**감지 흐름** (polling 전용):
```
RegionHealthMonitor._run_loop()
  → check_all_regions()     — 10초 간격 (health_check_interval_seconds)
    → check_region()        — HTTP GET /health/
      → status 판정         — 실패 시 consecutive_failures++
        → UNREACHABLE 판정  — unhealthy_threshold=3 연속 실패 시
```

**최악 감지 시간**: `10s × 3 = 30초`

**현재 보유 컴포넌트**:

| 컴포넌트 | 위치 | 역할 |
|----------|------|------|
| `GracefulShutdownCoordinator` | `core/shutdown_coordinator.py` | SIGTERM 수신 → DRAINING → 종료 |
| `RedisEventBus` | `services/event_bus/redis_bus.py` | Redis Pub/Sub 실시간 이벤트 |
| `AuditWatchdogSettings` | `settings/audit_watchdog.py` | Dead Man's Switch heartbeat (30s 간격) |
| `RedisStateBackend` | `core/state_backend.py` | 키-값 TTL 지원 |

### 3.2 개선 설계

3계층 감지 전략:

```
┌──────────────────────────────────────────────────────┐
│ Layer 1: Push (정상 종료) — 0초 감지                   │
│  GracefulShutdownCoordinator.on_shutdown_start()      │
│  → RedisEventBus.publish(REGION_INSTANCE_STOPPING)    │
├──────────────────────────────────────────────────────┤
│ Layer 2: TTL Heartbeat (비정상 종류) — 15초 감지        │
│  RedisStateBackend.set("heartbeat:{region}", TTL=15)  │
│  키 소멸 → Redis Keyspace Notification → 즉시 감지     │
├──────────────────────────────────────────────────────┤
│ Layer 3: Polling (폴백) — 30초 감지                    │
│  RegionHealthMonitor.check_all_regions() — 기존 유지   │
└──────────────────────────────────────────────────────┘
```

#### 3.2.1 Layer 1: Push 모델 (정상 종료)

`GracefulShutdownCoordinator`(core/shutdown_coordinator.py Line 233-266)의 `on_shutdown_start()` 콜백을 활용:

```python
# 새로운 ShutdownHandler 구현
class MultiRegionShutdownHandler(ShutdownHandler):
    """정상 종료 시 피어 리전에 즉시 통보."""

    def __init__(self, settings: MultiRegionSettings):
        self._settings = settings

    def on_shutdown_start(self) -> None:
        """SIGTERM 수신 즉시 실행 — 0초 감지."""
        try:
            from selfhealing.services.event_bus.redis_bus import get_event_bus
            bus = get_event_bus(distributed=True)
            bus.publish(SelfHealingEvent(
                event_type=EventType.REGION_INSTANCE_STOPPING,
                data={
                    "region": self._settings.current_region,
                    "reason": "graceful_shutdown",
                    "timestamp": time.time(),
                },
                source="shutdown_coordinator",
            ))
        except Exception as e:
            logger.warning(f"[Shutdown] Failed to notify peers: {e}")

    def on_drain_complete(self) -> None:
        pass  # 추가 동작 불필요

    def on_force_shutdown(self, pending_requests) -> None:
        pass  # Polling이 폴백으로 감지
```

**설계 근거**:
- `GracefulShutdownCoordinator`는 `SIGTERM` 시그널 핸들러를 등록하고(Line 246-250), `initiate_shutdown()` 호출 시 `ShutdownHandler.on_shutdown_start()`를 즉시 실행한다 (Line 262-266)
- `RedisEventBus.publish()`는 Redis Pub/Sub를 통해 모든 구독 인스턴스에 즉시 전파한다 (Line 286-298)
- 기존 `coordination/shutdown_integration.py`에서 `LeaderElector` 연동을 위해 동일한 패턴을 사용 중이므로, 추가 `ShutdownHandler`를 체인으로 연결하면 된다

**한계**: SIGKILL, OOM Kill, 네트워크 단절 등 비정상 종료 시에는 동작하지 않는다. → Layer 2가 담당.

#### 3.2.2 Layer 2: TTL Heartbeat (비정상 종료)

`RedisStateBackend.set()`의 TTL 파라미터(Line 204-211)를 활용:

```python
class RegionHeartbeat:
    """리전 생존 신호. TTL 만료 = 인스턴스 사망."""

    HEARTBEAT_KEY_PREFIX = "multiregion:heartbeat:"
    HEARTBEAT_TTL = 15  # 초 — 기존 30초 대비 50% 단축
    HEARTBEAT_INTERVAL = 5  # 초 — TTL의 1/3

    def __init__(self, settings: MultiRegionSettings):
        self._settings = settings
        self._running = False
        self._worker: threading.Thread | None = None

    def _heartbeat_key(self) -> str:
        return f"{self.HEARTBEAT_KEY_PREFIX}{self._settings.current_region}"

    def _beat(self) -> None:
        """하트비트 갱신."""
        try:
            from selfhealing.core.state_backend import get_state_backend
            backend = get_state_backend()
            backend.set(
                self._heartbeat_key(),
                {"region": self._settings.current_region, "ts": time.time()},
                ttl_seconds=self.HEARTBEAT_TTL,
            )
        except Exception as e:
            logger.warning(f"[Heartbeat] Failed: {e}")

    def start(self) -> None:
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while self._running:
            self._beat()
            time.sleep(self.HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        self._running = False
```

**감지 메커니즘 — Redis Keyspace Notifications**:

```python
# RegionHealthMonitor에 추가
def _subscribe_heartbeat_expiry(self) -> None:
    """Redis Keyspace Notification으로 하트비트 만료 감지."""
    try:
        import redis
        client = redis.from_url(self._redis_url, decode_responses=True)
        # CONFIG SET notify-keyspace-events Ex (만료 이벤트 활성화)
        client.config_set("notify-keyspace-events", "Ex")
        pubsub = client.pubsub()
        pubsub.psubscribe("__keyevent@*__:expired")

        for message in pubsub.listen():
            if message["type"] == "pmessage":
                key = message["data"]
                if key.startswith("selfhealing:state:multiregion:heartbeat:"):
                    region = key.split(":")[-1]
                    logger.warning(f"[RegionHealth] Heartbeat expired: {region}")
                    self._mark_unhealthy(region)
    except Exception as e:
        logger.warning(f"[RegionHealth] Keyspace notification unavailable: {e}")
```

**감지 시간 비교**:

| 종료 유형 | 현재 | 개선 후 |
|----------|------|---------|
| 정상 종료 (SIGTERM) | 30초 (polling) | **0초** (push) |
| 비정상 종료 (OOM/SIGKILL) | 30초 (polling) | **15초** (TTL 만료) |
| 네트워크 단절 | 30초 (polling) | **15초** (TTL + polling 병렬) |

#### 3.2.3 Layer 3: Polling 유지 (폴백)

기존 `RegionHealthMonitor._run_loop()`(health_monitor.py Line 465-473)은 그대로 유지한다.
TTL 미지원 환경(`FileStateBackend`)에서의 폴백이자, Keyspace Notification 누락 시 최종 안전망으로 기능한다.

### 3.3 EventType 확장

```python
# services/event_bus/bus.py에 추가
class EventType(str, Enum):
    # ... 기존 ...
    REGION_INSTANCE_STOPPING = "region_instance_stopping"
    REGION_HEARTBEAT_EXPIRED = "region_heartbeat_expired"
```

### 3.4 영향 범위

| 파일 | 변경 유형 |
|------|----------|
| `multiregion/heartbeat.py` | **신규** — `RegionHeartbeat` 클래스 |
| `multiregion/health_monitor.py` | Keyspace Notification 구독 추가 |
| `services/event_bus/bus.py` | `EventType` 2개 추가 |
| `core/shutdown_coordinator.py` | 변경 없음 (기존 인터페이스 사용) |

---

## 4. 약점 3: Failover TODO 미구현

### 4.1 현재 구조 분석

`multiregion/failover.py` — `RegionFailover._execute_failover()` (Line 262-313):

```python
# Line 284-286: TODO 스텁 1
# 2. DNS/Load Balancer 전환
# TODO: Route53 / GCP Global LB API 호출
self._update_traffic_routing(target_region)

# Line 288-290: TODO 스텁 2
# 3. 데이터 정합성 확인
# TODO: 마지막 복제 오프셋 확인
self._verify_data_consistency(target_region)
```

```python
# Line 315-318: 스텁 구현 — logger.info만 출력
def _update_traffic_routing(self, target_region: str) -> None:
    logger.info(f"[Failover] Updating traffic routing to {target_region}")

# Line 320-323: 스텁 구현 — logger.info만 출력
def _verify_data_consistency(self, target_region: str) -> None:
    logger.info(f"[Failover] Verifying data consistency for {target_region}")
```

### 4.2 `_verify_data_consistency()` 구현

#### 4.2.1 설계

기존 `RegionReplicator.get_stats()`(replicator.py Line 547-550)와 `RedisStateBackend`를 조합한다.

```python
def _verify_data_consistency(self, target_region: str) -> None:
    """
    데이터 정합성 확인.

    확인 항목:
    1. 복제 큐 잔량 (미복제 이벤트 존재 여부)
    2. 마지막 복제 타임스탬프 비교
    3. 핵심 키 정합성 (CB 상태, Emergency 상태)
    """
    issues = []

    # 1. 복제 큐 잔량 확인
    try:
        from selfhealing.multiregion.replicator import RegionReplicator
        replicator = RegionReplicator(settings=self._settings)
        queue_size = replicator.get_queue_size()
        if queue_size > 0:
            issues.append(f"Replication queue not empty: {queue_size} pending")
            logger.warning(
                f"[Failover] {queue_size} events pending replication "
                f"to {target_region}"
            )
    except Exception as e:
        logger.warning(f"[Failover] Cannot check replication queue: {e}")

    # 2. 핵심 키 정합성 — 로컬 vs 타겟 리전
    try:
        from selfhealing.core.state_backend import get_state_backend
        local_backend = get_state_backend()

        critical_keys = [
            "emergency_mode",
            "system_control",
        ]

        for key in critical_keys:
            local_val = local_backend.get(key)
            # 타겟 리전 값은 health_monitor의 API를 통해 조회
            remote_val = self._fetch_remote_state(target_region, key)
            if local_val != remote_val:
                issues.append(
                    f"Key '{key}' mismatch: "
                    f"local={local_val}, remote={remote_val}"
                )
    except Exception as e:
        logger.warning(f"[Failover] Consistency check partial: {e}")

    # 3. 결과 로깅
    if issues:
        logger.warning(
            f"[Failover] Data consistency issues for {target_region}: "
            f"{'; '.join(issues)}"
        )
        # 이슈가 있어도 failover는 계속 진행 (가용성 우선)
        # 이슈 정보를 FailoverEvent.details에 포함
    else:
        logger.info(
            f"[Failover] Data consistency verified for {target_region}"
        )

def _fetch_remote_state(self, region: str, key: str) -> Any:
    """타겟 리전의 상태 값 조회 (API 경유)."""
    endpoints = self._settings.get_peer_endpoints()
    for ep in endpoints:
        if ep.region == region and ep.api_endpoint:
            try:
                import urllib.request
                url = f"{ep.api_endpoint}/api/v1/state/{key}/"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read())
            except Exception:
                pass
    return None
```

**설계 근거**:
- `RegionReplicator.get_queue_size()`(Line 543-544)는 미복제 이벤트 수를 즉시 반환
- `RedisStateBackend.get()`(Line 196-203)으로 핵심 키 조회 가능
- `RegionHealthMonitor.check_region()`(health_monitor.py)과 동일한 `urllib.request` 기반 HTTP 호출 패턴 사용

### 4.3 `_update_traffic_routing()` 구현 — TrafficRoutingAdapter

#### 4.3.1 DNS/LB SDK 도입 필요성 평가

**결론: `TrafficRoutingAdapter` 인터페이스만 정의하고, 외부 SDK는 사용자가 주입한다.**

**근거** — 기존 코드베이스의 설계 철학:

| 인터페이스 | 위치 | 기본 구현 | 프로덕션 구현 |
|-----------|------|----------|-------------|
| `AlertAdapter` | `interfaces/alert_adapter.py` | `StdoutAlertAdapter` | 사용자가 PagerDuty/OpsGenie 어댑터 주입 |
| `NotificationAdapter` | `interfaces/notification.py` | `StdoutNotificationAdapter` | 사용자가 Slack/Teams 어댑터 주입 |
| `ConfigProviderInterface` | `interfaces/config_provider.py` | 환경변수 | 사용자가 Consul/etcd 어댑터 주입 |
| `AuditLogAdapter` | `interfaces/audit_adapter.py` | `FileAuditLogAdapter` | 사용자가 외부 SIEM 어댑터 주입 |

위 목록에서 확인되는 패턴:
1. **selfhealing 패키지 내부에는 외부 클라우드 SDK 의존성이 없다** (`adapters/alert/`에는 `stdout_adapter.py`, `file_adapter.py`, `null_adapter.py`만 존재)
2. **인터페이스(ABC/Protocol)만 제공하고, 프로덕션 어댑터는 호스트 앱이 등록한다** (`ProviderRegistry.register_*()`)
3. **기본 구현은 부작용 없는 No-op 또는 로깅**

`TrafficRoutingAdapter`도 동일 패턴을 따라야 한다:
- Route53, GCP Global LB, Cloudflare 등의 SDK를 selfhealing 내부에 포함하면 **"No forced dependencies"** 원칙 위반
- self-healing 시스템의 책임 범위는 **"장애 감지 → 판단 → 실행 위임"**이며, DNS/LB 직접 조작은 인프라 계층의 책임

#### 4.3.2 TrafficRoutingAdapter 인터페이스

```python
# interfaces/traffic_routing.py (신규)
"""
Traffic Routing Adapter Interface.

리전 장애 시 트래픽 전환을 위한 추상 인터페이스.

Implementations can use:
- AWS Route53 (boto3)
- GCP Global Load Balancer (google-cloud-compute)
- Cloudflare DNS (cloudflare)
- Kubernetes Ingress (kubernetes)
- 앱 레벨 라우팅 (ServiceLocalityRouter 연동)

기본 구현은 로깅만 수행합니다 (LoggingTrafficRoutingAdapter).
프로덕션에서는 사용자가 ProviderRegistry에 어댑터를 등록합니다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class RoutingChange:
    """트래픽 라우팅 변경 결과."""
    success: bool
    from_region: str
    to_region: str
    details: dict[str, Any]
    rollback_info: dict[str, Any] | None = None  # 롤백용 이전 상태


class TrafficRoutingAdapter(ABC):
    """
    트래픽 라우팅 어댑터 인터페이스.

    Example (AWS Route53):
        class Route53TrafficRouter(TrafficRoutingAdapter):
            def __init__(self, hosted_zone_id: str):
                self._client = boto3.client('route53')
                self._zone_id = hosted_zone_id

            def switch_primary(self, from_region, to_region) -> RoutingChange:
                # Route53 failover record set 업데이트
                ...

            def rollback(self, routing_change) -> bool:
                # 이전 상태로 복원
                ...

            def get_current_routing(self) -> dict:
                # 현재 라우팅 상태 조회
                ...
    """

    @abstractmethod
    def switch_primary(
        self, from_region: str, to_region: str
    ) -> RoutingChange:
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
```

#### 4.3.3 기본 구현 (No-op + App-level)

```python
# adapters/traffic_routing/logging_adapter.py (신규)
class LoggingTrafficRoutingAdapter(TrafficRoutingAdapter):
    """
    기본 어댑터 — 로깅만 수행.

    DNS/LB 레벨 전환 없이 앱 레벨에서만 동작:
    1. RedisEventBus로 REGION_PRIMARY_CHANGED 이벤트 발행
    2. ServiceLocalityRouter가 이벤트 수신 후 라우팅 테이블 갱신
    """

    def switch_primary(self, from_region, to_region) -> RoutingChange:
        logger.warning(
            f"[TrafficRouting] App-level routing update: "
            f"{from_region} → {to_region} "
            f"(DNS/LB adapter not configured)"
        )

        # 앱 레벨 라우팅 전파
        try:
            from selfhealing.services.event_bus.redis_bus import get_event_bus
            bus = get_event_bus(distributed=True)
            bus.publish(SelfHealingEvent(
                event_type=EventType.CONFIG_UPDATED,
                data={
                    "key": "region_primary",
                    "value": to_region,
                    "previous": from_region,
                },
                source="failover",
            ))
        except Exception as e:
            logger.error(f"[TrafficRouting] Event publish failed: {e}")

        return RoutingChange(
            success=True,
            from_region=from_region,
            to_region=to_region,
            details={"level": "app_only", "dns_updated": False},
        )

    def rollback(self, routing_change) -> bool:
        return self.switch_primary(
            routing_change.to_region,
            routing_change.from_region,
        ).success

    def get_current_routing(self) -> dict:
        return {"adapter": "logging", "note": "app-level only"}
```

#### 4.3.4 `_update_traffic_routing()` 개선

```python
# failover.py 수정
def _update_traffic_routing(self, target_region: str) -> None:
    """
    트래픽 라우팅 업데이트.

    TrafficRoutingAdapter를 통해 DNS/LB 전환을 실행합니다.
    어댑터 미등록 시 LoggingTrafficRoutingAdapter(앱 레벨만)를 사용합니다.
    """
    adapter = self._get_traffic_routing_adapter()
    result = adapter.switch_primary(self._current_primary, target_region)

    if not result.success:
        raise RuntimeError(
            f"Traffic routing switch failed: {result.details}"
        )

    # 롤백 정보 저장 (복구 시 사용)
    self._last_routing_change = result

    logger.info(
        f"[Failover] Traffic routing updated: "
        f"{self._current_primary} → {target_region} "
        f"(details={result.details})"
    )

def _get_traffic_routing_adapter(self) -> TrafficRoutingAdapter:
    """TrafficRoutingAdapter 인스턴스 반환."""
    if self._traffic_adapter is not None:
        return self._traffic_adapter

    # ProviderRegistry에서 조회 시도
    try:
        from selfhealing.factory import ProviderRegistry
        adapter = ProviderRegistry.get_traffic_routing()
        return adapter
    except (ValueError, AttributeError):
        pass

    # 기본 어댑터 사용
    from selfhealing.adapters.traffic_routing.logging_adapter import (
        LoggingTrafficRoutingAdapter,
    )
    return LoggingTrafficRoutingAdapter()
```

### 4.4 ProviderRegistry 확장

```python
# factory.py에 추가
class ProviderRegistry:
    # 기존 필드들...
    _traffic_routing_adapters: dict[str, type] = {}
    _default_traffic_routing: str = "logging"

    @classmethod
    def register_traffic_routing(cls, name: str, adapter_class: type) -> None:
        cls._traffic_routing_adapters[name] = adapter_class
        logger.debug(f"[Registry] Registered traffic routing: {name}")

    @classmethod
    def get_traffic_routing(
        cls, name: str | None = None, singleton: bool = True
    ) -> "TrafficRoutingAdapter":
        name = name or cls._default_traffic_routing
        # ... 기존 get_* 패턴과 동일 ...
```

### 4.5 영향 범위

| 파일 | 변경 유형 |
|------|----------|
| `interfaces/traffic_routing.py` | **신규** — `TrafficRoutingAdapter` ABC |
| `adapters/traffic_routing/logging_adapter.py` | **신규** — 기본 구현 |
| `multiregion/failover.py` | `_update_traffic_routing()`, `_verify_data_consistency()` 구현 |
| `factory.py` | `register_traffic_routing()`, `get_traffic_routing()` 추가 |
| `services/event_bus/bus.py` | `EventType.REGION_PRIMARY_CHANGED` 추가 |

---

## 5. DNS/LB SDK 준비의 필요성 평가

### 5.1 판단

**selfhealing 패키지 내부에 DNS/LB SDK(boto3, google-cloud 등)를 포함하지 않는다.**

### 5.2 근거

#### 5.2.1 시스템 설계 철학 — "No Forced Dependencies"

`interfaces/alert_adapter.py` Line 134-140에서 확인되는 패턴:

```python
class AlertAdapter(ABC):
    """
    Implementations can send alerts to:
    - stdout (StdoutAlertAdapter)         ← selfhealing 내부
    - Files (FileAlertAdapter)            ← selfhealing 내부
    - Slack/Teams (user implements)       ← 사용자 구현
    - PagerDuty/OpsGenie (user implements)← 사용자 구현
    - Nowhere (NullAlertAdapter)          ← selfhealing 내부
    """
```

`adapters/alert/` 디렉토리에는 `stdout_adapter.py`, `file_adapter.py`, `null_adapter.py`만 존재한다.
외부 서비스 SDK를 포함하는 어댑터는 **단 하나도 없다**.

이것은 의도적 설계이다:
- selfhealing은 **라이브러리 패키지**이며, 호스트 앱의 의존성 트리를 오염시키지 않는다
- 클라우드 SDK(boto3 ~60MB, google-cloud ~30MB)를 필수 의존성에 추가하면 설치 크기가 수십 배 증가한다
- AWS를 사용하는 팀에게 GCP SDK를, GCP 팀에게 AWS SDK를 강제하는 것은 불합리하다

#### 5.2.2 self-healing 시스템의 책임 범위

self-healing 시스템의 코어 책임 체인:

```
감지(Detection) → 분석(Analysis) → 판단(Decision) → 실행 위임(Delegation)
```

DNS/LB 직접 조작은 **인프라 계층**의 책임이다:
- K8s 환경: Ingress Controller, Service Mesh(Istio)가 담당
- AWS 환경: Route53 Health Check + Failover Record가 자동 처리
- GCP 환경: Global Load Balancer Health Check가 자동 처리

self-healing 시스템이 수행해야 할 것은 **"이 리전이 Primary"라는 의사결정**이며, 그 결정을 인프라에 전달하는 방법(DNS API, K8s API, 이벤트)은 어댑터의 책임이다.

#### 5.2.3 실질적 대안

DNS/LB 레벨 전환 없이도 앱 레벨에서 유효한 라우팅 전환이 가능하다:

```
RedisEventBus.publish(REGION_PRIMARY_CHANGED)
  → 모든 인스턴스의 ServiceLocalityRouter가 수신
    → 라우팅 테이블 갱신
      → 새 Primary로 요청 라우팅
```

이는 DNS TTL 전파(최대 60초)보다 빠르다.

### 5.3 사용자 구현 가이드 (문서화 범위)

DNS/LB 레벨 전환이 필요한 팀을 위해, 어댑터 구현 예시를 docstring에 포함한다:

```python
# TrafficRoutingAdapter docstring에 포함
"""
Example (AWS Route53):
    class Route53TrafficRouter(TrafficRoutingAdapter):
        def __init__(self, hosted_zone_id: str):
            self._client = boto3.client('route53')
            self._zone_id = hosted_zone_id

        def switch_primary(self, from_region, to_region) -> RoutingChange:
            self._client.change_resource_record_sets(...)
            return RoutingChange(success=True, ...)

Example (K8s Ingress):
    class K8sIngressTrafficRouter(TrafficRoutingAdapter):
        def switch_primary(self, from_region, to_region) -> RoutingChange:
            # kubectl patch ingress ...
            ...

# 등록
ProviderRegistry.register_traffic_routing("route53", Route53TrafficRouter)
```

---

## 6. 구현 우선순위

| 순위 | 개선 | 난이도 | 효과 | 비고 |
|------|------|--------|------|------|
| **P1** | 약점 2 - Push 모델 (Layer 1) | 낮음 | 정상 종료 0초 감지 | 기존 `ShutdownHandler` 구현만 추가 |
| **P2** | 약점 3 - `_verify_data_consistency()` | 중간 | Failover 안전성 확보 | 기존 컴포넌트 조합 |
| **P3** | 약점 2 - TTL Heartbeat (Layer 2) | 중간 | 비정상 종료 15초 감지 | Redis Config 변경 필요 (`notify-keyspace-events`) |
| **P4** | 약점 3 - `TrafficRoutingAdapter` | 중간 | 인터페이스 표준화 | 기본 구현은 앱 레벨만 |
| **P5** | 약점 1 - 동적 피어 레지스트리 | 높음 | 무중단 피어 추가/제거 | Redis 백엔드 필수 |

---

## 7. 신규 파일 목록

```
packages/selfhealing-python/src/selfhealing/
├── interfaces/
│   └── traffic_routing.py                    # TrafficRoutingAdapter ABC
├── adapters/
│   └── traffic_routing/
│       ├── __init__.py
│       └── logging_adapter.py                # 기본 구현 (로깅 + 앱 레벨)
└── multiregion/
    └── heartbeat.py                          # RegionHeartbeat (TTL 기반)
```

---

## 8. 테스트 전략

| 테스트 | 검증 대상 |
|--------|----------|
| `test_dynamic_peer_registry` | Redis/File 폴백, 피어 추가/제거 반영 |
| `test_replicator_refresh_targets` | `refresh_targets()` 호출 후 타겟 리스트 변경 |
| `test_push_shutdown_notification` | `on_shutdown_start()` → EventBus 이벤트 발행 확인 |
| `test_ttl_heartbeat_expiry` | TTL 만료 시 UNHEALTHY 판정 |
| `test_verify_data_consistency` | 큐 잔량 검사, 핵심 키 비교 |
| `test_traffic_routing_adapter_fallback` | 어댑터 미등록 시 LoggingAdapter 사용 |
| `test_traffic_routing_adapter_injection` | ProviderRegistry 통한 커스텀 어댑터 주입 |

---

## 9. 결론

3가지 약점 모두 **기존 컴포넌트의 조합과 확장**으로 해결 가능하다.
외부 의존성(Service Discovery, DNS SDK)은 추가하지 않으며, 시스템의 "No Forced Dependencies" 원칙을 유지한다.

DNS/LB SDK는 selfhealing 내부에 포함하지 않는 대신, `TrafficRoutingAdapter` 인터페이스를 제공하여 사용자가 자신의 인프라에 맞는 구현체를 주입할 수 있도록 한다. 이는 `AlertAdapter`, `NotificationAdapter`, `ConfigProviderInterface`와 완전히 동일한 패턴이다.

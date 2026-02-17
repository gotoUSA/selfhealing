# 237. Multi-Region 약점 3가지 개선 구현 계획

> **문서 번호**: 237
> **관련 모듈**: `selfhealing.multiregion.*`, `selfhealing.core.*`, `selfhealing.services.event_bus.*`
> **선행 문서**: 178_MULTI_REGION_ACTIVE_ACTIVE.md
> **상태**: ✅ 구현 완료
>
> ### 구현 완료 내역
>
> | # | 파일 | 변경 유형 |
> |---|------|----------|
> | 1 | `services/event_bus/bus.py` | EventType 3개 추가 (`REGION_INSTANCE_STOPPING`, `REGION_HEARTBEAT_EXPIRED`, `REGION_PRIMARY_CHANGED`) |
> | 2 | `interfaces/traffic_routing.py` | **신규** — `TrafficRoutingAdapter` ABC + `RoutingChange` dataclass |
> | 3 | `adapters/traffic_routing/__init__.py` | **신규** — 패키지 init |
> | 4 | `adapters/traffic_routing/logging_adapter.py` | **신규** — `LoggingTrafficRoutingAdapter` |
> | 5 | `factory.py` | `register_traffic_routing()`, `get_traffic_routing()` 추가 |
> | 6 | `multiregion/failover.py` | `_update_traffic_routing()`, `_verify_data_consistency()`, `_build_ssl_context()`, `_fetch_remote_state()` 구현 |
> | 7 | `multiregion/heartbeat.py` | **신규** — `RegionHeartbeat` (TTL) + `MultiRegionShutdownHandler` (Push) |
> | 8 | `multiregion/health_monitor.py` | `_subscribe_heartbeat_expiry()`, `_mark_unhealthy()` 추가, `start()` 수정 |
> | 9 | `multiregion/config.py` | `get_peer_endpoints()` Redis-first 폴백, `_load_dynamic_peers()` Security Note |
> | 10 | `multiregion/replicator.py` | `refresh_targets()` 메서드 추가 |
> | 11 | `multiregion/__init__.py` | heartbeat 모듈 export 추가 |
> | 12 | `interfaces/__init__.py` | traffic_routing export 추가 |
>
> **리뷰 반영 4건**: 4-1 (mTLS), 1-2/3-2 (CONFIG SET ResponseError), 2-2 (Security Note)
>
> **기존 단위 테스트**: 48개 전부 통과 (regression 없음)
>
> **통합 테스트 필요**: heartbeat TTL → health_monitor 체인은 실제 Redis keyspace notification 필요 (향후 작성)

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

---

## 10. 리뷰 분석 및 반영

> 16가지 리뷰 항목에 대한 분석, 판단 근거, 반영 사항을 코드 기반으로 정리한다.

### 10.1 시스템 환경 및 동시성

#### 10.1.1 [충분] 동기 vs 비동기 (Gunicorn+Threads)

**리뷰 판단**: 별도 수정 불필요.

**코드 근거**:
- `docker-compose.yml` Line 36: `gunicorn myproject.wsgi:application --workers 4 --threads 4 --worker-class gthread`
- `myproject/wsgi.py` Line 12: `from django.core.wsgi import get_wsgi_application` — WSGI(동기)
- `health_monitor.py` Line 480: `threading.Thread(target=self._run_loop, name="RegionHealthMonitor", daemon=True)`
- `replicator.py` Line 441: `threading.Thread(target=self._replicate_async_worker, daemon=True)`
- `failover.py` Line 406: `threading.Thread(target=self._run_loop, name="RegionFailover", daemon=True)`

**결론**: 모든 Multi-Region 컴포넌트가 전용 daemon Thread에서 실행된다. `urllib.request.urlopen` 같은 Blocking I/O가 Gunicorn 워커의 요청 처리를 차단하지 않는다. asyncio 이벤트 루프를 사용하지 않으므로 문서 237의 설계에 수정 불필요.

#### 10.1.2 [수정] Redis 환경 구성 — 관리형 Redis CONFIG SET 대응

**리뷰 판단**: `_subscribe_heartbeat_expiry`에서 `redis.exceptions.ResponseError` 별도 처리 필요.

**코드 근거**:
- `docker-compose.yml` Line 22: `image: redis:7-alpine` — 현재는 자체 호스팅 Standalone
- `state_backend.py` Line 179: `redis.from_url(self._redis_url, decode_responses=True)` — 단일 URL 연결
- Sentinel/Cluster 설정은 코드베이스에 없음 (`docker-compose.yml`에 `sentinel`, `cluster` 키워드 미존재)

**반영 사항**: §10.4.1에 수정된 코드 반영. `config_set` 호출을 try/except로 분리하여, `ResponseError` 시 관리형 Redis 파라미터 그룹 설정 안내 로그를 출력한다.

#### 10.1.3 [충분] Python 버전 (3.12/3.10)

**코드 근거**:
- `pyproject.toml` (호스트): `requires-python = ">=3.12"`
- `pyproject.toml` (패키지): `requires-python = ">=3.10"`, classifiers에 `3.10`, `3.11`, `3.12` 포함
- 모든 파일에 `from __future__ import annotations` 적용 (e.g., `config.py` Line 14, `replicator.py` Line 18)

**결론**: `dict[str, Any]`, `X | None` 등 PEP 604/585 문법 사용 가능. `dataclass` 활용에 제한 없음.

### 10.2 약점 1: 동적 피어 레지스트리

#### 10.2.1 [충분] 레지스트리 쓰기 권한

**코드 근거**:
- `config.py` Line 127-130: `peer_regions`는 환경변수 `SELFHEALING_MULTIREGION_PEER_REGIONS`로 설정
- 문서 237의 `_load_dynamic_peers()`는 `backend.get("multiregion:peers")`로 **읽기 전용**
- 코드베이스에 자기 등록(Self-Registration) 코드 없음

**결론**: 쓰기 주체는 배포 파이프라인 또는 어드민 도구. Race Condition 처리 불필요. 동시성 제어 로직을 제거하여 구현 복잡도를 낮춘다.

#### 10.2.2 [보완] 보안 — Security Note 추가

**코드 근거**:
- `config.py` `RegionEndpoint` (Line 31-67): `redis_url` 필드가 `redis://user:password@host:6379/0` 형태로 비밀번호 포함 가능
- `secure_client.py` (Line 4-8): "리전 간 데이터는 공용 인터넷을 통과할 수 있으므로 mTLS로 암호화"
- 그러나 `RedisStateBackend.set()` (Line 204-211)에 평문 JSON으로 저장됨

**반영 사항**: `_load_dynamic_peers()` 구현에 Security Note 주석 추가 (§10.4.2).

```python
# Security Note: peer_regions JSON에 redis_url이 포함되며,
# 비밀번호가 평문으로 저장될 수 있습니다.
# 프로덕션 환경에서는 다음을 권장합니다:
# 1. Redis ACL로 접근 제어
# 2. 보안 그룹/VPC 네트워크 격리
# 3. redis_url에서 비밀번호 분리 (환경변수로 별도 관리)
```

#### 10.2.3 [충분] 초기 부트스트랩

**코드 근거**:
- `config.py` `get_peer_endpoints()` (Line 280): `if not self.peer_regions: return []`
- `peer_regions` 기본값: `"[]"` (빈 배열)
- `health_monitor.py` `check_all_regions()` (Line 370): 빈 리스트면 루프 미실행
- `replicator.py` `__init__` (Line 380-381): 빈 리스트면 `_targets` 비어 있음

**결론**: Redis 비어있고 환경변수 없는 Cold Start에서 단일 리전으로 정상 동작. 에러 없음.

### 10.3 약점 2: 비정상 종료 감지

#### 10.3.1 [충분] Redis 장애 대응

**코드 근거** — 시스템 전반의 Fail-open 패턴:
- `redis_bus.py` `_connect_redis()` (Line 179-184): Redis 연결 실패 → `self._redis_client = None`, 로컬 버스 폴백
- `redis_bus.py` `publish()` (Line 273-277): Redis 발행 실패 → `logger.warning()`만
- `state_backend.py` `get()` (Line 196-202): Redis get 실패 → `default` 반환

**결론**: Fail-open 정책 유지. Heartbeat 실패 시 로그만 남기고, Polling(Layer 3)이 폴백으로 감지.

#### 10.3.2 [수정] Keyspace Notification — 관리형 Redis CONFIG SET 대응

**리뷰 판단**: 10.1.2와 동일. `CONFIG SET` 실패를 별도 처리해야 함.

**반영 사항**: §10.4.1에 통합 반영. `_subscribe_heartbeat_expiry`에서:
1. `config_set` 호출을 별도 try/except로 분리
2. `redis.exceptions.ResponseError` 발생 시 구체적 안내 로그 출력
3. `config_set` 실패해도 구독은 시도 (이미 파라미터 그룹에서 설정되어 있을 수 있음)

#### 10.3.3 [충분] Heartbeat 부하

**코드 근거**:
- 문서 237 `RegionHeartbeat._heartbeat_key()`: `f"{self.HEARTBEAT_KEY_PREFIX}{self._settings.current_region}"` — **리전 단위** 키
- `config.py` Line 99: `current_region`은 동일 리전의 모든 인스턴스가 같은 값
- Gunicorn fork 워커 × Heartbeat 스레드 = 동일 키에 대한 SET 연산 (마지막 SET이 TTL 갱신)

**결론**: 리전 수 × 5초 간격 = 무시할 수 있는 Redis 부하. 인스턴스 수 증가 시에도 동일 키 갱신이므로 부하 선형 증가하지 않음.

### 10.4 약점 3: Failover 및 데이터 정합성

#### 10.4.1 [수정 필수] mTLS 누락 — HTTP API 호출에 SSL Context 미적용

**리뷰 판단**: **가장 중요한 수정 사항.**

**코드 근거**:
- `secure_client.py` (Line 63-92): `SecureRedisClient._create_ssl_context()` — Redis 통신에만 mTLS 적용
- `health_monitor.py` `_check_api_health()` (Line 238-245): `urllib.request.urlopen(req, timeout=...)` — **SSL Context 미전달**
- `secure_client.py` (Line 4-8): 명시적으로 "공용 인터넷 통과 가능" 언급
- `config.py` (Line 224-249): `tls_enabled`, `tls_cert_path`, `tls_key_path`, `tls_ca_path`, `tls_verify_hostname` 설정 존재

**문제**: `_fetch_remote_state()`와 `_check_api_health()` 모두 HTTP API 호출 시 TLS 검증 없이 평문 통신. Public Internet 경유 시 보안 취약점.

**반영 방향**:
- `SecureRedisClient._create_ssl_context()` 로직을 **재사용**하지 않는다 (다른 클래스의 private 메서드)
- 대신 `multiregion/failover.py`의 `RegionFailover`에 `_build_ssl_context()` 메서드를 추가한다
- 네이밍을 `_build_ssl_context`로 하여 `secure_client.py`의 `_create_ssl_context`와 구분한다
- 로직은 `secure_client.py` Line 63-92의 패턴을 따르되, `tls_verify_hostname=False` 시 `CERT_NONE`으로 설정하지 않는다 (hostname 검증만 해제, 인증서 검증은 유지)

**수정된 코드** — `multiregion/failover.py`:

```python
def _build_ssl_context(self) -> ssl.SSLContext | None:
    """
    HTTP API 호출용 SSL Context 생성.

    config.py의 TLS 설정을 읽어 mTLS 컨텍스트를 생성합니다.
    TLS 비활성화 시 None 반환 (일반 HTTP 사용).

    Note:
        SecureRedisClient._create_ssl_context()와 동일한 설정을 사용하지만,
        Redis 클라이언트가 아닌 urllib.request.urlopen()에 전달하기 위한
        별도 메서드입니다.
    """
    if not self._settings.tls_enabled:
        return None

    try:
        context = ssl.create_default_context(
            cafile=self._settings.tls_ca_path,
        )

        # 클라이언트 인증서 로드 (mTLS)
        if self._settings.tls_cert_path and self._settings.tls_key_path:
            context.load_cert_chain(
                certfile=self._settings.tls_cert_path,
                keyfile=self._settings.tls_key_path,
            )

        # 호스트명 검증 설정
        context.check_hostname = self._settings.tls_verify_hostname
        context.verify_mode = ssl.CERT_REQUIRED

        return context
    except FileNotFoundError as e:
        logger.warning(f"[Failover] TLS certificate not found: {e}")
        return None
    except ssl.SSLError as e:
        logger.error(f"[Failover] SSL context creation failed: {e}")
        return None
```

```python
def _fetch_remote_state(self, region: str, key: str) -> Any:
    """
    타겟 리전의 상태 값 조회 (API 경유).

    mTLS가 활성화된 경우 SSL Context를 적용합니다.
    SecureRedisClient와 동일한 TLS 설정(config.py)을 사용합니다.
    """
    endpoints = self._settings.get_peer_endpoints()
    ssl_ctx = self._build_ssl_context()

    for ep in endpoints:
        if ep.region == region and ep.api_endpoint:
            try:
                url = f"{ep.api_endpoint}/api/v1/state/{key}/"
                req = urllib.request.Request(
                    url, method="GET",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(
                    req, timeout=5, context=ssl_ctx
                ) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read())
            except Exception as e:
                logger.warning(
                    f"[Failover] Failed to fetch state '{key}' "
                    f"from {region}: {e}"
                )
    return None
```

**네이밍 선택 근거**: `_build_ssl_context`
- `secure_client.py`의 `_create_ssl_context` (Line 63)와 충돌 회피
- `build` prefix는 "설정을 읽어 객체를 조립"하는 의미로 적합
- `create`는 `secure_client.py`에서 이미 사용 중이므로 혼동 방지

#### 10.4.2 [충분] 데이터 불일치 정책

**코드 근거**:
- `failover.py` `_execute_failover()` (Line 275-290): `_verify_data_consistency()` 호출 후 반환값 미확인, 바로 상태 업데이트
- `pyproject.toml` (Line 7): `Django REST API with Self-Healing capabilities` — 쇼핑/e-commerce 앱
- `docker-compose.yml` (Line 8): `POSTGRES_DB=shopping_db`

**결론**: "가용성 > 일관성" (AP over CP) 정책 유지. 금융/결제 전용이 아닌 일반 서비스.

#### 10.4.3 [충분] TrafficRoutingAdapter 롤백

**코드 근거**:
- 기본 구현 `LoggingTrafficRoutingAdapter`는 DNS 변경 없음 — Redis Pub/Sub 앱 레벨 이벤트만 전파
- DNS 전파 지연은 기본 구현에서 해당 없음
- 사용자가 DNS SDK 어댑터를 주입하는 경우에만 관련

**결론**: 인터페이스에 `rollback()` 메서드만 정의. 기본 구현의 롤백은 `switch_primary()` 역호출.

#### 10.4.4 [충분] 어댑터 주입 시점

**코드 근거**:
- `factory.py` (Line 120): `Should be called during app initialization (e.g., Django's AppConfig.ready())`
- `apps.py` (Line 116): `SelfHealingConfig.ready()` — 시스템 초기화 수행
- `failover.py`: `RegionFailover.__init__()`에 `_traffic_adapter` 필드 없음 → `_get_traffic_routing_adapter()`가 **호출 시점에** ProviderRegistry 조회

**결론**: Lazy 조회 패턴으로 타이밍 이슈 없음. `AppConfig.ready()`에서 등록하면 failover 실행 시점에 이미 사용 가능.

### 10.5 추가 확인 사항

#### 10.5.1 [충분] Service Discovery — Redis vs K8s

**코드 근거**:
- `pyproject.toml` (패키지 Line 42): `dependencies = ["redis>=4.0", ...]` — Redis는 이미 필수 의존성
- `kubernetes` 패키지는 의존성에 없음
- `tiered_redis.py` (Line 1-15): LOCAL/GLOBAL Redis 계층이 이미 설계됨

**결론**: Redis는 추가 비용 없이 사용 가능. K8s API는 "No Forced Dependencies" 원칙 위반.

#### 10.5.2 [충분] 테스트 환경 — Mock 기반

**코드 근거**:
- 테스트 코드 전반: `Mock()`, `mocker.patch()` 패턴
- `pyproject.toml` (패키지 dev): `fakeredis`, `testcontainers` 미포함
- `state_backend.py` (Line 293-321): `MemoryStateBackend` — 테스트용 in-memory 백엔드 제공

**결론**: Mock 기반 테스트 유지. `MemoryStateBackend`로 TTL 외 로직 검증.

#### 10.5.3 [충분] 문서화 — TrafficRoutingAdapter 예제

**코드 근거**:
- `alert_adapter.py` (Line 14-21): docstring에 구현 예시 포함 (기존 패턴)
- 문서 237 §4.3.2: AWS Route53, K8s Ingress 예제 이미 포함

**결론**: docstring에 예제 코드 포함하는 기존 패턴 유지.

### 10.6 리뷰 반영 요약

| # | 항목 | 판단 | 반영 |
|---|------|------|------|
| 1-1 | 동기 vs 비동기 | 충분 | 수정 없음 |
| 1-2 | Redis CONFIG SET | **수정** | `_subscribe_heartbeat_expiry` ResponseError 처리 (§10.4.1) |
| 1-3 | Python 버전 | 충분 | 수정 없음 |
| 2-1 | 쓰기 권한 | 충분 | 수정 없음 |
| 2-2 | 보안 | **보완** | Security Note 주석 추가 (§10.2.2) |
| 2-3 | 부트스트랩 | 충분 | 수정 없음 |
| 3-1 | Redis 장애 대응 | 충분 | 수정 없음 (Fail-open) |
| 3-2 | Keyspace Notification | **수정** | 1-2와 동일 처리 |
| 3-3 | Heartbeat 부하 | 충분 | 수정 없음 |
| 4-1 | mTLS 누락 | **수정 필수** | `_build_ssl_context()` + `_fetch_remote_state()` 수정 (§10.4.1) |
| 4-2 | 불일치 정책 | 충분 | 수정 없음 (가용성 우선) |
| 4-3 | 롤백 | 충분 | 수정 없음 |
| 4-4 | 주입 시점 | 충분 | 수정 없음 |
| 5-1 | Service Discovery | 충분 | 수정 없음 |
| 5-2 | 테스트 | 충분 | 수정 없음 (Mock) |
| 5-3 | 문서화 | 충분 | 수정 없음 |

---

## 11. 네이밍 검증

### 11.1 기존 코드베이스와의 충돌 검사

| 신규 이름 | 충돌 여부 | 비고 |
|----------|----------|------|
| `_build_ssl_context` | **없음** | `_create_ssl_context`가 `secure_client.py` Line 63에 존재하므로 `_build` prefix로 차별화 |
| `_fetch_remote_state` | **없음** | 코드베이스에 미존재 |
| `MultiRegionShutdownHandler` | **없음** | 기존 `ShutdownHandler`(ABC)의 구현체. `coordination/shutdown_integration.py`의 패턴과 일관 |
| `RegionHeartbeat` | **없음** | `multiregion/` 모듈의 컴포넌트 네이밍 패턴 (`Region` prefix) 준수 |
| `TrafficRoutingAdapter` | **없음** | `interfaces/` 디렉토리의 `*Adapter` 패턴 준수 (`AlertAdapter`, `AuditLogAdapter` 등) |
| `RoutingChange` | **없음** | `dataclass` 결과 객체. `FailoverEvent` 등과 동일 패턴 |
| `LoggingTrafficRoutingAdapter` | **없음** | `adapters/` 디렉토리의 `*Adapter` 패턴. `StdoutAlertAdapter`와 유사 |
| `REGION_INSTANCE_STOPPING` | **없음** | `EventType` Enum 멤버. `EMERGENCY_*`, `CIRCUIT_BREAKER_*` 등과 동일 패턴 |
| `REGION_HEARTBEAT_EXPIRED` | **없음** | 동일 |
| `REGION_PRIMARY_CHANGED` | **없음** | 동일 |
| `refresh_targets` | **없음** | `RegionReplicator`의 공개 메서드. `start()`, `stop()`, `enqueue()` 등과 동일 수준 |
| `_load_dynamic_peers` | **없음** | `MultiRegionSettings`의 private 메서드 |
| `_subscribe_heartbeat_expiry` | **없음** | `RegionHealthMonitor`의 private 메서드 |
| `_mark_unhealthy` | **없음** | `RegionHealthMonitor`에 미존재. 기존 건강 상태 갱신은 `self._health_states[region] = ...` 직접 할당 |
| `register_traffic_routing` | **없음** | `ProviderRegistry.register_*()` 패턴 (`register_cache`, `register_queue` 등) |
| `get_traffic_routing` | **없음** | `ProviderRegistry.get_*()` 패턴 (`get_cache`, `get_queue` 등) |

### 11.2 `_create_ssl_context` → `_build_ssl_context` 변경 근거

**기존 코드**: `SecureRedisClient._create_ssl_context()` — `secure_client.py` Line 63

```python
class SecureRedisClient:
    def _create_ssl_context(self) -> ssl.SSLContext | None:
        """SSL 컨텍스트 생성."""
        if not self._settings.tls_enabled:
            return None
        context = ssl.create_default_context(cafile=self._settings.tls_ca_path)
        context.load_cert_chain(
            certfile=self._settings.tls_cert_path,
            keyfile=self._settings.tls_key_path,
        )
        context.check_hostname = self._settings.tls_verify_hostname
        context.verify_mode = ssl.CERT_REQUIRED
        return context
```

**신규 코드**: `RegionFailover._build_ssl_context()` — `failover.py`

같은 TLS 설정(`config.py`)을 읽지만 다른 클래스에 속하므로 메서드 이름 충돌은 기술적으로 없다. 그러나 `_create` prefix를 재사용하면 코드 리뷰 시 혼동을 줄 수 있으므로 `_build`로 구분한다.

리뷰에서 제안한 `_create_ssl_context` 대신 `_build_ssl_context`를 채택하는 이유:
1. **모듈 내 일관성**: `multiregion/` 모듈에 같은 이름의 private 메서드가 2개 존재하면 `grep`/검색 시 혼동
2. **의미 차별화**: `create`는 "새로 생성", `build`는 "설정을 읽어 조립" — HTTP 용도를 구분
3. **코드 리뷰 효율**: 다른 이름이면 "왜 기존 것을 재사용하지 않는가?" 질문에 즉시 답변 가능

### 11.3 `_mark_unhealthy` 구현 필요성

문서 237에서 `_mark_unhealthy(region)` 호출이 있으나, `RegionHealthMonitor`에 이 메서드가 존재하지 않는다.

**현재 건강 상태 갱신 방식** — `health_monitor.py` `check_all_regions()` (Line 386-390):
```python
with self._lock:
    self._health_states = results
```

**신규 구현 필요**:
```python
def _mark_unhealthy(self, region: str) -> None:
    """
    특정 리전을 UNHEALTHY로 즉시 마킹.

    Keyspace Notification 또는 Push 이벤트로 감지된 장애를 반영한다.
    다음 check_all_regions() 루프에서 정상 확인 시 자동 복구된다.
    """
    with self._lock:
        if region in self._health_states:
            self._health_states[region] = RegionHealth(
                region=region,
                status=RegionHealthStatus.UNREACHABLE,
                latency_ms=0,
                last_check=datetime.now(timezone.utc),
                consecutive_failures=self._settings.unhealthy_threshold,
                details={"reason": "heartbeat_expired"},
            )
            logger.warning(
                f"[RegionHealth] Marked {region} as UNREACHABLE "
                f"(heartbeat expired)"
            )
```

---

## 12. 리뷰 반영 수정 코드

### 12.1 `_subscribe_heartbeat_expiry` — 관리형 Redis 대응

> **반영 리뷰**: 1-2 (Redis CONFIG SET), 3-2 (Keyspace Notification)

**변경 대상**: `multiregion/health_monitor.py` (§3.2.2의 코드를 대체)

```python
def _subscribe_heartbeat_expiry(self) -> None:
    """
    Redis Keyspace Notification으로 하트비트 만료 감지.

    CONFIG SET 권한이 없는 관리형 Redis(ElastiCache, Memorystore 등)에서는
    파라미터 그룹에서 미리 `notify-keyspace-events = Ex`를 설정해야 합니다.
    CONFIG SET 실패 시에도 구독을 시도합니다 (이미 설정되어 있을 수 있음).
    """
    try:
        import redis as redis_lib

        client = redis_lib.from_url(self._redis_url, decode_responses=True)

        # 관리형 Redis 대응: CONFIG SET 시도 후 실패 시 로그만 남기고 구독 시도
        try:
            client.config_set("notify-keyspace-events", "Ex")
            logger.info(
                "[RegionHealth] Keyspace notifications enabled "
                "(notify-keyspace-events=Ex)"
            )
        except redis_lib.exceptions.ResponseError as e:
            error_msg = str(e).lower()
            if "unknown command" in error_msg or "permission" in error_msg:
                logger.warning(
                    "[RegionHealth] 'CONFIG SET' not permitted. "
                    "If using managed Redis (ElastiCache, Memorystore), "
                    "set 'notify-keyspace-events=Ex' in the parameter "
                    "group/instance config. "
                    f"Error: {e}"
                )
            else:
                raise  # 다른 ResponseError는 상위로 전파

        pubsub = client.pubsub()
        pubsub.psubscribe("__keyevent@*__:expired")

        for message in pubsub.listen():
            if not self._running:
                break
            if message["type"] == "pmessage":
                key = message["data"]
                if key.startswith("selfhealing:state:multiregion:heartbeat:"):
                    region = key.split(":")[-1]
                    logger.warning(
                        f"[RegionHealth] Heartbeat expired: {region}"
                    )
                    self._mark_unhealthy(region)

    except ImportError:
        logger.warning(
            "[RegionHealth] redis package not installed, "
            "keyspace notification unavailable"
        )
    except Exception as e:
        logger.warning(
            f"[RegionHealth] Keyspace notification unavailable: {e}"
        )
```

### 12.2 `_build_ssl_context` + `_fetch_remote_state` — mTLS 지원

> **반영 리뷰**: 4-1 (mTLS 누락)

**변경 대상**: `multiregion/failover.py` (§4.2의 `_fetch_remote_state`를 대체)

```python
import ssl
import urllib.request

def _build_ssl_context(self) -> ssl.SSLContext | None:
    """
    HTTP API 호출용 SSL Context 생성.

    config.py의 TLS 설정(tls_enabled, tls_cert_path, tls_key_path,
    tls_ca_path, tls_verify_hostname)을 읽어 mTLS 컨텍스트를 생성합니다.

    TLS 비활성화 시 None을 반환합니다 (일반 HTTP).

    Note:
        SecureRedisClient._create_ssl_context() (secure_client.py Line 63)와
        동일한 설정을 사용하지만, urllib.request.urlopen()에 전달하기 위한
        별도 메서드입니다. 네이밍을 `_build`로 하여 `_create`와 구분합니다.
    """
    if not self._settings.tls_enabled:
        return None

    try:
        context = ssl.create_default_context(
            cafile=self._settings.tls_ca_path,
        )

        # 클라이언트 인증서 로드 (mTLS)
        if self._settings.tls_cert_path and self._settings.tls_key_path:
            context.load_cert_chain(
                certfile=self._settings.tls_cert_path,
                keyfile=self._settings.tls_key_path,
            )

        # 호스트명 검증 설정
        # tls_verify_hostname=False 시에도 인증서 체인 검증은 유지 (CERT_REQUIRED)
        context.check_hostname = self._settings.tls_verify_hostname
        context.verify_mode = ssl.CERT_REQUIRED

        return context
    except FileNotFoundError as e:
        logger.warning(f"[Failover] TLS certificate not found: {e}")
        return None
    except ssl.SSLError as e:
        logger.error(f"[Failover] SSL context creation failed: {e}")
        return None


def _fetch_remote_state(self, region: str, key: str) -> Any:
    """
    타겟 리전의 상태 값 조회 (API 경유).

    mTLS가 활성화된 경우 _build_ssl_context()로 생성한
    SSL Context를 urllib.request.urlopen()에 전달합니다.

    Args:
        region: 타겟 리전 이름
        key: 상태 키 (예: "emergency_mode")

    Returns:
        상태 값 (dict) 또는 None (실패 시)
    """
    endpoints = self._settings.get_peer_endpoints()
    ssl_ctx = self._build_ssl_context()

    for ep in endpoints:
        if ep.region == region and ep.api_endpoint:
            try:
                url = f"{ep.api_endpoint}/api/v1/state/{key}/"
                req = urllib.request.Request(
                    url, method="GET",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(
                    req, timeout=5, context=ssl_ctx
                ) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read())
            except Exception as e:
                logger.warning(
                    f"[Failover] Failed to fetch state '{key}' "
                    f"from {region}: {e}"
                )
    return None
```

### 12.3 `_load_dynamic_peers` — Security Note 추가

> **반영 리뷰**: 2-2 (보안)

**변경 대상**: `multiregion/config.py` (§2.3.1의 코드를 대체)

```python
def _load_dynamic_peers(self) -> list[RegionEndpoint] | None:
    """
    Redis에서 동적 피어 목록 로드.

    실패 시 None을 반환하여 환경변수 JSON으로 폴백합니다.

    Security Note:
        peer_regions JSON에 redis_url이 포함되며,
        비밀번호가 평문으로 저장될 수 있습니다.
        프로덕션 환경에서는 다음을 권장합니다:
        1. Redis ACL로 접근 제어
        2. 보안 그룹/VPC 네트워크 격리
        3. redis_url에서 비밀번호 분리 (환경변수로 별도 관리)

    Returns:
        RegionEndpoint 목록 또는 None (실패 시)
    """
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

---

## 13. 수정 파일 최종 목록

| 파일 | 변경 유형 | 리뷰 반영 |
|------|----------|----------|
| `multiregion/config.py` | `_load_dynamic_peers()` Security Note 추가, `get_peer_endpoints()` 수정 | 2-2 |
| `multiregion/failover.py` | `_build_ssl_context()` 신규, `_fetch_remote_state()` mTLS 적용, `_update_traffic_routing()` 구현, `_verify_data_consistency()` 구현, `__init__`에 `_last_routing_change` 필드 추가 | 4-1 |
| `multiregion/health_monitor.py` | `_subscribe_heartbeat_expiry()` ResponseError 처리, `_mark_unhealthy()` 신규, `start()`에 heartbeat 구독 스레드 추가 | 1-2, 3-2 |
| `multiregion/heartbeat.py` | **신규** — `RegionHeartbeat` 클래스 | — |
| `multiregion/replicator.py` | `refresh_targets()` 신규 | — |
| `interfaces/traffic_routing.py` | **신규** — `TrafficRoutingAdapter` ABC, `RoutingChange` dataclass | — |
| `adapters/traffic_routing/__init__.py` | **신규** — 패키지 init | — |
| `adapters/traffic_routing/logging_adapter.py` | **신규** — `LoggingTrafficRoutingAdapter` | — |
| `services/event_bus/bus.py` | `EventType` 3개 추가 (`REGION_INSTANCE_STOPPING`, `REGION_HEARTBEAT_EXPIRED`, `REGION_PRIMARY_CHANGED`) | — |
| `factory.py` | `register_traffic_routing()`, `get_traffic_routing()` 추가, `reset()`에 `_traffic_routing_adapters` 초기화 추가 | — |

---

## 14. 구현 순서 (우선순위 반영)

| 단계 | 작업 | 리뷰 | 의존성 |
|------|------|------|--------|
| **1** | `bus.py` — `EventType` 3개 추가 | — | 없음 |
| **2** | `interfaces/traffic_routing.py` 생성 | — | 없음 |
| **3** | `adapters/traffic_routing/` 패키지 생성 | — | 단계 2 |
| **4** | `factory.py` — `register_traffic_routing`, `get_traffic_routing` 추가 | — | 단계 2 |
| **5** | `failover.py` — `_build_ssl_context()`, `_fetch_remote_state()` mTLS 수정 | **4-1** | 없음 |
| **6** | `failover.py` — `_verify_data_consistency()` 구현 | — | 없음 |
| **7** | `failover.py` — `_update_traffic_routing()` 구현 | — | 단계 2, 3, 4 |
| **8** | `multiregion/heartbeat.py` 생성 | — | 없음 |
| **9** | `health_monitor.py` — `_subscribe_heartbeat_expiry()`, `_mark_unhealthy()` | **1-2, 3-2** | 단계 1, 8 |
| **10** | `config.py` — `_load_dynamic_peers()` Security Note, `get_peer_endpoints()` 수정 | **2-2** | 없음 |
| **11** | `replicator.py` — `refresh_targets()` 추가 | — | 단계 10 |
| **12** | 테스트 작성 | — | 단계 1-11 |

---

## 15. 테스트 전략 (리뷰 반영 추가분)

| 테스트 | 검증 대상 | 리뷰 |
|--------|----------|------|
| `test_dynamic_peer_registry` | Redis/File 폴백, 피어 추가/제거 반영 | — |
| `test_replicator_refresh_targets` | `refresh_targets()` 호출 후 타겟 리스트 변경 | — |
| `test_push_shutdown_notification` | `on_shutdown_start()` → EventBus 이벤트 발행 확인 | — |
| `test_ttl_heartbeat_expiry` | TTL 만료 시 UNHEALTHY 판정 | — |
| `test_verify_data_consistency` | 큐 잔량 검사, 핵심 키 비교 | — |
| `test_traffic_routing_adapter_fallback` | 어댑터 미등록 시 LoggingAdapter 사용 | — |
| `test_traffic_routing_adapter_injection` | ProviderRegistry 통한 커스텀 어댑터 주입 | — |
| **`test_fetch_remote_state_with_mtls`** | `_build_ssl_context()` 호출 확인, `urlopen(context=...)` 전달 검증 | **4-1** |
| **`test_subscribe_heartbeat_config_set_failure`** | `CONFIG SET` `ResponseError` 시 로그 출력 후 구독 계속 | **1-2, 3-2** |
| **`test_mark_unhealthy`** | `_mark_unhealthy(region)` 호출 후 `UNREACHABLE` 상태 확인 | — |
| **`test_load_dynamic_peers_security_note`** | 문서화 확인 (docstring 존재 여부) | **2-2** |

---

## 16. 결론

3가지 약점 모두 **기존 컴포넌트의 조합과 확장**으로 해결 가능하다.
외부 의존성(Service Discovery, DNS SDK)은 추가하지 않으며, 시스템의 "No Forced Dependencies" 원칙을 유지한다.

16개 리뷰 항목 중 **4건의 수정 사항**을 반영했다:

1. **[4-1] mTLS 누락** — `_build_ssl_context()` + `_fetch_remote_state()` SSL Context 적용 (가장 중요)
2. **[1-2] 관리형 Redis CONFIG SET** — `ResponseError` 분리 처리 + 구체적 안내 로그
3. **[3-2] Keyspace Notification** — 1-2와 동일 처리, `config_set` 실패 시에도 구독 시도
4. **[2-2] 보안** — `_load_dynamic_peers()` Security Note docstring 추가

나머지 12건은 기존 설계가 코드 근거에 부합하여 변경 불필요로 확인되었다.

네이밍은 기존 코드베이스와 충돌이 없으며, `_build_ssl_context`는 `secure_client.py`의 `_create_ssl_context`와 의도적으로 구분하여 모듈 내 검색 혼동을 방지한다.

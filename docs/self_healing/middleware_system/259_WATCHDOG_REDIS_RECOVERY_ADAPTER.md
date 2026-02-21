# 259. Watchdog Redis 복구 — RecoveryAdapter 연결

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Implemented
> **Parent**: [250_CORRELATION_ENGINE_OVERVIEW.md](250_CORRELATION_ENGINE_OVERVIEW.md)
> **Implements**: `meta/watchdog.py` — `_recover_redis()` 개선

---

## 0. 요약

`SelfHealerWatchdog._recover_redis()`가 현재 **가짜 복구(False Recovery)만** 수행하는 문제를 해결한다.

**핵심 변경 6건**:

| # | 항목 | 대상 파일 |
|---|------|----------|
| 1 | Stage 1 커넥션 풀 진성 복구 (`reconnect()`) | `adapters/cache/redis_adapter.py`, `meta/watchdog.py` |
| 2 | PVC 전제조건 명시 (P0 데이터 보호) | `k8s/redis-config.yaml` |
| 3 | Recovery 쿨다운 메커니즘 추가 | `meta/watchdog.py`, `settings/meta_watchdog.py` |
| 4 | StatefulSet 동적 감지 | `meta/recovery_adapter.py` |
| 5 | 워커 이름 환경변수화 | `settings/meta_watchdog.py`, `meta/watchdog.py` |
| 6 | Stage 1 예외 세분화 (화이트리스트) | `meta/watchdog.py` |

---

## 0.1 전제조건 (Prerequisites)

### P1: Redis 볼륨 PVC 전환

Stage 2 인프라 재시작은 **P0 데이터(CB 상태, Recovery Lock)가 Pod 재시작 시 보존된다는 전제**하에 안전하게 수행된다.

**현재 상태** (`k8s/redis-config.yaml` L150–L152):

```yaml
volumes:
  - name: redis-data
    emptyDir: {}    # ← Pod 삭제 시 데이터 소멸
```

**P0 키 정의** (`k8s/redis-config.yaml` 주석 L16–L18):

```
P0: Emergency state, Recovery locks, Circuit breaker state  → TTL 없음 (영구)
P1: Session data, Metrics cache
P2: Analytics, Logs
```

**문제**: 현재 메모리 정책 `volatile-lru`로 P0 키(TTL 없음)는 eviction에서 보호되지만,
`emptyDir` 볼륨이므로 Pod 재생성 시 RDB 스냅샷 자체가 소멸한다.
CB 상태 초기화 → 보호 중이던 서비스에 트래픽 폭풍(Thundering Herd) 발생 가능.

**필수 변경** — `k8s/redis-config.yaml`:

```yaml
volumes:
  - name: redis-data
    persistentVolumeClaim:
      claimName: redis-data-pvc
```

추가 PVC 정의:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-data-pvc
  namespace: selfhealing
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
```

> **259 구현 전 이 PVC 전환이 완료되어야 한다.**
> Stage 2가 `emptyDir` 상태에서 실행되면 자가 치유가 아닌 자가 파괴가 된다.

### P2: RedisCacheAdapter.reconnect() 메서드 선행 추가

Stage 1이 실제 서비스 커넥션 풀을 갱신하려면 `reconnect()` 메서드가 필요하다.
현재 `RedisCacheAdapter`에는 `close()`, `reconnect()`, `reset_pool()` 메서드가 없다
(`adapters/cache/redis_adapter.py` 전체 611줄 검증 완료).

이 선행 변경의 상세 설계는 **Section 2.2**에서 정의한다.

---

## 1. 현재 상태 분석

### 1.1 `_recover_redis()` — 현재 코드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py` L475–L503

```python
def _recover_redis(self, result: ProbeResult) -> bool:
    try:
        logger.info("[SelfHealerWatchdog] Redis connection reset")
        try:
            from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
            adapter = RedisCacheAdapter()
            adapter._redis.ping()
            return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] Redis recovery error: {e}")
        return False
```

**문제점**:
- `RedisCacheAdapter()` 재생성으로 **새 ConnectionPool**을 만들어 ping만 확인하고 버림 — 가짜 복구
- 기존 서비스 모듈이 사용 중인 싱글톤 인스턴스의 dead 커넥션은 방치됨
- 연결 리셋 실패 시 즉시 `False` 반환 — 인프라 레벨 복구 시도 없음
- `_recover_dlq()`와 달리 `RecoveryAdapter`를 전혀 사용하지 않음
- `AuthenticationError` 등 재시작해도 해결 불가한 예외도 Stage 2로 넘어감
- `"redis"` 워커 이름이 하드코딩되어 환경별 대응 불가

### 1.2 `_recover_dlq()` — 참조 패턴

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py` L442–L473

```python
def _recover_dlq(self, result: ProbeResult) -> bool:
    try:
        logger.info("[SelfHealerWatchdog] Attempting DLQ recovery")
        try:
            from selfhealing.meta.recovery_adapter import get_recovery_adapter
            adapter = get_recovery_adapter()
            result = adapter.restart_worker("celery-dlq-worker")
            return result.success
        except ImportError:
            pass
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] DLQ recovery error: {e}")
        return False
```

**핵심**: `get_recovery_adapter()` → `adapter.restart_worker()` 호출 패턴이 이미 존재.

> **참고**: `_recover_dlq()`도 `"celery-dlq-worker"` 하드코딩 문제가 동일하다 (Section 2.5에서 함께 해결).

### 1.3 `RecoveryInfrastructureAdapter` — 사용 가능한 메서드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py`

| 메서드 | 시그니처 | K8s 구현 |
|--------|---------|---------|
| `restart_worker` | `(worker_name: str) → RecoveryResult` | Deployment annotation patch → rolling restart |
| `scale_deployment` | `(name: str, replicas: int) → RecoveryResult` | `patch_namespaced_deployment_scale` |
| `delete_pod` | `(pod_name: str, namespace: str) → RecoveryResult` | `delete_namespaced_pod` → ReplicaSet 재생성 |

### 1.4 K8s Redis 배포 정보

**파일**: `k8s/redis-config.yaml` L91–L121

| 항목 | 값 |
|------|-----|
| `kind` | `Deployment` |
| `metadata.name` | `redis` |
| `metadata.namespace` | `selfhealing` |
| `image` | `redis:7-alpine` |
| `containerPort` | `6379` |

### 1.5 `get_recovery_adapter()` — 어댑터 선택 체인

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L530–L554

```python
def get_recovery_adapter() -> RecoveryInfrastructureAdapter:
    adapter_type = os.environ.get("SELFHEALING_RECOVERY_ADAPTER", "kubernetes").lower()
    if adapter_type == "noop":
        return NoOpRecoveryAdapter()
    elif adapter_type == "docker":
        return DockerComposeRecoveryAdapter()
    else:
        adapter = KubernetesRecoveryAdapter()
        if adapter.is_available():
            return adapter
        docker_adapter = DockerComposeRecoveryAdapter()
        if docker_adapter.is_available():
            return docker_adapter
        return NoOpRecoveryAdapter()
```

선택 체인: `K8s → Docker → NoOp` (환경변수 `SELFHEALING_RECOVERY_ADAPTER`로 오버라이드)

### 1.6 `ProviderRegistry` — 기존 싱글톤 인프라

**파일**: `packages/selfhealing-python/src/selfhealing/factory.py` L74–L75, L218–L247

```python
class ProviderRegistry:
    # Singleton instances (for reuse)
    _instances: dict[str, object] = {}

    @classmethod
    def get_cache(
        cls,
        name: str | None = None,
        singleton: bool = True,
    ) -> CacheProviderInterface:
        name = name or cls._default_cache
        if singleton:
            key = f"cache:{name}"
            if key in cls._instances:
                return cls._instances[key]
        if name not in cls._cache_providers:
            raise ValueError(...)
        instance = cls._cache_providers[name]()
        if singleton:
            cls._instances[key] = instance
        return instance
```

**핵심**: `get_cache("redis", singleton=True)` 호출 시 시스템 전체가 공유하는 단일 `RedisCacheAdapter` 인스턴스를 반환.
Stage 1은 이 싱글톤 인스턴스를 통해 커넥션 풀을 갱신해야 한다.

### 1.7 `KubernetesRecoveryAdapter.restart_worker()` — Deployment 하드코딩

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L247–L250

```python
self._apps_v1.patch_namespaced_deployment(
    name=worker_name,
    namespace=self._namespace,
    body=patch,
)
```

`patch_namespaced_deployment`만 호출 — StatefulSet 대상 호출 시 K8s API 404 에러.
`_apps_v1`는 `client.AppsV1Api()`로 `patch_namespaced_stateful_set`도 호출 가능하나 미사용.

### 1.8 Watchdog 쿨다운 부재

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py` L167–L179

```python
def _process_unhealthy_component(self, name: str, result: Any) -> bool:
    self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1
    if self._consecutive_failures[name] < self._settings.self_cb_failure_threshold:
        return False
    # ← 임계치 이상이면 매 프로브 주기(30초)마다 복구 시도 — 쿨다운 없음
    recovered = self._attempt_recovery(name, result)
```

`_attempt_recovery()` (L252–L310)에도 `last_recovery_time` 추적이 없다.
에스컬레이션에만 `escalation_cooldown_seconds`(기본 3600초)가 존재하고,
복구 액션에 대한 쿨다운은 전무하다.

---

## 2. 구현 설계

### 2.1 전체 복구 흐름

```
_attempt_recovery("redis", result)
  ├── 쿨다운 확인 → 쿨다운 중이면 SKIP (Section 2.6)
  └── _recover_redis(result)
        │
        ├── Stage 1: 진성 커넥션 풀 복구 (Section 2.3)
        │     ├── ProviderRegistry.get_cache("redis") — 싱글톤 인스턴스 획득
        │     ├── adapter.reconnect() — 기존 dead 커넥션 해제 + 새 커넥션 확보
        │     └── 성공 시 return True
        │
        ├── 예외 필터링 (Section 2.4)
        │     └── AuthenticationError 등 non-recoverable → return False (Stage 2 스킵)
        │
        └── Stage 2: RecoveryAdapter 인프라 재시작 (Section 2.5)
              ├── settings.redis_workload_name으로 타겟 이름 획득
              ├── recovery_adapter.restart_worker(name) — Deployment/StatefulSet 자동 감지
              └── 성공/실패 반환
```

### 2.2 선행 변경 1: `RedisCacheAdapter.reconnect()` 추가

#### 설계 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. `RedisCacheAdapter()`를 매번 새로 생성 | 현재 방식, 기존 인스턴스의 dead 커넥션 방치 | ✗ |
| B. `ProviderRegistry`에서 싱글톤 교체 | `_instances["cache:redis"]`를 새 객체로 대체 | ✗ |
| **C. 싱글톤 인스턴스에 `reconnect()` 호출** | `connection_pool.disconnect()` + `ping()` | **✓** |

**선택 이유**: `redis-py`의 `ConnectionPool`은 `disconnect()` 메서드를 제공하여 기존 커넥션을 모두 정리한다.
이후 `ping()` 호출 시 풀이 자동으로 새 커넥션을 생성한다.
`ProviderRegistry.get_cache("redis", singleton=True)`가 이미 시스템 전체의 단일 인스턴스를 반환하므로,
그 인스턴스에서 `reconnect()`를 호출하면 **모든 모듈이 사용하는 커넥션 풀이 갱신**된다.

선택지 B(싱글톤 교체)는 기존 모듈이 이전 인스턴스 참조를 계속 보유하는 문제(stale reference)를 해결하지 못한다.

#### 구현 코드

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/cache/redis_adapter.py`

```python
def reconnect(self) -> bool:
    """
    커넥션 풀 리셋 — 기존 dead 커넥션 해제 후 재연결.

    redis-py의 ConnectionPool.disconnect()는 풀 내 모든 커넥션을 닫는다.
    이후 ping() 호출 시 풀이 자동으로 새 커넥션을 생성한다.

    Returns:
        재연결 성공 여부
    """
    try:
        self._redis.connection_pool.disconnect()
        return self._redis.ping()
    except Exception as e:
        logger.error(f"[RedisCache] Reconnect failed: {e}")
        return False
```

**변경 없는 파일**: `interfaces/cache_provider.py` — `CacheProviderInterface`는 추상 클래스이고,
`reconnect()`는 Redis 전용 구현이므로 인터페이스에 추가하지 않는다.
`InMemoryCacheAdapter`에서 불필요한 no-op 구현을 강제하지 않기 위함이다.

### 2.3 Stage 1: 진성 커넥션 풀 복구

**기존** (가짜 복구):

```python
from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter
adapter = RedisCacheAdapter()   # 새 인스턴스 + 새 ConnectionPool → 사용 후 버려짐
adapter._redis.ping()
```

**변경 후** (진성 복구):

```python
from selfhealing.factory import ProviderRegistry
adapter = ProviderRegistry.get_cache("redis")  # 시스템 전체 싱글톤 인스턴스
adapter.reconnect()                             # 실제 서비스 커넥션 풀 갱신
```

`ProviderRegistry.get_cache("redis", singleton=True)` (factory.py L234–L237):
- `_instances["cache:redis"]`에 캐싱된 인스턴스를 반환
- 이 인스턴스는 시스템의 모든 모듈이 공유
- `reconnect()`가 이 인스턴스의 `connection_pool.disconnect()`를 호출하면,
  모든 모듈의 다음 Redis 명령에서 새 커넥션이 자동 생성됨

### 2.4 Stage 1: 예외 세분화 — 화이트리스트 방식

#### 설계 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. 블랙리스트 (특정 예외만 제외) | `except AuthenticationError → return False` | ✗ |
| **B. 화이트리스트 (특정 예외만 Stage 2로)** | `_INFRA_RECOVERABLE_ERRORS` 튜플 정의 | **✓** |

**선택 이유**: 화이트리스트 방식이 더 안전하다. 예상치 못한 새로운 예외 유형(예: 향후 Redis 버전의 새 에러)이 자동으로 Stage 2로 넘어가는 것을 원천 차단한다.
`redis-py`의 예외 계층은 명확하게 분리되어 있다:

```
redis.exceptions.RedisError
  ├── ConnectionError        ← 인프라 복구 대상
  ├── TimeoutError           ← 인프라 복구 대상
  ├── BusyLoadingError       ← 인프라 복구 대상 (RDB 로드 중)
  ├── AuthenticationError    ← 설정 오류 (재시작 무의미)
  └── ResponseError          ← 명령어/메모리 오류 (재시작 무의미)
```

#### 구현

```python
# _recover_redis() 내부 Stage 1에서 사용
import redis as redis_lib

_INFRA_RECOVERABLE_ERRORS = (
    redis_lib.exceptions.ConnectionError,
    redis_lib.exceptions.TimeoutError,
    redis_lib.exceptions.BusyLoadingError,
)
```

```python
# Stage 1 예외 분기
try:
    adapter.reconnect()
    return True
except _INFRA_RECOVERABLE_ERRORS as e:
    logger.warning(f"[SelfHealerWatchdog] Redis Stage 1 failed (recoverable): {e}")
    # → Stage 2 진행
except Exception as e:
    logger.error(
        f"[SelfHealerWatchdog] Redis Stage 1 failed (non-recoverable, skip Stage 2): {e}"
    )
    return False
```

### 2.5 워커 이름 환경변수화

#### 설계 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. `os.environ.get("REDIS_WORKER_NAME", "redis")` 직접 호출 | 간단하지만 검증/문서화 미통합 | ✗ |
| **B. `MetaWatchdogSettings`에 Pydantic 필드** | 프로젝트 설정 패턴 일치, 자동 검증 | **✓** |

**선택 이유**: 프로젝트 전역이 `SELFHEALING_META_*` 접두사 환경변수 + Pydantic Settings 패턴을 따른다
(settings/meta_watchdog.py의 `model_config = SettingsConfigDict(env_prefix="SELFHEALING_META_")`).
직접 `os.environ.get()` 호출은 이 일원화를 깨뜨린다.

필드 이름은 `redis_workload_name`을 채택한다 — StatefulSet 동적 감지(Section 2.7)를 고려해 리소스 타입에 중립적인 용어.

#### 구현 코드

**파일**: `packages/selfhealing-python/src/selfhealing/settings/meta_watchdog.py`

```python
# === 워크로드 이름 설정 (K8s Deployment/StatefulSet 이름) ===

redis_workload_name: str = Field(
    default="redis",
    description="Redis Deployment/StatefulSet 이름 (K8s 리소스명)",
)

dlq_worker_workload_name: str = Field(
    default="celery-dlq-worker",
    description="DLQ Worker Deployment 이름 (K8s 리소스명)",
)
```

**환경변수**: `SELFHEALING_META_REDIS_WORKLOAD_NAME`, `SELFHEALING_META_DLQ_WORKER_WORKLOAD_NAME`

**사용처 변경**:

```python
# _recover_redis() — 기존: restart_worker("redis")
recovery_adapter.restart_worker(self._settings.redis_workload_name)

# _recover_dlq() — 기존: restart_worker("celery-dlq-worker")
adapter.restart_worker(self._settings.dlq_worker_workload_name)
```

### 2.6 Recovery 쿨다운 메커니즘

#### 설계 선택 — 쿨다운 위치

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. `_recover_redis()` 내부에 쿨다운 | Redis에만 적용, 다른 컴포넌트는 별도 구현 필요 | ✗ |
| **B. `_attempt_recovery()` 진입부에 쿨다운** | 모든 컴포넌트에 통합 적용 | **✓** |

**선택 이유**: `_attempt_recovery()`는 모든 `_recover_*` 메서드의 진입점이다 (watchdog.py L252–L310).
여기에 쿨다운을 두면 redis, dlq, circuit_breaker, recovery_pipeline 모두에 일괄 적용된다.
에스컬레이션 쿨다운(`escalation_cooldown_seconds`)도 동일한 수준에서 관리되므로 아키텍처 일관성이 유지된다.

#### 재시작 폭주 시나리오 (쿨다운 미적용 시)

```
T=0s    : Redis Probe ❌ → consecutive_failures["redis"] = 1
T=30s   : Redis Probe ❌ → consecutive_failures["redis"] = 2
...
T=120s  : consecutive_failures["redis"] = 5 → Stage 1 실패 → Stage 2 restart_worker("redis")
T=150s  : Redis Probe ❌ (아직 재시작 중) → Stage 2 또 실행!
T=180s  : 또 실행... (K8s API 남용)
```

#### 설정 필드

**파일**: `packages/selfhealing-python/src/selfhealing/settings/meta_watchdog.py`

```python
recovery_cooldown_seconds: float = Field(
    default=300.0,
    description="동일 컴포넌트 복구 시도 쿨다운 (초, 기본 5분)",
    ge=30.0,
)
```

**환경변수**: `SELFHEALING_META_RECOVERY_COOLDOWN_SECONDS`

기본값 300초 근거: K8s rolling restart가 완료되기까지 일반적으로 30초~3분.
5분은 Redis Pod가 재시작되고 readinessProbe(5초 간격)를 통과하기에 충분한 유예 시간이다.

#### 구현 코드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/watchdog.py`

`__init__`에 추가:

```python
# Recovery 쿨다운 추적
self._last_recovery_time: dict[str, float] = {}
```

`_attempt_recovery()` 진입부에 쿨다운 가드 추가:

```python
def _attempt_recovery(self, component: str, result: ProbeResult) -> bool:
    # === 쿨다운 확인 ===
    now = time.time()
    last_time = self._last_recovery_time.get(component, 0.0)
    elapsed = now - last_time
    if elapsed < self._settings.recovery_cooldown_seconds:
        remaining = self._settings.recovery_cooldown_seconds - elapsed
        logger.info(
            f"[SelfHealerWatchdog] Recovery cooldown active for {component}: "
            f"{remaining:.0f}s remaining"
        )
        return False

    start_time = now
    # ... (기존 복구 로직)

    # 복구 시도 후 타임스탬프 기록 (성공/실패 무관)
    self._last_recovery_time[component] = start_time
```

쿨다운은 **성공/실패 무관**하게 기록한다 — 실패 후 즉시 재시도하면 같은 결과가 반복될 가능성이 높고,
K8s rolling restart 같은 비동기 작업은 결과가 지연되어 실패로 판정되는 경우가 빈번하기 때문이다.

### 2.7 선행 변경 2: `KubernetesRecoveryAdapter` — StatefulSet 동적 감지

#### 설계 선택

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. 매번 Deployment read → 404시 StatefulSet read | API 호출 2~4회/복구 | ✗ |
| **B. 초기화 시 Kind 감지 + 캐싱** | API 호출 1회 (이후 캐시 히트) | **✓** |

**선택 이유**:
복구는 긴급 상황에서 실행된다. 매번 K8s API를 2~4회 호출하는 것은 불필요한 지연.
리소스 Kind는 런타임에 변경되지 않으므로 최초 1회 감지 후 캐싱이 효율적이다.

#### StatefulSet annotation patch 주의사항

Deployment와 달리 StatefulSet은 `updateStrategy`에 따라 동작이 다르다:
- `RollingUpdate` (기본값): annotation patch → 자동 rolling restart (**Deployment와 동일**)
- `OnDelete`: annotation patch만으로는 재시작 미발생

현재 Redis는 Deployment(`k8s/redis-config.yaml`)이지만, 향후 StatefulSet 전환 시
`RollingUpdate` 전략이 기본값이므로 annotation patch 방식이 동일하게 동작한다.

#### 구현 코드

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py`

`KubernetesRecoveryAdapter.__init__`에 추가:

```python
# 리소스 Kind 캐시 (Deployment vs StatefulSet)
self._resource_kind_cache: dict[str, str] = {}
```

새 메서드:

```python
def _detect_resource_kind(self, name: str) -> str:
    """
    워크로드 리소스 Kind를 감지하고 캐싱.

    탐색 체인: Deployment → StatefulSet
    최초 1회 API 호출 후 캐싱하여 이후 호출에서는 API 미호출.

    Args:
        name: 워크로드 이름

    Returns:
        "Deployment" 또는 "StatefulSet"

    Raises:
        Exception: 두 Kind 모두에서 리소스를 찾지 못한 경우
    """
    cache_key = f"{self._namespace}/{name}"
    if cache_key in self._resource_kind_cache:
        return self._resource_kind_cache[cache_key]

    from kubernetes.client.exceptions import ApiException

    # Deployment 탐색
    try:
        self._apps_v1.read_namespaced_deployment(name, self._namespace)
        self._resource_kind_cache[cache_key] = "Deployment"
        logger.debug(
            f"[KubernetesRecoveryAdapter] Detected {name} as Deployment"
        )
        return "Deployment"
    except ApiException as e:
        if e.status != 404:
            raise

    # StatefulSet 탐색
    try:
        self._apps_v1.read_namespaced_stateful_set(name, self._namespace)
        self._resource_kind_cache[cache_key] = "StatefulSet"
        logger.debug(
            f"[KubernetesRecoveryAdapter] Detected {name} as StatefulSet"
        )
        return "StatefulSet"
    except ApiException as e:
        if e.status != 404:
            raise

    raise Exception(
        f"Workload '{name}' not found as Deployment or StatefulSet "
        f"in namespace '{self._namespace}'"
    )
```

`restart_worker()` 변경:

```python
def restart_worker(self, worker_name: str) -> RecoveryResult:
    if not self._is_available:
        return RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=False,
            target=worker_name,
            message="K8s client not available",
            timestamp=datetime.now(timezone.utc),
        )

    try:
        self._validate_service_name(worker_name)

        kind = self._detect_resource_kind(worker_name)

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "selfhealing.watchdog/restartedAt":
                                datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            }
        }

        if kind == "Deployment":
            self._apps_v1.patch_namespaced_deployment(
                name=worker_name,
                namespace=self._namespace,
                body=patch,
            )
        else:  # StatefulSet
            self._apps_v1.patch_namespaced_stateful_set(
                name=worker_name,
                namespace=self._namespace,
                body=patch,
            )

        logger.info(
            f"[KubernetesRecoveryAdapter] Triggered restart: "
            f"{worker_name} ({kind})"
        )
        return RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target=worker_name,
            message=f"Rolling restart triggered ({kind})",
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.error(f"[KubernetesRecoveryAdapter] Restart failed: {e}")
        return RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=False,
            target=worker_name,
            message=str(e),
            timestamp=datetime.now(timezone.utc),
        )
```

### 2.8 `_recover_redis()` — 최종 구현 코드

```python
def _recover_redis(self, result: ProbeResult) -> bool:
    """
    Redis 연결 복구 — 2단계 전략.

    Stage 1: ProviderRegistry 싱글톤의 커넥션 풀 리셋 (소프트 복구)
    Stage 2: RecoveryAdapter를 통한 인프라 재시작 (하드 복구)

    예외 세분화:
    - ConnectionError, TimeoutError, BusyLoadingError → Stage 2 진행
    - AuthenticationError, ResponseError 등 → Stage 2 스킵 (재시작 무의미)

    Args:
        result: 프로브 결과

    Returns:
        복구 성공 여부
    """
    # === Stage 1: 진성 커넥션 풀 복구 ===
    try:
        logger.info("[SelfHealerWatchdog] Redis recovery Stage 1: connection pool reset")

        from selfhealing.factory import ProviderRegistry

        adapter = ProviderRegistry.get_cache("redis")
        if adapter.reconnect():
            logger.info(
                "[SelfHealerWatchdog] Redis Stage 1 success: "
                "connection pool restored"
            )
            return True
        # reconnect()가 False 반환 — ping 실패
        logger.warning(
            "[SelfHealerWatchdog] Redis Stage 1 failed: reconnect returned False"
        )
    except ImportError:
        logger.warning("[SelfHealerWatchdog] ProviderRegistry not available")
    except Exception as e:
        import redis as redis_lib

        _INFRA_RECOVERABLE_ERRORS = (
            redis_lib.exceptions.ConnectionError,
            redis_lib.exceptions.TimeoutError,
            redis_lib.exceptions.BusyLoadingError,
        )

        if isinstance(e, _INFRA_RECOVERABLE_ERRORS):
            logger.warning(
                f"[SelfHealerWatchdog] Redis Stage 1 failed (recoverable): {e}, "
                "proceeding to Stage 2 (infrastructure restart)"
            )
        else:
            logger.error(
                f"[SelfHealerWatchdog] Redis Stage 1 failed "
                f"(non-recoverable, skip Stage 2): {e}"
            )
            return False

    # === Stage 2: RecoveryAdapter 인프라 재시작 ===
    try:
        from selfhealing.meta.recovery_adapter import get_recovery_adapter

        recovery_adapter = get_recovery_adapter()
        workload_name = self._settings.redis_workload_name
        recovery_result = recovery_adapter.restart_worker(workload_name)
        if recovery_result.success:
            logger.info(
                "[SelfHealerWatchdog] Redis Stage 2 success: "
                f"{recovery_result.message}"
            )
        else:
            logger.error(
                "[SelfHealerWatchdog] Redis Stage 2 failed: "
                f"{recovery_result.message}"
            )
        return recovery_result.success
    except ImportError:
        logger.warning(
            "[SelfHealerWatchdog] RecoveryAdapter not available"
        )
        return False
    except Exception as e:
        logger.error(f"[SelfHealerWatchdog] Redis Stage 2 error: {e}")
        return False
```

### 2.9 변경 범위

| 파일 | 변경 | 내용 |
|------|------|------|
| `adapters/cache/redis_adapter.py` | 메서드 추가 | `reconnect()` |
| `settings/meta_watchdog.py` | 필드 추가 | `recovery_cooldown_seconds`, `redis_workload_name`, `dlq_worker_workload_name` |
| `meta/watchdog.py` | `__init__` 수정 | `_last_recovery_time` 딕셔너리 추가 |
| `meta/watchdog.py` | `_attempt_recovery()` 수정 | 쿨다운 가드 추가 |
| `meta/watchdog.py` | `_recover_redis()` 교체 | Stage 1 진성 복구 + 예외 필터링 + Stage 2 |
| `meta/watchdog.py` | `_recover_dlq()` 수정 | `self._settings.dlq_worker_workload_name` 사용 |
| `meta/recovery_adapter.py` | `KubernetesRecoveryAdapter` 수정 | `_detect_resource_kind()` 추가, `restart_worker()` 리팩토링 |

**새로 추가되는 파일**: 없음

---

## 3. K8s 환경 동작 상세

### 3.1 `restart_worker()` 실행 흐름 (Deployment/StatefulSet 동적 감지)

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py`

```
restart_worker("redis")
  ├── _detect_resource_kind("redis")
  │     ├── 캐시 확인 → 히트 시 즉시 반환
  │     ├── read_namespaced_deployment("redis", "selfhealing") → 200 OK → "Deployment"
  │     └── (404 시) read_namespaced_stateful_set("redis", "selfhealing") → "StatefulSet"
  │
  ├── kind == "Deployment"
  │     └── patch_namespaced_deployment(annotation patch) → rolling restart
  │
  └── kind == "StatefulSet"
        └── patch_namespaced_stateful_set(annotation patch) → rolling restart
```

현재 `k8s/redis-config.yaml`의 Kind는 `Deployment` → `_detect_resource_kind("redis")`는 첫 번째 탐색에서 즉시 "Deployment"를 반환하고 캐싱한다.

### 3.2 RBAC 요구사항

**파일**: `k8s/selfhealing-rbac.yaml` (기존)

```yaml
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "patch"]       # restart_worker (Deployment) + _detect_resource_kind
  - apiGroups: ["apps"]
    resources: ["deployments/scale"]
    verbs: ["get", "patch"]               # scale_deployment
  - apiGroups: ["apps"]
    resources: ["statefulsets"]
    verbs: ["get", "list", "patch"]       # restart_worker (StatefulSet) + _detect_resource_kind
```

**추가 RBAC 불필요** — 기존 `selfhealing-watchdog` ServiceAccount에 `statefulsets` get/patch 권한 이미 존재.

---

## 4. Docker 환경 동작 상세

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` L353–L479

`DockerComposeRecoveryAdapter.restart_worker()`는 Deployment/StatefulSet 구분이 불필요하다:

```python
subprocess.run(
    ["docker-compose", "restart", worker_name],  # settings.redis_workload_name
    capture_output=True, text=True, timeout=60
)
```

StatefulSet 동적 감지는 `KubernetesRecoveryAdapter`에서만 적용되며,
`DockerComposeRecoveryAdapter`와 `NoOpRecoveryAdapter`는 변경 없음.

---

## 5. 테스트 전략

### 5.1 `reconnect()` 단위 테스트

```python
class TestRedisCacheAdapterReconnect:
    """RedisCacheAdapter.reconnect() 테스트."""

    def test_reconnect_success(self, mock_redis_client):
        """disconnect() 후 ping() 성공."""
        adapter = RedisCacheAdapter(client=mock_redis_client)
        mock_redis_client.ping.return_value = True
        assert adapter.reconnect() is True
        mock_redis_client.connection_pool.disconnect.assert_called_once()

    def test_reconnect_ping_fail(self, mock_redis_client):
        """disconnect() 후 ping() 실패."""
        adapter = RedisCacheAdapter(client=mock_redis_client)
        mock_redis_client.ping.return_value = False
        assert adapter.reconnect() is False

    def test_reconnect_exception(self, mock_redis_client):
        """disconnect() 중 예외."""
        adapter = RedisCacheAdapter(client=mock_redis_client)
        mock_redis_client.connection_pool.disconnect.side_effect = Exception("pool error")
        assert adapter.reconnect() is False
```

### 5.2 `_recover_redis()` 2단계 복구 테스트

```python
class TestRecoverRedis:
    """_recover_redis() 2단계 복구 테스트."""

    def test_stage1_reconnect_success_skips_stage2(
        self, watchdog, mock_provider_registry
    ):
        """Stage 1 reconnect 성공 시 Stage 2를 호출하지 않는다."""
        mock_adapter = MagicMock()
        mock_adapter.reconnect.return_value = True
        mock_provider_registry.get_cache.return_value = mock_adapter

        result = watchdog._recover_redis(mock_probe_result)
        assert result is True

    def test_stage1_connection_error_triggers_stage2(
        self, watchdog, mock_provider_registry, mock_recovery_adapter
    ):
        """Stage 1 ConnectionError 시 Stage 2 RecoveryAdapter를 호출한다."""
        import redis as redis_lib
        mock_adapter = MagicMock()
        mock_adapter.reconnect.side_effect = redis_lib.exceptions.ConnectionError
        mock_provider_registry.get_cache.return_value = mock_adapter
        mock_recovery_adapter.restart_worker.return_value = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target="redis",
            message="Rolling restart triggered (Deployment)",
            timestamp=datetime.now(timezone.utc),
        )

        result = watchdog._recover_redis(mock_probe_result)
        assert result is True
        mock_recovery_adapter.restart_worker.assert_called_once_with("redis")

    def test_stage1_auth_error_skips_stage2(
        self, watchdog, mock_provider_registry
    ):
        """AuthenticationError 시 Stage 2를 스킵하고 즉시 False."""
        import redis as redis_lib
        mock_adapter = MagicMock()
        mock_adapter.reconnect.side_effect = redis_lib.exceptions.AuthenticationError
        mock_provider_registry.get_cache.return_value = mock_adapter

        result = watchdog._recover_redis(mock_probe_result)
        assert result is False

    def test_both_stages_fail(
        self, watchdog, mock_provider_registry, mock_recovery_adapter
    ):
        """Stage 1, 2 모두 실패 시 False 반환."""
        import redis as redis_lib
        mock_adapter = MagicMock()
        mock_adapter.reconnect.side_effect = redis_lib.exceptions.ConnectionError
        mock_provider_registry.get_cache.return_value = mock_adapter
        mock_recovery_adapter.restart_worker.return_value = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=False,
            target="redis",
            message="K8s client not available",
            timestamp=datetime.now(timezone.utc),
        )

        result = watchdog._recover_redis(mock_probe_result)
        assert result is False

    def test_stage2_uses_settings_workload_name(
        self, watchdog, mock_provider_registry, mock_recovery_adapter
    ):
        """Stage 2가 settings의 redis_workload_name을 사용한다."""
        import redis as redis_lib
        mock_adapter = MagicMock()
        mock_adapter.reconnect.side_effect = redis_lib.exceptions.TimeoutError
        mock_provider_registry.get_cache.return_value = mock_adapter
        watchdog._settings.redis_workload_name = "redis-master"
        mock_recovery_adapter.restart_worker.return_value = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target="redis-master",
            message="Rolling restart triggered",
            timestamp=datetime.now(timezone.utc),
        )

        watchdog._recover_redis(mock_probe_result)
        mock_recovery_adapter.restart_worker.assert_called_once_with("redis-master")
```

### 5.3 쿨다운 메커니즘 테스트

```python
class TestRecoveryCooldown:
    """_attempt_recovery() 쿨다운 테스트."""

    def test_cooldown_blocks_repeated_recovery(self, watchdog):
        """쿨다운 기간 내 동일 컴포넌트 복구 차단."""
        watchdog._settings.recovery_cooldown_seconds = 300.0
        watchdog._last_recovery_time["redis"] = time.time()

        result = watchdog._attempt_recovery("redis", mock_probe_result)
        assert result is False

    def test_cooldown_expired_allows_recovery(self, watchdog):
        """쿨다운 만료 후 복구 허용."""
        watchdog._settings.recovery_cooldown_seconds = 300.0
        watchdog._last_recovery_time["redis"] = time.time() - 301

        # 복구 시도 진행 (성공/실패와 무관하게 쿨다운 통과)
        # → _recover_redis()가 호출됨을 mock으로 검증

    def test_different_component_not_affected(self, watchdog):
        """다른 컴포넌트의 쿨다운에 영향받지 않는다."""
        watchdog._settings.recovery_cooldown_seconds = 300.0
        watchdog._last_recovery_time["dlq"] = time.time()

        # "redis"는 쿨다운 없으므로 복구 진행
```

### 5.4 StatefulSet 동적 감지 테스트

```python
class TestDetectResourceKind:
    """_detect_resource_kind() 테스트."""

    def test_deployment_detected(self, k8s_adapter, mock_apps_v1):
        """Deployment가 존재하면 'Deployment' 반환."""
        mock_apps_v1.read_namespaced_deployment.return_value = MagicMock()
        assert k8s_adapter._detect_resource_kind("redis") == "Deployment"

    def test_statefulset_fallback(self, k8s_adapter, mock_apps_v1):
        """Deployment 404 → StatefulSet 탐색."""
        from kubernetes.client.exceptions import ApiException
        mock_apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)
        mock_apps_v1.read_namespaced_stateful_set.return_value = MagicMock()
        assert k8s_adapter._detect_resource_kind("redis") == "StatefulSet"

    def test_result_cached(self, k8s_adapter, mock_apps_v1):
        """2회 호출 시 API는 1회만 호출."""
        mock_apps_v1.read_namespaced_deployment.return_value = MagicMock()
        k8s_adapter._detect_resource_kind("redis")
        k8s_adapter._detect_resource_kind("redis")
        assert mock_apps_v1.read_namespaced_deployment.call_count == 1

    def test_not_found_raises(self, k8s_adapter, mock_apps_v1):
        """Deployment/StatefulSet 모두 404 시 예외."""
        from kubernetes.client.exceptions import ApiException
        mock_apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)
        mock_apps_v1.read_namespaced_stateful_set.side_effect = ApiException(status=404)
        with pytest.raises(Exception, match="not found"):
            k8s_adapter._detect_resource_kind("redis")

    def test_restart_worker_statefulset(self, k8s_adapter, mock_apps_v1):
        """StatefulSet 대상 restart가 patch_namespaced_stateful_set을 호출."""
        from kubernetes.client.exceptions import ApiException
        mock_apps_v1.read_namespaced_deployment.side_effect = ApiException(status=404)
        mock_apps_v1.read_namespaced_stateful_set.return_value = MagicMock()

        result = k8s_adapter.restart_worker("redis")
        assert result.success is True
        mock_apps_v1.patch_namespaced_stateful_set.assert_called_once()
        assert "StatefulSet" in result.message
```

### 5.5 Audit 연동 확인

기존 `_attempt_recovery()` (L252–L310)가 `_recover_redis()` 전후로 `RecoveryAuditRecorder`를 통해 감사 로그를 기록하므로, Stage 2 추가 시에도 Audit는 **자동으로 기록됨** — 별도 Audit 코드 변경 불필요.

---

## 6. 영향 분석

| 항목 | 영향 |
|------|------|
| Stage 1 동작 | 가짜 복구 → 진성 복구 (`ProviderRegistry` 싱글톤 + `reconnect()`) |
| Stage 2 동작 | 신규 추가 (`RecoveryAdapter` 인프라 재시작) |
| 예외 처리 | 무차별 `except Exception` → 화이트리스트 기반 예외 분류 |
| 워커 이름 | 하드코딩 → `MetaWatchdogSettings` 필드 (환경변수 오버라이드) |
| 쿨다운 | 미존재 → `_attempt_recovery()` 진입부에 컴포넌트별 쿨다운 |
| StatefulSet | 미지원 → `_detect_resource_kind()` 동적 감지 + 캐싱 |
| Audit 시스템 | 변경 불필요 (`_attempt_recovery`에서 처리) |
| RBAC | 추가 불필요 (기존 `statefulsets` get/patch 권한 존재) |
| `_recover_dlq()` | `self._settings.dlq_worker_workload_name` 사용으로 변경 |
| `interfaces/cache_provider.py` | 변경 없음 (`reconnect()`는 `RedisCacheAdapter` 구체 클래스 전용) |
| `InMemoryCacheAdapter` | 변경 없음 |
| `DockerComposeRecoveryAdapter` | 변경 없음 |
| `NoOpRecoveryAdapter` | 변경 없음 |

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `meta/watchdog.py` | 수정 대상 (`_recover_redis`, `_recover_dlq`, `_attempt_recovery`, `__init__`) |
| `meta/recovery_adapter.py` | 수정 대상 (`KubernetesRecoveryAdapter.restart_worker`, `_detect_resource_kind`) |
| `adapters/cache/redis_adapter.py` | 수정 대상 (`reconnect()` 메서드 추가) |
| `settings/meta_watchdog.py` | 수정 대상 (3개 필드 추가) |
| `factory.py` | 사용만 (변경 없음, `ProviderRegistry.get_cache("redis")`) |
| `interfaces/cache_provider.py` | 변경 없음 |
| `k8s/redis-config.yaml` | 전제조건 (PVC 전환 필요) |
| `k8s/selfhealing-rbac.yaml` | 참조 (기존 RBAC 충분) |

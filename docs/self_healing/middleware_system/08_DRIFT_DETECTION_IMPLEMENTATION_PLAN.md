# 08. Drift Detection 구현 계획

> **Version**: 1.1.0
> **Created**: 2026-01-15
> **Updated**: 2026-01-15
> **Status**: ✅ 구현 완료
> **Author**: AI Assistant
> **Reference**: 14_METRIC_COLLECTION_CORE.md, 15_METRIC_COLLECTION_ADVANCED.md

---

## 1. 개요

### 1.1 배경

Self-Healing 시스템에는 다양한 **캐시**, **계층형 저장소**, **복제 상태**가 존재합니다. 이들 간의 불일치(Drift)를 감지하고 추적해야 운영 안정성을 보장할 수 있습니다.

### 1.2 구현 현황 (v1.1.0)

| Phase | 컴포넌트 | 상태 |
|-------|----------|------|
| Phase 1 | PoolCircuitBreaker, PrecomputedCache | ✅ 완료 |
| Phase 2 | EmergencyMode Cache, RateLimiter | ✅ 완료 |
| Phase 3 | Config lru_cache | ✅ 완료 |

### 1.3 Drift 필요 조건 (업계 표준)

| 조건 | 설명 | 예시 |
|------|------|------|
| **캐시된 상태** | 원본 ↔ 캐시 불일치 | Gauge ↔ DB, Config Cache |
| **계층형 저장소** | L1 ↔ L2 불일치 | Memory ↔ Redis/DB |
| **설정 vs 실제** | 기대값 ↔ 실측치 | SLA 설정 ↔ 실제 성능 |
| **분산 복제** | 노드간 상태 불일치 | Pod간 CB 상태 |

---

## 2. 현재 구현 상태 분석

### 2.1 Drift 추적 완료 (4개)

| 컴포넌트 | 파일 위치 | Drift 유형 | 구현 상태 |
|----------|----------|------------|----------|
| **LayeredRepository** | `adapters/memory/layered_repository.py` | L1↔L2 | ✅ `DriftReconciler` 연동 |
| **CircuitBreaker L1/L2** | `adapters/memory/circuit_breaker.py` | L1↔L2 | ✅ `DriftReconciliation` 연동 |
| **SLA Drift** | `tasks/drift_detection.py` | 설정↔실제 | ✅ `SLADriftDetector` |
| **Gauge Metrics** | `metrics/prometheus.py` | 캐시↔DB | ✅ `SafeGauge` + `Reconciler` |

### 2.2 Drift 추적 누락 (5개) → ✅ 모두 구현 완료

| 컴포넌트 | 파일 위치 | Drift 유형 | 현재 상태 | 구현 버전 |
|----------|----------|------------|----------|----------|
| **PrecomputedCache** | `services/precomputed_cache.py` | L1↔L2 캐시 | ✅ 완료 | v6.3.0 |
| **EmergencyMode Cache** | `services/emergency_mode/manager.py` | TTL 캐시 | ✅ 완료 | v6.3.0 |
| **PoolCircuitBreaker** | `api/django/pool_circuit_breaker.py` | 캐시 stale | ✅ 완료 | v6.2.2 |
| **RateLimiter** | `adapters/rate_limit/redis_adapter.py` | Redis↔실제 | ✅ 완료 | v6.3.0 |
| **Config lru_cache** | `config.py` | 설정 캐시 | ✅ 완료 | v6.3.0 |

---

## 3. 구현 상세

### 3.1 PrecomputedCache Drift 감지

**파일**: `services/precomputed_cache.py`

**현재 코드 (문제점)**:
```python
# L1: In-process TTLCache (2초 TTL)
# L2: Redis Pre-computed JSON (15초 TTL)
# TTL 차이로 인한 L1↔L2 불일치 발생 가능
# 하지만 drift 추적 메트릭 없음
```

**구현 계획**:
```python
# selfhealing/services/precomputed_cache.py

from prometheus_client import Counter, Gauge

# Drift 메트릭 정의
cache_drift_detected_total = Counter(
    "selfhealing_cache_drift_detected_total",
    "Total cache drift detections between L1 and L2",
    ["cache_key", "severity"],
)

cache_l1_l2_consistency = Gauge(
    "selfhealing_cache_l1_l2_consistency",
    "L1/L2 cache consistency ratio (1.0 = fully consistent)",
    ["cache_key"],
)

cache_hit_rate = Gauge(
    "selfhealing_cache_hit_rate",
    "Cache hit rate per layer",
    ["cache_key", "layer"],  # layer: l1, l2
)


class PrecomputedCacheService:
    def __init__(self):
        # 기존 코드...
        self._l1_hits = 0
        self._l2_hits = 0
        self._l3_hits = 0  # fallback
        self._drift_count = 0
    
    def get_with_drift_check(self, cache_key: str) -> dict:
        """L1/L2 동시 조회 후 drift 감지."""
        l1_value = self._l1_cache.get(cache_key)
        l2_value = self._get_from_l2(cache_key)
        
        if l1_value is not None and l2_value is not None:
            if not self._values_match(l1_value, l2_value):
                self._drift_count += 1
                cache_drift_detected_total.labels(
                    cache_key=cache_key,
                    severity="warning",
                ).inc()
                logger.warning(
                    f"[PrecomputedCache] Drift detected for {cache_key}: "
                    f"L1 != L2"
                )
        
        # 히트율 메트릭 업데이트
        total = self._l1_hits + self._l2_hits + self._l3_hits
        if total > 0:
            cache_hit_rate.labels(cache_key=cache_key, layer="l1").set(
                self._l1_hits / total
            )
            cache_hit_rate.labels(cache_key=cache_key, layer="l2").set(
                self._l2_hits / total
            )
        
        return l1_value or l2_value
    
    def get_drift_stats(self) -> dict:
        """Drift 통계 반환."""
        return {
            "drift_count": self._drift_count,
            "l1_hits": self._l1_hits,
            "l2_hits": self._l2_hits,
            "l3_fallbacks": self._l3_hits,
        }
```

---

### 3.2 EmergencyMode Cache Drift 감지

**파일**: `services/emergency_mode/manager.py`

**현재 코드 (문제점)**:
```python
self._cache_ttl_seconds: int = 30  # 캐시 유효 시간
self._last_load_time: Optional[datetime] = None
# invalidate()만 있고, drift 감지/메트릭 없음
```

**구현 계획**:
```python
# selfhealing/services/emergency_mode/manager.py

from prometheus_client import Counter, Gauge

# Drift 메트릭
emergency_cache_stale_count = Counter(
    "selfhealing_emergency_cache_stale_total",
    "Number of times emergency mode cache became stale",
)

emergency_cache_drift_detected = Counter(
    "selfhealing_emergency_cache_drift_total",
    "Number of times cached state differed from backend",
)

emergency_cache_age_seconds = Gauge(
    "selfhealing_emergency_cache_age_seconds",
    "Current age of emergency mode cache in seconds",
)


class EmergencyModeManager:
    def _check_cache_drift(self) -> bool:
        """캐시와 백엔드 상태 비교 후 drift 감지."""
        if not self._is_cache_valid():
            emergency_cache_stale_count.inc()
            return True
        
        # 캐시와 실제 백엔드 비교
        cached_state = self._state
        backend_state = self._load_state_from_backend()
        
        if cached_state.level != backend_state.level:
            emergency_cache_drift_detected.inc()
            logger.warning(
                f"[EmergencyMode] Drift detected: "
                f"cached={cached_state.level}, backend={backend_state.level}"
            )
            # 자동 동기화
            self._state = backend_state
            return True
        
        return False
    
    def get_state(self) -> EmergencyState:
        """상태 조회 (drift 체크 포함)."""
        # 캐시 age 메트릭 업데이트
        if self._last_load_time:
            age = (datetime.now(timezone.utc) - self._last_load_time).total_seconds()
            emergency_cache_age_seconds.set(age)
        
        # 기존 로직...
        self._check_cache_drift()
        return self._state
```

---

### 3.3 PoolCircuitBreaker Stale Cache 메트릭

**파일**: `api/django/pool_circuit_breaker.py`

**현재 코드 (문제점)**:
```python
# _cached_pool_status (100ms 주기)
# _is_stale 플래그만 있고 Prometheus 메트릭 없음
# stale_cache_fallbacks, stale_cache_warnings 통계는 내부에만 존재
```

**구현 계획**:
```python
# selfhealing/api/django/pool_circuit_breaker.py

from prometheus_client import Counter, Gauge, Histogram

# Stale Cache 메트릭 (Prometheus 노출)
pool_cb_cache_stale_total = Counter(
    "selfhealing_pool_cb_cache_stale_total",
    "Total stale cache events in PoolCircuitBreaker",
    ["severity"],  # warning, critical
)

pool_cb_cache_age_ms = Histogram(
    "selfhealing_pool_cb_cache_age_ms",
    "Age of PoolCircuitBreaker cache when accessed (ms)",
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000],
)

pool_cb_cache_hit_rate = Gauge(
    "selfhealing_pool_cb_cache_hit_rate",
    "PoolCircuitBreaker cache hit rate",
)


class PoolCircuitBreaker:
    def get_cached_pool_status(self) -> dict:
        """캐시된 상태 조회 (Prometheus 메트릭 포함)."""
        with self._cache_lock:
            if self._cached_pool_status is None:
                return self._get_safe_fallback()
            
            # 캐시 age 계산 및 히스토그램 기록
            cache_time = self._cached_pool_status.get("_cache_time", 0)
            age_ms = (time.time() - cache_time) * 1000
            pool_cb_cache_age_ms.observe(age_ms)
            
            # Stale 체크 및 메트릭 기록
            if age_ms > self._critical_stale_ms:
                pool_cb_cache_stale_total.labels(severity="critical").inc()
                self._stats["stale_cache_fallbacks"] += 1
                return self._get_safe_fallback()
            elif age_ms > self._warning_stale_ms:
                pool_cb_cache_stale_total.labels(severity="warning").inc()
                self._stats["stale_cache_warnings"] += 1
            
            # 히트율 업데이트
            total_accesses = self._stats.get("cache_hits", 0) + self._stats.get("cache_misses", 0)
            if total_accesses > 0:
                hit_rate = self._stats.get("cache_hits", 0) / total_accesses
                pool_cb_cache_hit_rate.set(hit_rate)
            
            self._stats["cache_hits"] = self._stats.get("cache_hits", 0) + 1
            return self._cached_pool_status
```

---

### 3.4 RateLimiter Redis Drift 감지

**파일**: `adapters/rate_limit/redis_adapter.py`

**현재 코드 (문제점)**:
```python
# Redis에서 상태 조회/저장
# Redis 장애 시 fallback 로직 있음
# 하지만 drift 메트릭 없음
```

**구현 계획**:
```python
# selfhealing/adapters/rate_limit/redis_adapter.py

from prometheus_client import Counter, Gauge

# Drift 메트릭
ratelimit_redis_unavailable_total = Counter(
    "selfhealing_ratelimit_redis_unavailable_total",
    "Number of times Redis was unavailable for rate limiting",
)

ratelimit_state_drift_total = Counter(
    "selfhealing_ratelimit_state_drift_total",
    "Number of rate limit state drifts detected",
    ["key"],
)

ratelimit_fallback_active = Gauge(
    "selfhealing_ratelimit_fallback_active",
    "Whether rate limiter is in fallback mode (1=yes, 0=no)",
)


class RedisRateLimitStorage:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._available: Optional[bool] = None
        self._fallback_mode = False
        self._local_state: Dict[str, RateLimitState] = {}  # 폴백용 로컬 상태
    
    def is_available(self) -> bool:
        """Check if Redis is available."""
        try:
            self._redis.ping()
            if self._fallback_mode:
                # 복구됨 - drift 체크 필요
                self._reconcile_after_recovery()
            self._fallback_mode = False
            ratelimit_fallback_active.set(0)
            self._available = True
            return True
        except Exception as e:
            if not self._fallback_mode:
                ratelimit_redis_unavailable_total.inc()
                logger.warning(f"[RedisRateLimitStorage] Redis unavailable: {e}")
            self._fallback_mode = True
            ratelimit_fallback_active.set(1)
            self._available = False
            return False
    
    def _reconcile_after_recovery(self) -> None:
        """Redis 복구 후 로컬 상태와 동기화."""
        for key, local_state in self._local_state.items():
            try:
                redis_state = self._get_state_from_redis(key)
                if redis_state != local_state:
                    ratelimit_state_drift_total.labels(key=key).inc()
                    logger.info(
                        f"[RedisRateLimitStorage] Drift detected for {key}, "
                        f"syncing local → Redis"
                    )
                    # 더 보수적인 값 선택 (안전 우선)
                    merged = self._merge_conservative(local_state, redis_state)
                    self._save_to_redis(key, merged)
            except Exception as e:
                logger.warning(f"[RedisRateLimitStorage] Reconciliation failed: {e}")
        
        self._local_state.clear()
```

---

### 3.5 Config lru_cache 변경 감지

**파일**: `config.py`

**현재 코드 (문제점)**:
```python
@lru_cache(maxsize=1)
def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    # 환경변수에서 로드
    # 환경변수 변경 시 캐시 무효화 메커니즘 없음
```

**구현 계획**:
```python
# selfhealing/config.py

import hashlib
from prometheus_client import Counter, Info

# Config Drift 메트릭
config_env_changed_total = Counter(
    "selfhealing_config_env_changed_total",
    "Number of environment variable changes detected",
    ["config_type"],
)

config_cache_invalidated_total = Counter(
    "selfhealing_config_cache_invalidated_total",
    "Number of config cache invalidations",
    ["config_type"],
)


class ConfigDriftMonitor:
    """환경변수 변경 감지 및 캐시 무효화."""
    
    def __init__(self):
        self._env_hashes: Dict[str, str] = {}
    
    def _compute_env_hash(self, prefix: str) -> str:
        """해당 prefix로 시작하는 환경변수들의 해시 계산."""
        import os
        relevant_vars = {
            k: v for k, v in os.environ.items() 
            if k.startswith(prefix)
        }
        content = str(sorted(relevant_vars.items()))
        return hashlib.md5(content.encode()).hexdigest()
    
    def check_and_invalidate(self, config_type: str, prefix: str) -> bool:
        """환경변수 변경 확인 후 필요시 캐시 무효화."""
        current_hash = self._compute_env_hash(prefix)
        previous_hash = self._env_hashes.get(config_type)
        
        if previous_hash and current_hash != previous_hash:
            config_env_changed_total.labels(config_type=config_type).inc()
            logger.info(
                f"[ConfigDriftMonitor] Environment change detected for {config_type}"
            )
            self._invalidate_cache(config_type)
            return True
        
        self._env_hashes[config_type] = current_hash
        return False
    
    def _invalidate_cache(self, config_type: str) -> None:
        """해당 config 타입의 lru_cache 무효화."""
        config_cache_invalidated_total.labels(config_type=config_type).inc()
        
        cache_map = {
            "circuit_breaker": get_circuit_breaker_settings,
            "metric_collection": get_metric_collection_settings,
            # 추가 config 함수들...
        }
        
        func = cache_map.get(config_type)
        if func and hasattr(func, "cache_clear"):
            func.cache_clear()
            logger.info(f"[ConfigDriftMonitor] Cache cleared for {config_type}")


# 싱글톤 인스턴스
_config_drift_monitor: Optional[ConfigDriftMonitor] = None

def get_config_drift_monitor() -> ConfigDriftMonitor:
    global _config_drift_monitor
    if _config_drift_monitor is None:
        _config_drift_monitor = ConfigDriftMonitor()
    return _config_drift_monitor


# 사용 예시: 설정 조회 전 drift 체크
def get_circuit_breaker_settings_safe() -> CircuitBreakerSettings:
    """환경변수 변경 감지 후 설정 반환."""
    monitor = get_config_drift_monitor()
    monitor.check_and_invalidate("circuit_breaker", "SELFHEALING_CB_")
    return get_circuit_breaker_settings()
```

---

## 4. 신규 Prometheus 메트릭 요약

### 4.1 추가될 메트릭 목록

| 메트릭 이름 | 타입 | 레이블 | 설명 |
|------------|------|--------|------|
| `selfhealing_cache_drift_detected_total` | Counter | cache_key, severity | L1/L2 캐시 drift 감지 횟수 |
| `selfhealing_cache_l1_l2_consistency` | Gauge | cache_key | L1/L2 일관성 비율 (0~1) |
| `selfhealing_cache_hit_rate` | Gauge | cache_key, layer | 캐시 히트율 |
| `selfhealing_emergency_cache_stale_total` | Counter | - | Emergency 캐시 stale 횟수 |
| `selfhealing_emergency_cache_drift_total` | Counter | - | Emergency 상태 drift 횟수 |
| `selfhealing_emergency_cache_age_seconds` | Gauge | - | Emergency 캐시 age |
| `selfhealing_pool_cb_cache_stale_total` | Counter | severity | Pool CB stale 횟수 |
| `selfhealing_pool_cb_cache_age_ms` | Histogram | - | Pool CB 캐시 age 분포 |
| `selfhealing_pool_cb_cache_hit_rate` | Gauge | - | Pool CB 캐시 히트율 |
| `selfhealing_ratelimit_redis_unavailable_total` | Counter | - | Redis 불가용 횟수 |
| `selfhealing_ratelimit_state_drift_total` | Counter | key | Rate limit drift 횟수 |
| `selfhealing_ratelimit_fallback_active` | Gauge | - | Fallback 모드 여부 |
| `selfhealing_config_env_changed_total` | Counter | config_type | 환경변수 변경 감지 횟수 |
| `selfhealing_config_cache_invalidated_total` | Counter | config_type | 캐시 무효화 횟수 |

---

## 5. 구현 순서 및 우선순위

### 5.1 Phase 1: 높은 우선순위 ✅ 완료

| 순서 | 컴포넌트 | 상태 | 구현 내용 |
|------|----------|------|----------|
| 1 | **PoolCircuitBreaker** | ✅ 완료 | Prometheus 메트릭 연동 (stale, age, hit_rate) |
| 2 | **PrecomputedCache** | ✅ 완료 | L1/L2 drift 감지, hit rate 추적 |

### 5.2 Phase 2: 중간 우선순위 ✅ 완료

| 순서 | 컴포넌트 | 상태 | 구현 내용 |
|------|----------|------|----------|
| 3 | **EmergencyMode Cache** | ✅ 완료 | 캐시 age 추적, drift 감지, 로드 메트릭 |
| 4 | **RateLimiter** | ✅ 완료 | Fallback 모드 추적, Redis 복구 시 reconciliation |

### 5.3 Phase 3: 낮은 우선순위 ✅ 완료

| 순서 | 컴포넌트 | 상태 | 구현 내용 |
|------|----------|------|----------|
| 5 | **Config lru_cache** | ✅ 완료 | ConfigDriftMonitor, 환경변수 변경 감지 |

---

## 6. 테스트 계획

### 6.1 단위 테스트

```python
# tests/unit/drift/test_precomputed_cache_drift.py

class TestPrecomputedCacheDrift:
    def test_detects_l1_l2_mismatch(self):
        """L1과 L2 값이 다르면 drift 감지."""
        cache = PrecomputedCacheService()
        cache._l1_cache.set("health", {"status": "ok"})
        cache._l2_set("health", {"status": "degraded"})
        
        cache.get_with_drift_check("health")
        
        assert cache._drift_count == 1

    def test_no_drift_when_consistent(self):
        """L1과 L2 값이 같으면 drift 없음."""
        cache = PrecomputedCacheService()
        data = {"status": "ok"}
        cache._l1_cache.set("health", data)
        cache._l2_set("health", data)
        
        cache.get_with_drift_check("health")
        
        assert cache._drift_count == 0


# tests/unit/drift/test_pool_cb_stale.py

class TestPoolCBStaleMetrics:
    def test_stale_cache_increments_prometheus(self):
        """Stale 캐시 접근 시 Prometheus 메트릭 증가."""
        from prometheus_client import REGISTRY
        
        cb = PoolCircuitBreaker()
        cb._cached_pool_status = {
            "_cache_time": time.time() - 10,  # 10초 전
        }
        
        cb.get_cached_pool_status()
        
        # Prometheus 메트릭 확인
        metric = REGISTRY.get_sample_value(
            "selfhealing_pool_cb_cache_stale_total",
            {"severity": "critical"},
        )
        assert metric >= 1
```

### 6.2 통합 테스트

```python
# tests/integration/test_drift_detection_e2e.py

class TestDriftDetectionE2E:
    def test_full_drift_detection_cycle(self):
        """전체 drift 감지 사이클 테스트."""
        # 1. 정상 상태 확인
        # 2. L1/L2 불일치 유발
        # 3. Drift 감지 확인
        # 4. 자동 동기화 확인
        # 5. Prometheus 메트릭 확인
        pass
```

---

## 7. 추가 구현 필요 여부 검토

### 7.1 추가로 확인된 누락 컴포넌트

코드 분석 결과, 추가로 확인된 drift 가능 영역:

| 컴포넌트 | 파일 | Drift 유형 | 권장 |
|----------|------|------------|------|
| **WAL Sync** | `audit/wal.py` | WAL↔중앙저장소 | ⚠️ 이미 Reconciler 있음, 메트릭만 추가 |
| **ShadowLogger** | `adapters/memory/shadow_logger.py` | 동기화 기록 | ⚠️ 부분 구현, 메트릭 보강 필요 |
| **TTLCacheStrategy** | `adapters/cache/` | 캐시 TTL | 🟢 추가 권장 |

### 7.2 구현 권장 여부

모든 누락 컴포넌트에 drift 추적을 추가하는 것이 **괜찮습니다**. 이유:

1. **오버헤드 최소화**: Prometheus Counter/Gauge는 매우 가벼움 (ns 수준)
2. **관측 가능성 향상**: 문제 발생 시 root cause 분석 용이
3. **SRE 표준**: Google SRE 가이드에서 권장하는 패턴
4. **점진적 구현**: Phase별로 구현하여 리스크 최소화

---

## 8. 관련 문서

- [14_METRIC_COLLECTION_CORE.md](../14_METRIC_COLLECTION_CORE.md) - 메트릭 수집 전략
- [15_METRIC_COLLECTION_ADVANCED.md](../15_METRIC_COLLECTION_ADVANCED.md) - Drift 감지 기본
- [13_LAYERED_STORAGE_RESILIENCE.md](../13_LAYERED_STORAGE_RESILIENCE.md) - 계층형 저장소
- [08_OBSERVABILITY.md](../08_OBSERVABILITY.md) - 관측 가능성 설계

---

## 9. 체크리스트

### 9.1 구현 전 확인 ✅

- [x] Prometheus 메트릭 네이밍 컨벤션 준수
- [x] 기존 메트릭과 중복 없음 확인
- [x] 단위 테스트 작성
- [x] 문서 업데이트

### 9.2 구현 후 확인 ✅

- [x] 모든 테스트 통과 (20개 drift 테스트, 278개 전체)
- [ ] Grafana 대시보드에 새 메트릭 추가 (운영팀)
- [ ] 알림 규칙 설정 (drift 임계값 초과 시) (운영팀)
- [ ] 운영 가이드 업데이트 (운영팀)
- [ ] 성능 영향 측정 (운영팀)

---

## 10. 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|----------|
| 1.0.0 | 2026-01-15 | AI Assistant | 최초 작성 |
| 1.1.0 | 2026-01-15 | AI Assistant | Phase 1,2,3 전체 구현 완료, 테스트 통과 |

---

## 11. 구현 파일 목록

### 11.1 신규 생성 파일

| 파일 경로 | 설명 |
|----------|------|
| `selfhealing/metrics/drift_metrics.py` | Drift Detection Prometheus 메트릭 정의 |
| `tests/unit/drift/__init__.py` | 테스트 패키지 |
| `tests/unit/drift/test_drift_detection.py` | Drift Detection 단위 테스트 (20개) |

### 11.2 수정된 파일

| 파일 경로 | 변경 내용 |
|----------|----------|
| `api/django/pool_circuit_breaker.py` | Stale cache Prometheus 메트릭 연동 |
| `services/precomputed_cache.py` | L1/L2 drift 감지 및 hit rate 추적 |
| `services/emergency_mode/manager.py` | 캐시 drift 감지 및 age 추적 |
| `adapters/rate_limit/redis_adapter.py` | Fallback 모드 및 reconciliation 추적 |
| `config.py` | ConfigDriftMonitor 클래스 추가 |

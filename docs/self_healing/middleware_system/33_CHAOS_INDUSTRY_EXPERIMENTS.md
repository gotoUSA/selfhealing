# 33. 업계 표준 Chaos 실험 추가 계획

> **작성일**: 2026-01-09  
> **상태**: Phase 1-5 구현 완료  
> **관련 문서**: [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md), [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md)

---

## 1. 개요

### 1.1 현재 상태 vs 업계 표준

| 카테고리 | 업계 표준 실험 | 현재 구현 | Gap |
|----------|---------------|-----------|-----|
| **네트워크** | Latency, Packet Loss, DNS Failure, Blackhole, Bandwidth | ✅ 3/5 | 2개 |
| **인프라** | Container Kill, Pod Restart, AZ Failover | ❌ 0/3 | 3개 (범위 외) |
| **리소스** | CPU, Memory, Disk I/O, Process Kill, FD Exhaustion | ✅ 2/5 | 3개 |
| **상태** | State Injection, Clock Skew, Dependency Failure | ✅ 1/3 | 2개 |
| **보안** | Certificate Expiry, TLS Failure, Auth Failure | ✅ 1/3 | 2개 |

### 1.2 구현 대상 선정 기준

1. **Self-Healing 시스템과 연동 가능성**
2. **애플리케이션 레벨에서 시뮬레이션 가능**
3. **기존 코드 자산 활용 가능**

---

## 2. 네트워크 카테고리 확장

### 2.1 DNSFailureExperiment (신규)

#### 목적
DNS 장애 시 캐시/폴백 동작 및 Circuit Breaker 동작 검증

#### 시스템 연동 가능성
- **ConnectionHealthMonitor**: DNS 조회 실패 감지
  - 코드 위치: `core/connection_health.py:85-113`
- **Circuit Breaker**: 외부 API 연결 실패 감지
  - 코드 위치: `services/circuit_breaker/service.py:100-150`

#### Resilience Expectation 예시

```python
# 기대: ConnectionHealthMonitor가 10초 이내에 UNHEALTHY 보고
config = ExperimentConfig(
    target_service="external-payment-api",
    resilience_expectation=ResilienceExpectation(
        assertions=[
            ResilienceAssertion(
                expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                target_service="external-payment-api",
                expected_within_seconds=10.0,
                description="ConnectionHealthMonitor should report UNHEALTHY within 10s",
            ),
        ],
    ),
)
```

> **Assertion Scoring**: 실험 완료 후 `ResilienceValidator`가 자동으로 채점
> - Reference: `services/chaos/resilience_validator.py:211-320`

#### 구현 명세

```python
class DNSFailureExperiment(ChaosExperiment):
    """
    Simulate DNS resolution failures.
    
    Tests fallback to cached DNS, alternative DNS servers.
    
    Config parameters:
        - failure_mode: "timeout", "nxdomain", "servfail"
        - affected_domains: List of domains to affect
        - cache_fallback: Whether to allow cached DNS (default: True)
    """
    
    experiment_type = "dns_failure"
    requires_approval = True  # Network-wide impact
    
    @property
    def failure_mode(self) -> str:
        return self.config.parameters.get("failure_mode", "timeout")
    
    @property
    def affected_domains(self) -> list:
        return self.config.parameters.get("affected_domains", [])
    
    def inject_chaos(self) -> bool:
        """Inject DNS resolution failures."""
        logger.info(
            f"[DNSFailure] Injecting DNS {self.failure_mode} for "
            f"{len(self.affected_domains)} domains (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # DNS 실패는 네트워크 레벨 설정 필요
            # 애플리케이션 레벨에서는 ConnectionHealthMonitor와 연동
            _apply_chaos_config({
                "dns_failure": {
                    "enabled": True,
                    "failure_mode": self.failure_mode,
                    "affected_domains": self.affected_domains,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[DNSFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[DNSFailure] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "dns_failure": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

#### 애플리케이션 레벨 시뮬레이션

DNS 장애를 직접 주입하기 어려우므로, **ConnectionHealthMonitor**를 통해 시뮬레이션:

```python
# core/connection_health.py 확장
class ChaosAwareConnectionHealthMonitor(DefaultConnectionHealthMonitor):
    """카오스 실험 인식 연결 모니터."""
    
    def check_health(self, connection_type: ConnectionType, name: str) -> ConnectionHealth:
        # 카오스 설정 확인
        chaos_config = _get_current_chaos_config()
        dns_failure = chaos_config.get("dns_failure", {})
        
        if dns_failure.get("enabled") and self._is_affected(name, dns_failure):
            # DNS 장애 시뮬레이션
            return ConnectionHealth(
                connection_type=connection_type,
                name=name,
                status=ConnectionStatus.UNHEALTHY,
                error_message=f"DNS {dns_failure.get('failure_mode')} simulated",
                consecutive_failures=5,
            )
        
        # 정상 체크
        return super().check_health(connection_type, name)
```

---

### 2.2 NetworkBlackholeExperiment (신규)

#### 목적
특정 엔드포인트/서비스로의 트래픽 완전 차단 (응답 없음)

#### 시스템 연동 가능성
- **Circuit Breaker**: 무응답 감지 → OPEN
- **Timeout Experiment**와 유사하지만 응답 자체가 없음

#### 구현 명세

```python
class NetworkBlackholeExperiment(ChaosExperiment):
    """
    Simulate network blackhole (traffic absorbed, no response).
    
    Differs from timeout - no error response, just silence.
    
    Config parameters:
        - affected_endpoints: List of endpoints to blackhole
        - duration_seconds: How long to maintain blackhole
    """
    
    experiment_type = "network_blackhole"
    requires_approval = True  # High risk
    
    @property
    def affected_endpoints(self) -> list:
        return self.config.parameters.get("affected_endpoints", [])
    
    def inject_chaos(self) -> bool:
        """Create network blackhole for endpoints."""
        logger.warning(
            f"[NetworkBlackhole] Blackholing {len(self.affected_endpoints)} endpoints "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "network_blackhole": {
                    "enabled": True,
                    "affected_endpoints": self.affected_endpoints,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[NetworkBlackhole] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[NetworkBlackhole] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "network_blackhole": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

## 3. 리소스 카테고리 확장

### 3.1 SimulatedDiskIOExperiment (신규)

> **네이밍 변경**: `DiskIOExperiment` → `SimulatedDiskIOExperiment`
> - 실제 OS 커널/디스크 헤드에 부하를 주는 것이 아닌 **애플리케이션 레벨 시뮬레이션**임을 명시
> - Storage Adapter에서 `time.sleep()` 방식으로 지연 주입

#### 목적
디스크 I/O 지연/실패 시뮬레이션으로 DB/로그 복원력 검증

#### 시스템 연동 가능성
- **DLQ Service**: 디스크 쓰기 실패 시 메모리 폴백
  - 코드 위치: `services/dlq/base.py`
- **Audit Resilience**: L1(Memory) → L2(Disk) 폴백 검증
  - 코드 위치: `audit/resilience.py:500-600`

#### Blast Radius 하드캡

```python
# 운영 환경 통제 불능 방지를 위한 상수 정의
MAX_IO_LATENCY_MS: int = 2000      # 최대 2초 지연
MAX_FAILURE_RATE: float = 0.30     # 최대 30% 실패율
```

#### Resilience Expectation 예시

```python
# 기대: DLQ가 디스크 장애 시 메모리 폴백 활성화
config = ExperimentConfig(
    target_service="dlq-storage",
    resilience_expectation=ResilienceExpectation(
        assertions=[
            ResilienceAssertion(
                expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                target_service="dlq-storage",
                expected_within_seconds=5.0,
                description="DLQ should fallback to memory within 5s",
            ),
        ],
    ),
)
```

#### 구현 명세

```python
class SimulatedDiskIOExperiment(ChaosExperiment):
    """
    Simulate disk I/O latency or failures at application level.
    
    NOTE: This is APPLICATION-LEVEL simulation using time.sleep(),
    NOT actual OS/kernel level disk stress (like fio).
    
    Tests fallback to memory storage, log buffering.
    
    Config parameters:
        - io_latency_ms: Latency to add to I/O operations (max: 2000ms)
        - failure_rate: Percentage of I/O ops to fail (max: 30%)
        - affected_paths: List of paths to affect (optional)
    """
    
    experiment_type = "simulated_disk_io"
    requires_approval = True  # Data loss risk
    
    # === Blast Radius 하드캡 ===
    MAX_IO_LATENCY_MS: int = 2000
    MAX_FAILURE_RATE: float = 0.30
    
    @property
    def io_latency_ms(self) -> int:
        raw = self.config.parameters.get("io_latency_ms", 500)
        return min(raw, self.MAX_IO_LATENCY_MS)  # 하드캡 적용
    
    @property
    def failure_rate(self) -> float:
        raw = self.config.parameters.get("failure_rate", 0.10)
        return min(raw, self.MAX_FAILURE_RATE)  # 하드캡 적용
    
    def inject_chaos(self) -> bool:
        """Inject disk I/O chaos."""
        logger.warning(
            f"[DiskIO] Injecting {self.io_latency_ms}ms latency + "
            f"{self.failure_rate*100}% failures (TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "disk_io": {
                    "enabled": True,
                    "io_latency_ms": self.io_latency_ms,
                    "failure_rate": self.failure_rate,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[DiskIO] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[DiskIO] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "disk_io": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

### 3.2 PoolExhaustionExperiment (신규)

> **네이밍 변경**: `ConnectionPoolExhaustionExperiment` → `PoolExhaustionExperiment` (간결화)

#### 목적
DB 커넥션 풀 고갈 시뮬레이션으로 풀 모니터링 및 폴백 검증

#### 시스템 연동 가능성
- **ConnectionPoolMonitor**: 풀 고갈 감지
  - 코드 위치: `core/pool_monitor.py:83-100`
- **HealthCheckService**: 풀 상태 확인
  - 코드 위치: `services/health_check.py:30-50`

#### 구현 명세

#### Resilience Expectation 예시

```python
# 기대: Pool 고갈 시 PoolMonitor가 EXHAUSTED 감지
config = ExperimentConfig(
    target_service="db-pool",
    resilience_expectation=ResilienceExpectation.expect_cb_open(
        target_service="db-pool",
        within_seconds=10.0,
    ),
)
```

#### 구현 명세

```python
class PoolExhaustionExperiment(ChaosExperiment):
    """
    Simulate connection pool exhaustion.
    
    Uses PoolMonitor.set_simulation_override() - no actual connections held.
    Reference: core/pool_monitor.py:131-160
    
    Tests pool monitoring, wait queue handling, graceful degradation.
    
    Config parameters:
        - pool_name: Name of pool to exhaust
        - hold_connections: Number of connections to simulate holding
        - hold_duration_seconds: How long to hold
    """
    
    experiment_type = "pool_exhaustion"
    requires_approval = True  # Service disruption
    
    @property
    def pool_name(self) -> str:
        return self.config.parameters.get("pool_name", "default")
    
    @property
    def hold_connections(self) -> int:
        return self.config.parameters.get("hold_connections", 10)
    
    def inject_chaos(self) -> bool:
        """Exhaust connection pool."""
        logger.warning(
            f"[PoolExhaustion] Holding {self.hold_connections} connections "
            f"on pool '{self.pool_name}' (TTL: {self._effective_ttl}s)"
        )
        
        try:
            # PoolMonitor 시뮬레이션 오버라이드 사용
            # Reference: core/pool_monitor.py:131-160
            from selfhealing.core.pool_monitor import (
                ConnectionPoolMonitor,
                PoolHealthStatus,
            )
            
            monitor = ConnectionPoolMonitor()
            monitor.set_simulation_override(
                health_status=PoolHealthStatus.EXHAUSTED,
                experiment_id=self.experiment_id,
            )
            
            _apply_chaos_config({
                "pool_exhaustion": {
                    "enabled": True,
                    "pool_name": self.pool_name,
                    "hold_connections": self.hold_connections,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[PoolExhaustion] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[PoolExhaustion] Rolling back {self.experiment_id}")
            
            # 시뮬레이션 오버라이드 해제
            try:
                from selfhealing.core.pool_monitor import ConnectionPoolMonitor
                monitor = ConnectionPoolMonitor()
                monitor.clear_simulation_override()
            except Exception as e:
                logger.warning(f"[PoolExhaustion] Failed to clear override: {e}")
            
            _apply_chaos_config({
                "pool_exhaustion": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

#### PoolMonitor 연동

#### PoolMonitor 연동 (이미 구현됨)

`core/pool_monitor.py:131-160`에 `set_simulation_override()` 메서드가 이미 구현되어 있습니다:

```python
# core/pool_monitor.py (기존 구현)
class ConnectionPoolMonitor:
    def set_simulation_override(
        self,
        health_status: Optional[PoolHealthStatus] = None,
        stats: Optional[PoolStats] = None,
        experiment_id: Optional[str] = None,
    ) -> None:
        """
        시뮬레이션 상태 오버라이드 설정.
        
        실제 인프라를 변경하지 않고 모니터가 특정 상태를 보고하도록 강제.
        카오스 실험에서 알림/복구 체인 검증에 사용.
        
        Reference: 32_CHAOS_SYSTEM_INTEGRATION.md §16.2.1, §22.2.2
        """
        with self._lock:
            self._simulation_override = health_status
            self._simulation_stats = stats
            self._simulation_experiment_id = experiment_id
```

---

## 4. 보안 카테고리 추가

### 4.1 CertificateExpiryExperiment (신규)

#### 목적
인증서 만료 시나리오 시뮬레이션으로 모니터링 및 알림 검증

#### 시스템 연동 가능성
- **CertificateExpiryMonitor**: 인증서 만료 감지
  - 코드 위치: `core/cert_monitor.py:59-100`

#### 구현 명세

```python
class CertificateExpiryExperiment(ChaosExperiment):
    """
    Simulate certificate expiry scenarios.
    
    Tests certificate monitoring, alert generation, auto-renewal triggers.
    
    Config parameters:
        - simulated_days_remaining: Days until simulated expiry
        - affected_endpoints: Endpoints with expiring certs
    """
    
    experiment_type = "certificate_expiry"
    requires_approval = False  # Read-only simulation
    
    @property
    def simulated_days_remaining(self) -> int:
        return self.config.parameters.get("simulated_days_remaining", 5)
    
    @property
    def affected_endpoints(self) -> list:
        return self.config.parameters.get("affected_endpoints", [])
    
    def inject_chaos(self) -> bool:
        """Simulate certificate expiry."""
        logger.info(
            f"[CertExpiry] Simulating {self.simulated_days_remaining} days until expiry "
            f"for {len(self.affected_endpoints)} endpoints"
        )
        
        try:
            _apply_chaos_config({
                "certificate_expiry": {
                    "enabled": True,
                    "simulated_days_remaining": self.simulated_days_remaining,
                    "affected_endpoints": self.affected_endpoints,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
            # 인증서 모니터 알림 트리거
            from selfhealing.core.cert_monitor import CertificateExpiryMonitor
            
            monitor = CertificateExpiryMonitor()
            for endpoint in self.affected_endpoints:
                # 시뮬레이션된 만료일로 체크
                from datetime import datetime, timezone, timedelta
                simulated_not_after = datetime.now(timezone.utc) + timedelta(
                    days=self.simulated_days_remaining
                )
                monitor.check_expiry(
                    not_after=simulated_not_after,
                    endpoint=endpoint,
                    subject=f"chaos_test_{endpoint}",
                )
            
            return True
        except Exception as e:
            logger.error(f"[CertExpiry] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[CertExpiry] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "certificate_expiry": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

### 4.2 SimulatedTLSFailureExperiment (신규)

> **네이밍 변경**: `TLSFailureExperiment` → `SimulatedTLSFailureExperiment`
> - 실제 SSL 핸드셰이크 조작이 아닌 **HTTP Client 레벨 응답 시뮬레이션**
> - `requests.exceptions.SSLError`를 의도적으로 발생시키는 방식

#### 목적
TLS 핸드셰이크 실패 시뮬레이션으로 보안 연결 복원력 검증

#### 구현 명세

```python
class SimulatedTLSFailureExperiment(ChaosExperiment):
    """
    Simulate TLS handshake failures at application level.
    
    NOTE: This is APPLICATION-LEVEL simulation.
    Does NOT manipulate actual SSL/TLS connections.
    Intercepts HTTP client calls and raises SSLError.
    
    Tests TLS error handling, fallback behaviors, security logging.
    
    Config parameters:
        - failure_type: "handshake_timeout", "cert_invalid", "protocol_mismatch"
        - affected_endpoints: Endpoints to affect
    """
    
    experiment_type = "simulated_tls_failure"
    requires_approval = True  # Security sensitive
    
    @property
    def failure_type(self) -> str:
        return self.config.parameters.get("failure_type", "handshake_timeout")
    
    def inject_chaos(self) -> bool:
        """Inject TLS failures."""
        logger.warning(
            f"[TLSFailure] Injecting TLS {self.failure_type} "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "tls_failure": {
                    "enabled": True,
                    "failure_type": self.failure_type,
                    "affected_endpoints": self.config.parameters.get("affected_endpoints", []),
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[TLSFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[TLSFailure] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "tls_failure": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

## 5. 상태 카테고리 추가

### 5.1 ClockSkewExperiment (신규)

#### 목적
시스템 시간 불일치 시뮬레이션으로 시간 의존 로직 검증

#### 시스템 연동 가능성
- **IdempotencyService**: 시간 기반 중복 체크
- **TTL 기반 만료**: 카오스 실험 TTL 자체도 영향 받음

#### ⚠️ Recursive Failure 방지: Monotonic Clock 보호

**문제**: 시스템 시간을 미래로 돌리면 카오스 엔진 자신의 TTL 타이머까지 영향받아 실험이 영원히 끝나지 않는 루프에 빠질 수 있음.

**해결**: 롤백을 담당하는 타이머는 **Monotonic Clock** 사용 (시스템 시간 변화에 영향받지 않는 시계)

```python
import time

class ClockSkewExperiment(ChaosExperiment):
    """
    Simulate system clock skew/drift.
    
    SAFETY: Uses time.monotonic() for TTL to prevent recursive failure.
    Even if system time is skewed 100 years into the future,
    the experiment engine knows "60 real-world seconds" have passed.
    
    Tests time-sensitive logic, token expiry, TTL handling.
    
    Config parameters:
        - skew_seconds: Seconds to skew clock (positive = future)
        - drift_rate: Continuous drift rate per second
    """
    
    experiment_type = "clock_skew"
    requires_approval = True  # Subtle, hard to debug issues
    
    # === Monotonic TTL 보호 ===
    _monotonic_start: float = 0.0
    
    @property
    def skew_seconds(self) -> int:
        return self.config.parameters.get("skew_seconds", 300)  # 5 minutes
    
    def _start_monotonic_timer(self) -> None:
        """
        Monotonic clock 기반 TTL 타이머 시작.
        
        time.monotonic()는 시스템 시간 변경에 영향받지 않음.
        Reference: metrics/decorators.py:86-88 (기존 사용 패턴)
        """
        self._monotonic_start = time.monotonic()
        logger.info(
            f"[ClockSkew] Monotonic timer started at {self._monotonic_start:.2f}"
        )
    
    def is_expired_monotonic(self) -> bool:
        """
        Monotonic clock 기반 TTL 만료 확인.
        
        시스템 시간(timezone.now())이 아닌 실제 경과 시간으로 판단.
        Clock Skew 실험 중에도 안전하게 롤백 가능.
        
        Returns:
            True if TTL expired based on monotonic clock
        """
        if self._monotonic_start == 0.0:
            return False
        elapsed = time.monotonic() - self._monotonic_start
        return elapsed >= self._effective_ttl
    
    def inject_chaos(self) -> bool:
        """Inject clock skew with monotonic TTL protection."""
        logger.warning(
            f"[ClockSkew] Skewing time by {self.skew_seconds}s "
            f"(TTL: {self._effective_ttl}s, protected by monotonic clock)"
        )
        
        # Monotonic 타이머 시작 (핵심!)
        self._start_monotonic_timer()
        
        try:
            _apply_chaos_config({
                "clock_skew": {
                    "enabled": True,
                    "skew_seconds": self.skew_seconds,
                    "experiment_id": self.experiment_id,
                    "monotonic_start": self._monotonic_start,
                    # expires_at는 참고용으로만 사용 (monotonic이 진짜 TTL)
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ClockSkew] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            elapsed = time.monotonic() - self._monotonic_start if self._monotonic_start else 0
            logger.info(
                f"[ClockSkew] Rolling back {self.experiment_id} "
                f"(elapsed monotonic: {elapsed:.2f}s)"
            )
            _apply_chaos_config({
                "clock_skew": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

#### 애플리케이션 레벨 시뮬레이션 (ContextVar 기반)

> **ContextVar 전파 무결성**: 이미 `_is_chaos_request`, `_current_actor`, `_celery_context_var` 등으로 프로젝트 전반에서 사용 중
> - Reference: `services/http_client.py:19`, `context/actor_context.py:48`, `audit/trace.py:214`

```python
# core/timezone.py 확장
from contextvars import ContextVar
from datetime import timedelta

# Clock Skew 시뮬레이션용 ContextVar (특정 요청에만 적용 가능)
_clock_skew_seconds: ContextVar[int] = ContextVar("clock_skew_seconds", default=0)

def now() -> datetime:
    """현재 시간 반환 (카오스 실험 시 skew 적용)."""
    current_time = datetime.now(timezone.utc)
    
    # ContextVar 기반 skew (특정 요청에만 적용)
    skew = _clock_skew_seconds.get()
    if skew != 0:
        current_time = current_time + timedelta(seconds=skew)
        return current_time
    
    # 전역 카오스 설정 확인 (폴백)
    try:
        chaos_config = _get_current_chaos_config()
        clock_skew = chaos_config.get("clock_skew", {})
        
        if clock_skew.get("enabled"):
            skew_seconds = clock_skew.get("skew_seconds", 0)
            current_time = current_time + timedelta(seconds=skew_seconds)
    except Exception:
        pass
    
    return current_time

def set_clock_skew_for_request(skew_seconds: int) -> None:
    """현재 요청에만 Clock Skew 적용 (스레드 안전)."""
    _clock_skew_seconds.set(skew_seconds)

def clear_clock_skew_for_request() -> None:
    """현재 요청의 Clock Skew 해제."""
    _clock_skew_seconds.set(0)
```

---

## 6. 시스템 고유 실험 타입

### 6.1 AuditStorageFailureExperiment (시스템 고유)

#### 목적
Audit 저장소 계층 장애 시뮬레이션 (L1 Memory → L2 Disk → L3 Central)

#### 시스템 연동 가능성
- **DegradedModeManager**: Audit 저장 모드 전환
  - 코드 위치: `audit/resilience.py:697-750`

#### 구현 명세

```python
class AuditStorageFailureExperiment(ChaosExperiment):
    """
    Simulate audit storage layer failures.
    
    Tests L1→L2→L3 failover, data recovery.
    
    Config parameters:
        - failed_layer: "l2", "l3", "all"
        - recovery_mode: "degraded", "memory_only", "queue"
    """
    
    experiment_type = "audit_storage_failure"
    requires_approval = True  # Audit data at risk
    
    @property
    def failed_layer(self) -> str:
        return self.config.parameters.get("failed_layer", "l2")
    
    def inject_chaos(self) -> bool:
        """Inject audit storage failure."""
        logger.warning(
            f"[AuditStorageFailure] Failing layer {self.failed_layer} "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            from selfhealing.audit.resilience import DegradedModeManager
            
            # Degraded Mode 활성화
            manager = DegradedModeManager()
            manager.enter_degraded_mode(
                reason=f"Chaos Experiment: {self.experiment_id}",
                failed_layer=self.failed_layer,
            )
            
            _apply_chaos_config({
                "audit_storage_failure": {
                    "enabled": True,
                    "failed_layer": self.failed_layer,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[AuditStorageFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[AuditStorageFailure] Rolling back {self.experiment_id}")
            
            try:
                from selfhealing.audit.resilience import DegradedModeManager
                
                manager = DegradedModeManager()
                manager.exit_degraded_mode()
            except Exception as e:
                logger.error(f"[AuditStorageFailure] Rollback failed: {e}")
            
            _apply_chaos_config({
                "audit_storage_failure": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

### 6.2 ReplayFloodExperiment (시스템 고유)

#### 목적
DLQ Replay 폭풍 시뮬레이션으로 Replay 시스템 복원력 검증

#### 시스템 연동 가능성
- **DLQ Service**: 대량 Replay 처리
- **Throttle**: Replay 속도 제한

#### Blast Radius 하드캡

```python
# 운영 환경 통제 불능 방지를 위한 상수 정의
MAX_ENTRIES_TO_CREATE: int = 5000   # 최대 5,000건
MAX_REPLAY_RATE: int = 500          # 초당 최대 500건
```

#### 가상 격리 (Virtual Isolation)

**실험 데이터가 실제 비즈니스 지표(SLA)를 절대 건드리지 않도록 보장:**

```python
# DLQ 엔트리 생성 시 필수 필드
CHAOS_DOMAIN_PREFIX = "chaos_test:"  # domain 필드 접두어
METADATA_SYNTHETIC_FLAG = {
    "is_synthetic": True,
    "is_chaos_experiment": True,
}
```

> **Reference**: `is_chaos_experiment` 패턴은 `chaos_context.py`에 이미 구현됨
> - `services/chaos_context.py:220-240` - `attach_chaos_context()` 함수
> - Error Budget 계산 시 자동 제외 (`metadata__is_chaos_experiment=True` 필터)

#### 구현 명세

```python
class ReplayFloodExperiment(ChaosExperiment):
    """
    Simulate DLQ replay flood.
    
    Tests replay throttling, prioritization, resource management.
    
    IMPORTANT: Uses Virtual Isolation to prevent data pollution:
    - domain prefix: "chaos_test:"
    - metadata.is_synthetic = True
    - metadata.is_chaos_experiment = True
    - Auto-cleanup on rollback via purge_archived()
    
    Config parameters:
        - entries_to_create: Number of DLQ entries (max: 5000)
        - replay_rate: Replays per second (max: 500)
        - domain: Target domain for entries
    """
    
    experiment_type = "replay_flood"
    requires_approval = True  # Resource intensive
    
    # === Blast Radius 하드캡 ===
    MAX_ENTRIES_TO_CREATE: int = 5000
    MAX_REPLAY_RATE: int = 500
    CHAOS_DOMAIN_PREFIX: str = "chaos_test:"
    
    @property
    def entries_to_create(self) -> int:
        raw = self.config.parameters.get("entries_to_create", 1000)
        return min(raw, self.MAX_ENTRIES_TO_CREATE)  # 하드캡 적용
    
    @property
    def replay_rate(self) -> int:
        raw = self.config.parameters.get("replay_rate", 100)
        return min(raw, self.MAX_REPLAY_RATE)  # 하드캡 적용
    
    @property
    def isolated_domain(self) -> str:
        """Return domain with chaos prefix for virtual isolation."""
        base_domain = self.config.parameters.get("domain", "chaos")
        return f"{self.CHAOS_DOMAIN_PREFIX}{base_domain}"
    
    def inject_chaos(self) -> bool:
        """Create DLQ entries with virtual isolation and trigger mass replay."""
        logger.warning(
            f"[ReplayFlood] Creating {self.entries_to_create} DLQ entries "
            f"(domain: {self.isolated_domain}) "
            f"and replaying at {self.replay_rate}/s (TTL: {self._effective_ttl}s)"
        )
        
        try:
            from selfhealing.services.dlq import get_dlq_service
            from selfhealing.services.chaos_context import create_chaos_context
            
            dlq = get_dlq_service()
            
            # 카오스 컨텍스트 생성 (가상 격리 플래그 포함)
            chaos_context = create_chaos_context(
                experiment_id=self.experiment_id,
                experiment_type=self.experiment_type,
            )
            
            # 가상 격리 메타데이터
            isolation_metadata = {
                **chaos_context.to_dict(),
                "is_synthetic": True,
                "is_chaos_experiment": True,
                "chaos_domain_prefix": self.CHAOS_DOMAIN_PREFIX,
            }
            
            for i in range(self.entries_to_create):
                dlq.store(
                    operation_type="chaos_test",
                    entity_type="test_entity",
                    entity_id=f"chaos_{self.experiment_id}_{i}",
                    domain=self.isolated_domain,  # 격리된 도메인
                    failure_type="simulated",
                    error_message="Chaos experiment entry",
                    metadata=isolation_metadata,
                )
            
            _apply_chaos_config({
                "replay_flood": {
                    "enabled": True,
                    "entries_created": self.entries_to_create,
                    "isolated_domain": self.isolated_domain,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ReplayFlood] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Cleanup chaos entries using virtual isolation filter."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[ReplayFlood] Rolling back {self.experiment_id}")
            
            # 생성된 DLQ 엔트리 정리 (is_chaos_experiment 필터 활용)
            try:
                from selfhealing.services.dlq import get_dlq_service
                
                dlq = get_dlq_service()
                repo = dlq.repository
                
                # chaos_test: 도메인 엔트리 자동 해결 및 purge
                # Reference: adapters/redis/dlq.py:933-959 (purge_archived)
                chaos_entries = repo.query(
                    domain=self.isolated_domain,
                    limit=self.entries_to_create + 100,
                )
                
                purged_count = 0
                for entry in chaos_entries:
                    if entry.metadata and entry.metadata.get("is_chaos_experiment"):
                        repo.delete(entry.id)
                        purged_count += 1
                
                logger.info(
                    f"[ReplayFlood] Purged {purged_count} chaos entries "
                    f"for experiment {self.experiment_id}"
                )
            except Exception as e:
                logger.error(f"[ReplayFlood] Cleanup failed: {e}")
            
            _apply_chaos_config({
                "replay_flood": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

---

## 7. 구현 우선순위

| 우선순위 | 실험 타입 | 이유 |
|----------|----------|------|
| 🔴 P1 | `ConnectionPoolExhaustionExperiment` | 기존 PoolMonitor 활용 가능 |
| 🔴 P1 | `CertificateExpiryExperiment` | 기존 CertMonitor 활용 가능 |
| 🟠 P2 | `DNSFailureExperiment` | ConnectionHealthMonitor 연동 |
| 🟠 P2 | `AuditStorageFailureExperiment` | DegradedModeManager 연동 |
| 🟠 P2 | `ClockSkewExperiment` | timezone.now() 확장 |
| 🟢 P3 | `NetworkBlackholeExperiment` | 네트워크 레벨 시뮬레이션 필요 |
| 🟢 P3 | `DiskIOExperiment` | OS 레벨 시뮬레이션 필요 |
| 🟢 P3 | `TLSFailureExperiment` | 보안 설정 변경 필요 |
| 🟢 P3 | `ReplayFloodExperiment` | DLQ 부하 테스트 |

---

## 8. ExperimentType Enum 확장 (전체)

```python
class ExperimentType(str, Enum):
    """Complete experiment types."""
    
    # === 기존 (5개) ===
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    PACKET_LOSS = "packet_loss"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    
    # === chaos_context.py 미구현 (6개) - 31_CHAOS_EXPERIMENT_EXPANSION ===
    ERROR_4XX = "error_4xx"
    CONNECTION_RESET = "connection_reset"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    PARTIAL_FAILURE = "partial_failure"
    CASCADING_FAILURE = "cascading_failure"
    
    # === 업계 표준 추가 (8개) - 33_CHAOS_INDUSTRY_EXPERIMENTS ===
    DNS_FAILURE = "dns_failure"
    NETWORK_BLACKHOLE = "network_blackhole"
    DISK_IO = "disk_io"
    CONNECTION_POOL_EXHAUSTION = "connection_pool_exhaustion"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    TLS_FAILURE = "tls_failure"
    CLOCK_SKEW = "clock_skew"
    
    # === 시스템 고유 (2개) ===
    AUDIT_STORAGE_FAILURE = "audit_storage_failure"
    REPLAY_FLOOD = "replay_flood"
```

**총 21개 실험 타입** (기존 5개 + 미구현 6개 + 업계 8개 + 시스템 고유 2개)

---

## 9. 안전 메커니즘 요약

### 9.1 Blast Radius 하드캡 (Hard Cap)

각 실험 타입별 **운영 환경 통제 불능 방지**를 위한 상수:

| 실험 타입 | 하드캡 | 값 | 코드 위치 |
|-----------|--------|-----|-----------|
| `SimulatedDiskIOExperiment` | `MAX_IO_LATENCY_MS` | 2,000ms | §3.1 |
| `SimulatedDiskIOExperiment` | `MAX_FAILURE_RATE` | 30% | §3.1 |
| `ReplayFloodExperiment` | `MAX_ENTRIES_TO_CREATE` | 5,000건 | §6.2 |
| `ReplayFloodExperiment` | `MAX_REPLAY_RATE` | 500/s | §6.2 |

> **기존 구현 참조**: `services/chaos/blast_radius.py`의 `BlastRadiusPolicy` 클래스
> - `max_traffic_percent_region: float = 10.0`
> - `region_max_concurrent: int = 1`

### 9.2 Monotonic TTL (Clock Skew 보호)

**문제**: `ClockSkewExperiment`가 시스템 시간을 100년 뒤로 돌리면, 실험 엔진 자신의 TTL 타이머도 미래로 인식되어 롤백 불가

**해결**: `time.monotonic()` 기반 TTL

```python
# ClockSkewExperiment 전용
def is_expired_monotonic(self) -> bool:
    elapsed = time.monotonic() - self._monotonic_start
    return elapsed >= self._effective_ttl
```

> **기존 사용 패턴**: `metrics/decorators.py:86-88`에서 `time.monotonic()` 이미 사용 중

### 9.3 가상 격리 (Virtual Isolation)

**문제**: `ReplayFloodExperiment`가 운영 DLQ에 5,000건 생성 시 데이터 오염 (Data Pollution)

**해결**: 격리된 네임스페이스 + 자동 필터링

```python
CHAOS_DOMAIN_PREFIX = "chaos_test:"  # domain 필드 접두어
METADATA_SYNTHETIC_FLAG = {
    "is_synthetic": True,
    "is_chaos_experiment": True,
}
```

> **기존 구현 참조**: `chaos_context.py:220-240`의 `attach_chaos_context()` 함수
> - Error Budget 계산 시 `metadata__is_chaos_experiment=True` 자동 제외

### 9.4 Assertion Scoring (자동 채점)

**문제**: "장애를 냈다"에서 끝나면 안 됨. "시스템이 예상대로 대응했는가" 판정 필요

**해결**: `ResilienceExpectation` + `ResilienceValidator`

```python
# 이미 구현됨: services/chaos/resilience_expectation.py
config = ExperimentConfig(
    resilience_expectation=ResilienceExpectation.expect_cb_open(
        target_service="payment-api",
        within_seconds=10.0,  # 10초 내 CB OPEN 기대
    ),
)

# 검증 결과 Audit 로그에 기록
# {
#     "resilience_passed": True,
#     "resilience_score": 1.0,
#     "summary": "Resilience: 1/1 (100%)"
# }
```

> **기존 구현 위치**:
> - `services/chaos/resilience_expectation.py:1-446`
> - `services/chaos/resilience_validator.py:1-545`
> - `services/chaos/base.py:182-200` (ExperimentConfig.resilience_expectation)

---

## 10. 기술 부채 점검 항목

> **$500M를 위해 마지막으로 점검해야 할 항목**

| # | 항목 | 현재 상태 | 상세 문서 |
|---|------|----------|-----------|
| 1 | **Monotonic Clock 사용 여부** | ClockSkewExperiment에 구현 필요 | [34_CHAOS_SAFETY_MECHANISMS.md](34_CHAOS_SAFETY_MECHANISMS.md) §2 |
| 2 | **ContextVar 전파 무결성** | 이미 구현됨 (`_celery_context_var`, `_current_actor`) | §5.1 참조 |
| 3 | **DLQ Cleanup 전략** | `purge_archived()` 이미 구현됨 + rollback에서 자동 정리 | §6.2 참조 |

### 10.1 Monotonic Clock 사용 여부 (구현 필요)

- **위험**: ClockSkewExperiment가 엔진 자폭 유발 가능
- **해결**: `time.monotonic()` 기반 TTL 타이머
- **구현 위치**: §5.1 ClockSkewExperiment

### 10.2 ContextVar 전파 무결성 (이미 구현됨)

- **현재 상태**: Celery 워커에서 `set_celery_context()` 자동 호출
- **코드 위치**: `audit/trace.py:214-280`
- **검증**: `tests/unit/selfhealing/test_celery_trace_standardization.py`

### 10.3 DLQ Cleanup 전략 (이미 구현됨)

- **현재 상태**: `purge_archived()` 함수로 archived 엔트리 삭제 가능
- **코드 위치**: `adapters/redis/dlq.py:933-959`
- **ReplayFlood 적용**: rollback 시 `is_chaos_experiment=True` 필터로 자동 정리

---

## 11. 구현 순서 (Implementation Order)

### 이미 완료된 구현 (코드 근거)

> ⚠️ 아래 기능들은 이미 구현되어 있으므로, 새로운 실험 구현 시 재사용 가능합니다.

| # | 기능 | 상태 | 코드 위치 |
|---|------|------|-----------|
| 1 | ChaosExperiment 기본 클래스 | ✅ 완료 | `services/chaos/base.py:362+` |
| 2 | SteadyStateHypothesis | ✅ 완료 | `services/chaos/base.py:302-355` |
| 3 | StopConditionsChecker | ✅ 완료 | `services/chaos/stop_conditions.py` |
| 4 | SafetyGuard 연동 | ✅ 완료 | `services/chaos/safety_guard.py` |
| 5 | Kill Switch 연동 | ✅ 완료 | `services/chaos/safety_guard.py:649-656` |
| 6 | Auto-Abort (지표 기반) | ✅ 완료 | `services/chaos/base.py:1203-1248` |
| 7 | 가상 격리 상수 | ✅ 완료 | `services/chaos/constants.py` |
| 8 | 격리 헬퍼 | ✅ 완료 | `services/chaos/isolation_helpers.py` |
| 9 | ContextVar 전파 | ✅ 완료 | `audit/trace.py:214-280` |
| 10 | ExperimentHardCaps | ✅ 완료 | `services/chaos/constants.py` |
| 11 | **MonotonicTTLHelper** | ✅ **2026-01-14 구현** | `services/chaos/base.py:359-473` |
| 12 | **CertificateExpiryExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py:1810-1940` |
| 13 | **ClockSkewExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py:1950-2100` |
| 14 | **DNSFailureExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |
| 15 | **NetworkBlackholeExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |
| 16 | **SimulatedDiskIOExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |
| 17 | **SimulatedTLSFailureExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |
| 18 | **AuditStorageFailureExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |
| 19 | **ReplayFloodExperiment** | ✅ **2026-01-14 구현** | `services/chaos/experiment_impl.py` |

### Phase 1: 기반 안전 메커니즘 (P0) ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|------|
| 1-1 | Monotonic TTL 헬퍼 추가 | `services/chaos/base.py` | ✅ **2026-01-14 구현** |
| 1-2 | 가상 격리 상수 정의 | `services/chaos/constants.py` | ✅ 완료 |
| 1-3 | SteadyStateHypothesis | `services/chaos/base.py` | ✅ 완료 |
| 1-4 | StopConditionsChecker | `services/chaos/stop_conditions.py` | ✅ 완료 |

### Phase 2: P1 실험 구현 (기존 코드 활용) ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|--------|
| 2-1 | `PoolExhaustionExperiment` | `services/chaos/experiment_impl.py` | ✅ 이미 구현됨 (Phase 5-2) |
| 2-2 | `CertificateExpiryExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |
| 2-3 | `ClockSkewExperiment` (Monotonic TTL 포함) | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |

### Phase 3: P2 실험 구현 (확장 필요) ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|--------|
| 3-1 | `DNSFailureExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |
| 3-2 | `AuditStorageFailureExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |

### Phase 4: P3 실험 구현 (시뮬레이션 레벨) ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|--------|
| 4-1 | `NetworkBlackholeExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |
| 4-2 | `SimulatedDiskIOExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |
| 4-3 | `SimulatedTLSFailureExperiment` | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |
| 4-4 | `ReplayFloodExperiment` (가상 격리 포함) | `services/chaos/experiment_impl.py` | ✅ **2026-01-14 구현** |

### Phase 5: ExperimentType Enum 확장 ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|--------|
| 5-1 | Enum 값 추가 (전체 6개 추가) | `services/chaos/base.py` | ✅ **2026-01-14 구현** |
| 5-2 | 테스트 작성 (101개) | `tests/self_healing/chaos/test_chaos_industry_experiments.py` | ✅ **2026-01-14 작성 및 통과** |

### 우선순위 요약

| 우선순위 | 항목 | 상태 | 설명 |
|----------|------|------|------|
| ✅ 완료 | SteadyStateHypothesis | ✅ 완료 | 정상 상태 가설 검증 |
| ✅ 완료 | StopConditions | ✅ 완료 | 자동 중단 조건 |
| ✅ 완료 | Kill Switch | ✅ 완료 | 전역 비상 정지 |
| ✅ 완료 | 가상 격리 헬퍼 | ✅ 완료 | 테스트 데이터 격리 |
| ✅ 완료 | Monotonic TTL | ✅ **2026-01-14 구현** | ClockSkewExperiment 필수 |
| 🔴 P0 | Zombie Hunter 태스크 | 🔴 구현 필요 | 고아 실험 정리 |
| ✅ 완료 | PoolExhaustionExperiment | ✅ 완료 | 기존 모니터 활용 |
| ✅ 완료 | CertificateExpiryExperiment | ✅ **2026-01-14 구현** | 기존 모니터 활용 |
| ✅ 완료 | ClockSkewExperiment | ✅ **2026-01-14 구현** | Monotonic TTL 보호 적용 |
| ✅ 완료 | DNSFailureExperiment | ✅ **2026-01-14 구현** | DNS 장애 시뮬레이션 |
| ✅ 완료 | NetworkBlackholeExperiment | ✅ **2026-01-14 구현** | 네트워크 블랙홀 |
| ✅ 완료 | SimulatedDiskIOExperiment | ✅ **2026-01-14 구현** | 디스크 I/O 시뮬레이션 |
| ✅ 완료 | SimulatedTLSFailureExperiment | ✅ **2026-01-14 구현** | TLS 실패 시뮬레이션 |
| ✅ 완료 | AuditStorageFailureExperiment | ✅ **2026-01-14 구현** | Audit 저장소 장애 |
| ✅ 완료 | ReplayFloodExperiment | ✅ **2026-01-14 구현** | DLQ Replay 폭풍 |

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md) | 미구현 실험 타입 |
| [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md) | 힐링 시스템 연동 |
| [34_CHAOS_SAFETY_MECHANISMS.md](34_CHAOS_SAFETY_MECHANISMS.md) | 안전 메커니즘 상세 구현 |
| [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md) | 기존 통합 계획 |

---

## 12. 업계 표준 비교 및 현재 구현 상태

### 12.1 Netflix Chaos Engineering 원칙 대비

| 원칙 | Netflix | 현재 구현 | 상태 |
|------|---------|----------|------|
| **Steady State 가설 수립** | SPS 기준 | ✅ `SteadyStateHypothesis` 클래스 | `base.py:302-355` |
| **실제 이벤트 시뮬레이션** | 프로덕션 장애 재현 | ✅ 21개 실험 타입 | `experiment_impl.py` |
| **프로덕션 실험** | 통제된 환경 | ✅ SafetyGuard | `safety_guard.py` |
| **자동화 & 지속 실행** | 자동 스케줄링 | ✅ ChaosScheduler | 스케줄러 구현 |
| **Blast Radius 최소화** | 점진적 확대 | ✅ 하드캡 상수 | `constants.py` |

### 12.2 Gremlin Safety Net 대비

| 기능 | Gremlin | 현재 구현 | 상태 | 코드 위치 |
|------|---------|----------|------|-----------|
| **Halt (Kill Switch)** | 모든 실험 즉시 중단 | ✅ | 구현됨 | `safety_guard.py:649-656` |
| **Rollback** | 자동 복구 | ✅ | 구현됨 | `base.py:rollback()` |
| **Targeting** | 영향 범위 제어 | ✅ | 구현됨 | `BlastRadiusPolicy` |
| **Auto-Abort (지표 기반)** | error_rate, latency | ✅ | 구현됨 | `stop_conditions.py` |

### 12.3 AWS FIS Stop Conditions 대비

| 기능 | AWS FIS | 현재 구현 | 상태 | 코드 위치 |
|------|---------|----------|------|-----------|
| **CloudWatch Alarm 연동** | 알람 기반 중단 | ⚠️ | 부분 구현 | Prometheus 연동 가능 |
| **% 기반 타겟팅** | 10%, 25%, 50% | ✅ | 하드캡 구현 | `constants.py:ExperimentHardCaps` |
| **Error Rate 중단** | 5% 초과 시 | ✅ | 구현됨 | `stop_conditions.py:35` |
| **Latency P99 중단** | 2초 초과 시 | ✅ | 구현됨 | `stop_conditions.py:38` |

### 12.4 LitmusChaos Probes 대비

| 기능 | LitmusChaos | 현재 구현 | 상태 | 코드 위치 |
|------|-------------|----------|------|-----------|
| **Steady State Probe** | HTTP/Cmd/K8s | ✅ | 구현됨 | `SteadyStateHypothesis.validate()` |
| **Continuous Validation** | 실험 중 지속 검증 | ✅ | 구현됨 | `_monitor_with_kill_switch()` |
| **Auto-Rollback** | 조건 위반 시 | ✅ | 구현됨 | `auto_abort_stop_condition` |

### 12.5 Gap 분석 요약

| # | 항목 | 업계 표준 | 현재 상태 | Gap |
|---|------|----------|----------|-----|
| 1 | Steady State Hypothesis | ✅ 필수 | ✅ 구현됨 | - |
| 2 | Auto-Abort (지표 기반) | ✅ 필수 | ✅ 구현됨 | - |
| 3 | Kill Switch (전역 중단) | ✅ 필수 | ✅ 구현됨 | - |
| 4 | TTL 기반 자동 만료 | ✅ 필수 | ✅ 구현됨 | - |
| 5 | Monotonic TTL (Clock Skew 보호) | ⚠️ 고급 | ✅ **2026-01-14 구현** | 완료 |
| 6 | Zombie Hunter (고아 실험 정리) | ⚠️ 고급 | 🔴 구현 필요 | **34_CHAOS_SAFETY 참조** |
| 7 | 점진적 확대 (Gradual Rollout) | ⚠️ 권장 | ⚠️ 하드캡만 | 추후 확장 |
| 8 | 실험 전용 대시보드 | ⚠️ 권장 | ⚠️ Grafana 연동 가능 | 추후 확장 |

### 12.6 이미 구현된 핵심 기능 (코드 근거)

```python
# 1. SteadyStateHypothesis (base.py:302-355)
@dataclass
class SteadyStateHypothesis:
    p50_latency_max_ms: float = 100.0
    p99_latency_max_ms: float = 500.0
    error_rate_max_percent: float = 0.1
    
    def validate(self, metrics: Dict[str, float]) -> tuple[bool, List[str]]: ...

# 2. StopConditionsConfig (stop_conditions.py:25-70)
@dataclass
class StopConditionsConfig:
    max_error_rate_percent: float = 5.0      # 에러율 5% 초과 시 중단
    max_latency_p99_ms: int = 2000           # P99 2초 초과 시 중단
    consecutive_breaches_required: int = 2   # 연속 2회 위반 시

# 3. KillSwitch (safety_guard.py:649-656)
def _check_kill_switch(self) -> bool:
    control = get_system_control()
    return not control.is_selfhealing_enabled()

# 4. Auto-Abort in Monitoring Loop (base.py:1203-1248)
if stop_result.should_stop:
    self._kill_requested = True
    self._stop_condition_violation = "; ".join(violation_messages)
    self._audit("auto_abort_stop_condition", {...})
    break
```

---

## 버전 정보

- **현재 버전**: 1.5.0
- **마지막 업데이트**: 2026-01-14
- **변경 이력**:
  - 1.5.0 (2026-01-14): **Phase 3-5 구현 완료** - DNSFailureExperiment, NetworkBlackholeExperiment, SimulatedDiskIOExperiment, SimulatedTLSFailureExperiment, AuditStorageFailureExperiment, ReplayFloodExperiment 구현, ExperimentType Enum 확장 (6개 추가), 테스트 101개 작성 및 통과
  - 1.4.0 (2026-01-14): **Phase 1-2 구현 완료** - MonotonicTTLHelper, CertificateExpiryExperiment, ClockSkewExperiment 구현, ExperimentType Enum 확장 (CERTIFICATE_EXPIRY, CLOCK_SKEW, DNS_FAILURE), 테스트 44개 작성 및 통과
  - 1.3.0 (2026-01-14): 실제 코드 검증 기반 구현 상태 업데이트, 구현 순서 재정렬 (이미 완료된 기능 명시), 우선순위 요약 추가
  - 1.2.0 (2026-01-14): 업계 표준 비교 섹션 추가, 구현 상태 코드 근거 명시
  - 1.1.0 (2026-01-14): 리뷰 피드백 반영 - 하드캡, Monotonic TTL, 가상 격리, Assertion Scoring
  - 1.0.0 (2026-01-09): 초기 버전
- **담당자**: SelfHealing Team

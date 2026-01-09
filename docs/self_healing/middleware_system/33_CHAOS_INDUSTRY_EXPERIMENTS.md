# 33. 업계 표준 Chaos 실험 추가 계획

> **작성일**: 2026-01-09  
> **상태**: 계획 수립  
> **관련 문서**: [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md), [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md)

---

## 1. 개요

### 1.1 현재 상태 vs 업계 표준

| 카테고리 | 업계 표준 실험 | 현재 구현 | Gap |
|----------|---------------|-----------|-----|
| **네트워크** | Latency, Packet Loss, DNS Failure, Blackhole, Bandwidth | ✅ 2/5 | 3개 |
| **인프라** | Container Kill, Pod Restart, AZ Failover | ❌ 0/3 | 3개 (범위 외) |
| **리소스** | CPU, Memory, Disk I/O, Process Kill, FD Exhaustion | ✅ 1/5 | 4개 |
| **상태** | State Injection, Clock Skew, Dependency Failure | ❌ 0/3 | 3개 |
| **보안** | Certificate Expiry, TLS Failure, Auth Failure | ❌ 0/3 | 3개 |

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

### 3.1 DiskIOExperiment (신규)

#### 목적
디스크 I/O 지연/실패 시뮬레이션으로 DB/로그 복원력 검증

#### 시스템 연동 가능성
- **DLQ Service**: 디스크 쓰기 실패 시 메모리 폴백
  - 코드 위치: `services/dlq/base.py`
- **Audit Resilience**: L1(Memory) → L2(Disk) 폴백 검증
  - 코드 위치: `audit/resilience.py:500-600`

#### 구현 명세

```python
class DiskIOExperiment(ChaosExperiment):
    """
    Simulate disk I/O latency or failures.
    
    Tests fallback to memory storage, log buffering.
    
    Config parameters:
        - io_latency_ms: Latency to add to I/O operations
        - failure_rate: Percentage of I/O ops to fail
        - affected_paths: List of paths to affect (optional)
    """
    
    experiment_type = "disk_io"
    requires_approval = True  # Data loss risk
    
    @property
    def io_latency_ms(self) -> int:
        return self.config.parameters.get("io_latency_ms", 500)
    
    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 0.10)
    
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

### 3.2 ConnectionPoolExhaustionExperiment (신규)

#### 목적
DB 커넥션 풀 고갈 시뮬레이션으로 풀 모니터링 및 폴백 검증

#### 시스템 연동 가능성
- **ConnectionPoolMonitor**: 풀 고갈 감지
  - 코드 위치: `core/pool_monitor.py:83-100`
- **HealthCheckService**: 풀 상태 확인
  - 코드 위치: `services/health_check.py:30-50`

#### 구현 명세

```python
class ConnectionPoolExhaustionExperiment(ChaosExperiment):
    """
    Simulate connection pool exhaustion.
    
    Tests pool monitoring, wait queue handling, graceful degradation.
    
    Config parameters:
        - pool_name: Name of pool to exhaust
        - hold_connections: Number of connections to hold
        - hold_duration_seconds: How long to hold
    """
    
    experiment_type = "connection_pool_exhaustion"
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
            _apply_chaos_config({
                "connection_pool_exhaustion": {
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
            _apply_chaos_config({
                "connection_pool_exhaustion": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

#### PoolMonitor 연동

```python
# core/pool_monitor.py 확장
class ChaosAwareConnectionPoolMonitor(ConnectionPoolMonitor):
    """카오스 실험 인식 풀 모니터."""
    
    def check_health(self) -> PoolHealthStatus:
        chaos_config = _get_current_chaos_config()
        pool_chaos = chaos_config.get("connection_pool_exhaustion", {})
        
        if pool_chaos.get("enabled") and pool_chaos.get("pool_name") == self._pool_name:
            # 풀 고갈 상태 시뮬레이션
            return PoolHealthStatus.EXHAUSTED
        
        return super().check_health()
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

### 4.2 TLSFailureExperiment (신규)

#### 목적
TLS 핸드셰이크 실패 시뮬레이션으로 보안 연결 복원력 검증

#### 구현 명세

```python
class TLSFailureExperiment(ChaosExperiment):
    """
    Simulate TLS handshake failures.
    
    Tests TLS error handling, fallback behaviors, security logging.
    
    Config parameters:
        - failure_type: "handshake_timeout", "cert_invalid", "protocol_mismatch"
        - affected_endpoints: Endpoints to affect
    """
    
    experiment_type = "tls_failure"
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

#### 구현 명세

```python
class ClockSkewExperiment(ChaosExperiment):
    """
    Simulate system clock skew/drift.
    
    Tests time-sensitive logic, token expiry, TTL handling.
    
    Config parameters:
        - skew_seconds: Seconds to skew clock (positive = future)
        - drift_rate: Continuous drift rate per second
    """
    
    experiment_type = "clock_skew"
    requires_approval = True  # Subtle, hard to debug issues
    
    @property
    def skew_seconds(self) -> int:
        return self.config.parameters.get("skew_seconds", 300)  # 5 minutes
    
    def inject_chaos(self) -> bool:
        """Inject clock skew."""
        logger.warning(
            f"[ClockSkew] Skewing time by {self.skew_seconds}s "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "clock_skew": {
                    "enabled": True,
                    "skew_seconds": self.skew_seconds,
                    "experiment_id": self.experiment_id,
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
            
            logger.info(f"[ClockSkew] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "clock_skew": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True
```

#### 애플리케이션 레벨 시뮬레이션

```python
# core/timezone.py 확장
def now() -> datetime:
    """현재 시간 반환 (카오스 실험 시 skew 적용)."""
    current_time = datetime.now(timezone.utc)
    
    # 카오스 설정 확인
    try:
        chaos_config = _get_current_chaos_config()
        clock_skew = chaos_config.get("clock_skew", {})
        
        if clock_skew.get("enabled"):
            skew_seconds = clock_skew.get("skew_seconds", 0)
            current_time = current_time + timedelta(seconds=skew_seconds)
    except Exception:
        pass
    
    return current_time
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

#### 구현 명세

```python
class ReplayFloodExperiment(ChaosExperiment):
    """
    Simulate DLQ replay flood.
    
    Tests replay throttling, prioritization, resource management.
    
    Config parameters:
        - entries_to_create: Number of DLQ entries to create
        - replay_rate: Replays per second to trigger
        - domain: Target domain for entries
    """
    
    experiment_type = "replay_flood"
    requires_approval = True  # Resource intensive
    
    @property
    def entries_to_create(self) -> int:
        return self.config.parameters.get("entries_to_create", 1000)
    
    @property
    def replay_rate(self) -> int:
        return self.config.parameters.get("replay_rate", 100)
    
    def inject_chaos(self) -> bool:
        """Create DLQ entries and trigger mass replay."""
        logger.warning(
            f"[ReplayFlood] Creating {self.entries_to_create} DLQ entries "
            f"and replaying at {self.replay_rate}/s (TTL: {self._effective_ttl}s)"
        )
        
        try:
            from selfhealing.services.dlq import get_dlq_service
            from selfhealing.services.chaos_context import create_chaos_context
            
            dlq = get_dlq_service()
            
            # 카오스 컨텍스트와 함께 DLQ 엔트리 생성
            chaos_context = create_chaos_context(
                experiment_id=self.experiment_id,
                experiment_type=self.experiment_type,
            )
            
            for i in range(self.entries_to_create):
                dlq.store(
                    operation_type="chaos_test",
                    entity_type="test_entity",
                    entity_id=f"chaos_{self.experiment_id}_{i}",
                    domain=self.config.parameters.get("domain", "chaos"),
                    failure_type="simulated",
                    error_message="Chaos experiment entry",
                    metadata=chaos_context.to_dict(),
                )
            
            _apply_chaos_config({
                "replay_flood": {
                    "enabled": True,
                    "entries_created": self.entries_to_create,
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
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[ReplayFlood] Rolling back {self.experiment_id}")
            
            # 생성된 DLQ 엔트리 정리
            try:
                from selfhealing.services.dlq import get_dlq_service
                
                dlq = get_dlq_service()
                # 카오스 실험 엔트리 자동 해결
                # (drift_detection_tasks.py:61-100 로직 활용)
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

## 관련 문서

| 문서 | 설명 |
|------|------|
| [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md) | 미구현 실험 타입 |
| [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md) | 힐링 시스템 연동 |
| [24_CHAOS_INTEGRATION_PLAN.md](24_CHAOS_INTEGRATION_PLAN.md) | 기존 통합 계획 |

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2026-01-09
- **담당자**: SelfHealing Team

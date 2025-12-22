# Governance Implementation Plan - Part 1

> RBAC, 환경변수 Audit, API Rate Limit 구현 계획

---

## 개요

Big 4 실사 대응을 위한 거버넌스 강화 구현 계획입니다.

| 순위 | 항목 | 중요도 | 예상 작업 |
|------|------|--------|----------|
| 1 | RBAC (Operator/Admin 분리) | 최상 | 1일 |
| 2 | 환경변수 Audit | 상 | 0.5일 |
| 3 | API Rate Limit | 상 | 0.5일 |

---

## 1. RBAC (Role-Based Access Control)

### 1.1 현재 상태

```python
# 현재: Admin만 있음
permission_classes = [IsAuthenticated, IsAdminUser]
```

### 1.2 목표 상태

| 역할 | 권한 | 대상 API |
|------|------|----------|
| Viewer | 읽기 전용 | GET /status, GET /dashboard |
| Operator | 운영 작업 | POST /dlq/replay, GET /audit |
| Admin | 모든 권한 | POST /allow, POST /block, PUT /config |

### 1.3 구현 계획

#### Phase 1: 권한 클래스 생성

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/permissions.py`

```python
"""
RBAC Permission Classes for Self-Healing Control API.

Reference: docs/self_healing/10_OPERATIONS_GUIDE.md (권한 테이블)
"""
from rest_framework.permissions import BasePermission
import logging

logger = logging.getLogger(__name__)


class IsViewer(BasePermission):
    """
    읽기 전용 권한 (Viewer 역할).
    
    - 인증된 사용자 + 'selfhealing.view' 그룹 또는 staff
    """
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Admin/Staff는 항상 허용
        if request.user.is_staff:
            return True
        
        # selfhealing_viewer 그룹 멤버십 확인
        return request.user.groups.filter(name='selfhealing_viewer').exists()


class IsOperator(BasePermission):
    """
    운영자 권한 (Operator 역할).
    
    - DLQ 리플레이, CB 상태 조회, Audit 조회 가능
    - CB 수동 제어, Override는 불가
    """
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Admin은 항상 허용
        if request.user.is_staff and request.user.is_superuser:
            return True
        
        # selfhealing_operator 그룹 멤버십 확인
        return request.user.groups.filter(
            name__in=['selfhealing_operator', 'selfhealing_admin']
        ).exists()


class IsSelfHealingAdmin(BasePermission):
    """
    관리자 권한 (Admin 역할).
    
    - 모든 권한 (CB 수동 제어, Override 승인 포함)
    - Fail-Secure: 권한 확인 실패 시 거부
    """
    
    def has_permission(self, request, view):
        try:
            if not request.user or not request.user.is_authenticated:
                return False
            
            # Django superuser
            if request.user.is_superuser:
                return True
            
            # selfhealing_admin 그룹
            return request.user.groups.filter(name='selfhealing_admin').exists()
            
        except Exception as e:
            # Fail-Secure: 오류 시 거부
            logger.warning(f"[RBAC] Permission check failed (deny): {e}")
            return False
```

#### Phase 2: View에 적용

```python
# 읽기 전용 엔드포인트
class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsViewer]

# 운영자 엔드포인트  
class DLQReplayView(APIView):
    permission_classes = [IsAuthenticated, IsOperator]

# 관리자 엔드포인트
class CircuitBreakerControlView(APIView):
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
```

#### Phase 3: Django Group 생성 (Migration)

**파일**: `shopping/migrations/XXXX_create_selfhealing_groups.py`

```python
from django.db import migrations

def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    
    Group.objects.get_or_create(name='selfhealing_viewer')
    Group.objects.get_or_create(name='selfhealing_operator')
    Group.objects.get_or_create(name='selfhealing_admin')

def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__startswith='selfhealing_').delete()

class Migration(migrations.Migration):
    dependencies = [
        ('shopping', 'XXXX_previous'),
        ('auth', '__latest__'),
    ]
    
    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
```

### 1.4 테스트 계획

```python
class TestRBACPermissions:
    def test_viewer_can_read_dashboard(self):
        """Viewer는 대시보드 조회 가능"""
        
    def test_viewer_cannot_replay_dlq(self):
        """Viewer는 DLQ 리플레이 불가"""
        
    def test_operator_can_replay_dlq(self):
        """Operator는 DLQ 리플레이 가능"""
        
    def test_operator_cannot_control_cb(self):
        """Operator는 CB 수동 제어 불가"""
        
    def test_admin_can_do_everything(self):
        """Admin은 모든 작업 가능"""
```

---

## 2. 환경변수 Audit

### 2.1 현재 상태

- API 변경: Audit 로그 ✅
- 환경변수 변경: Audit 로그 ❌

### 2.2 목표 상태

시스템 시작 시점의 환경변수 스냅샷을 AuditService로 기록.

### 2.3 구현 계획

#### Phase 1: 환경변수 스냅샷 수집기

**파일**: `packages/selfhealing-python/src/selfhealing/audit/env_snapshot.py`

```python
"""
Environment Variable Snapshot for Audit Trail.

시스템 시작 시 Self-Healing 관련 환경변수를 스냅샷으로 기록.
"""
import os
import hashlib
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Self-Healing 관련 환경변수 prefix
TRACKED_PREFIXES = [
    'SELFHEALING_',
    'CIRCUIT_BREAKER_',
    'DLQ_',
    'SLA_',
    'CHAOS_',
]

# 민감 키워드 (마스킹 대상)
SENSITIVE_KEYWORDS = ['SECRET', 'PASSWORD', 'TOKEN', 'KEY', 'CREDENTIAL']


def collect_env_snapshot() -> Dict[str, Any]:
    """
    Self-Healing 관련 환경변수 스냅샷 수집.
    
    Returns:
        {
            "variables": {"SELFHEALING_DLQ_ENABLED": "true", ...},
            "hash": "sha256:abc123...",
            "count": 15
        }
    """
    variables = {}
    
    for key, value in os.environ.items():
        # Prefix 매칭
        if any(key.startswith(prefix) for prefix in TRACKED_PREFIXES):
            # 민감 정보 마스킹
            if any(kw in key.upper() for kw in SENSITIVE_KEYWORDS):
                variables[key] = "***MASKED***"
            else:
                variables[key] = value
    
    # 변경 감지용 해시
    sorted_items = sorted(variables.items())
    hash_input = str(sorted_items).encode('utf-8')
    config_hash = hashlib.sha256(hash_input).hexdigest()[:16]
    
    return {
        "variables": variables,
        "hash": f"sha256:{config_hash}",
        "count": len(variables),
    }


def log_env_snapshot_to_audit():
    """
    환경변수 스냅샷을 AuditService로 기록.
    
    AppConfig.ready()에서 호출.
    """
    try:
        from selfhealing.audit import log_config_change
        
        snapshot = collect_env_snapshot()
        
        log_config_change(
            config_type="environment_variables",
            changes=snapshot["variables"],
            changed_by="system_startup",
            reason="Application startup - environment snapshot",
            metadata={
                "hash": snapshot["hash"],
                "variable_count": snapshot["count"],
            }
        )
        
        logger.info(
            f"[EnvAudit] Snapshot recorded: "
            f"count={snapshot['count']}, hash={snapshot['hash']}"
        )
        
    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(f"[EnvAudit] Failed to record snapshot: {e}")
```

#### Phase 2: AppConfig에서 호출

**파일**: `packages/selfhealing-python/src/selfhealing/apps.py` (수정)

```python
from django.apps import AppConfig

class SelfHealingConfig(AppConfig):
    name = 'selfhealing'
    
    def ready(self):
        # 환경변수 스냅샷 기록 (1회)
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit
        log_env_snapshot_to_audit()
```

### 2.4 감사 로그 예시

```json
{
  "timestamp": "2025-12-23T09:00:00Z",
  "action": "config_change",
  "config_type": "environment_variables",
  "actor": "system_startup",
  "changes": {
    "SELFHEALING_DLQ_ENABLED": "true",
    "SELFHEALING_CB_THRESHOLD": "5",
    "SELFHEALING_SECRET_KEY": "***MASKED***"
  },
  "metadata": {
    "hash": "sha256:a1b2c3d4",
    "variable_count": 12
  }
}
```

---

## 3. API Rate Limit

### 3.1 현재 상태

- Control API에 Rate Limit 없음
- 무제한 호출 가능 → 시스템 공격 수단 될 수 있음

### 3.2 목표 상태

Redis 기반 Rate Limit + 장애 대비 Fail-Open 전략.

### 3.3 구현 계획

#### Phase 1: Rate Limit 미들웨어

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py`

```python
"""
Redis-based Rate Limiting for Control API.

Features:
- Redis INCR 기반 슬라이딩 윈도우
- Fail-Open: Redis 장애 시 통과 (+ 경고 로그)
- IP + User 복합 키
"""
import time
import logging
from typing import Optional, Tuple
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# 기본 설정
DEFAULT_RATE_LIMIT = 100  # 요청/분
DEFAULT_WINDOW_SECONDS = 60
CONTROL_API_PATH_PREFIX = '/api/self-healing/'


class RateLimitMiddleware:
    """
    Control API Rate Limiting Middleware.
    
    Redis 장애 시 Fail-Open (통과) + 경고 로그.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = self._get_redis_client()
        
    def _get_redis_client(self):
        """Redis 클라이언트 획득 (lazy)."""
        try:
            from django.core.cache import caches
            return caches['default'].client.get_client()
        except Exception:
            return None
    
    def __call__(self, request):
        # Control API만 대상
        if not request.path.startswith(CONTROL_API_PATH_PREFIX):
            return self.get_response(request)
        
        # Rate limit 체크
        is_allowed, remaining, reset_time = self._check_rate_limit(request)
        
        if not is_allowed:
            return self._rate_limit_response(remaining, reset_time)
        
        # 정상 처리
        response = self.get_response(request)
        
        # Rate limit 헤더 추가
        response['X-RateLimit-Remaining'] = str(remaining)
        response['X-RateLimit-Reset'] = str(reset_time)
        
        return response
    
    def _get_client_key(self, request) -> str:
        """Rate limit 키 생성 (IP + User)."""
        ip = self._get_client_ip(request)
        user_id = getattr(request.user, 'id', 'anonymous')
        return f"ratelimit:control_api:{ip}:{user_id}"
    
    def _get_client_ip(self, request) -> str:
        """클라이언트 IP 추출."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
    
    def _check_rate_limit(self, request) -> Tuple[bool, int, int]:
        """
        Rate limit 체크.
        
        Returns:
            (is_allowed, remaining, reset_timestamp)
        """
        # Redis 없으면 Fail-Open
        if not self.redis_client:
            logger.warning("[RateLimit] Redis unavailable - Fail-Open")
            return (True, DEFAULT_RATE_LIMIT, 0)
        
        try:
            key = self._get_client_key(request)
            now = int(time.time())
            window_start = now - DEFAULT_WINDOW_SECONDS
            
            pipe = self.redis_client.pipeline()
            
            # 슬라이딩 윈도우: 현재 시간 추가 + 오래된 항목 제거
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, DEFAULT_WINDOW_SECONDS + 10)
            
            results = pipe.execute()
            current_count = results[2]
            
            remaining = max(0, DEFAULT_RATE_LIMIT - current_count)
            reset_time = now + DEFAULT_WINDOW_SECONDS
            
            if current_count > DEFAULT_RATE_LIMIT:
                logger.warning(
                    f"[RateLimit] Exceeded: key={key}, count={current_count}"
                )
                return (False, 0, reset_time)
            
            return (True, remaining, reset_time)
            
        except Exception as e:
            # Fail-Open: Redis 오류 시 통과
            logger.error(f"[RateLimit] Redis error - Fail-Open: {e}")
            return (True, DEFAULT_RATE_LIMIT, 0)
    
    def _rate_limit_response(self, remaining: int, reset_time: int):
        """429 응답 생성."""
        return JsonResponse(
            {
                "error": "rate_limit_exceeded",
                "message": "Too many requests to Control API",
                "retry_after": reset_time - int(time.time()),
            },
            status=429,
            headers={
                'X-RateLimit-Remaining': '0',
                'X-RateLimit-Reset': str(reset_time),
                'Retry-After': str(reset_time - int(time.time())),
            }
        )
```

#### Phase 2: 설정에 미들웨어 추가

**파일**: `myproject/settings.py` (수정)

```python
MIDDLEWARE = [
    # ... 기존 미들웨어 ...
    'selfhealing.api.django.rate_limit.RateLimitMiddleware',
]
```

### 3.4 장애 대비 전략: 지능형 하이브리드 제동 (Hybrid Throttling)

기존 Fail-Open은 위험합니다. Redis 장애 시 **L1 로컬 메모리 기반 비상 제동**으로 전환합니다.

#### 기존 vs 개선된 동작

| 상황 | 기존 (Fail-Open) | 개선 (Hybrid) | 이유 |
|------|-----------------|---------------|------|
| Redis 연결 실패 | 그냥 통과 ⚠️ | 로컬 메모리 기반 **엄격한 제한** | Cascading Failure 방지 |
| 로그 기록 | 일반 경고 | **Critical Alert + Audit Tagging** | Forensic 분석 + 즉시 개입 유도 |
| 복구 시점 | 자동 재개 | **Jitter 기반 점진적 복구** | Thundering Herd 방지 |

#### 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Hybrid Rate Limit Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Request ──▶ Redis Health? ──OK──▶ L2 (Redis) Rate Limit            │
│                    │                     │                           │
│                    │                     ▼                           │
│               UNHEALTHY            Normal Limit (100 req/min)        │
│                    │                                                 │
│                    ▼                                                 │
│         ┌─────────────────────┐                                      │
│         │  L1 Local Fallback  │                                      │
│         │  (Emergency Mode)   │                                      │
│         └─────────────────────┘                                      │
│                    │                                                 │
│                    ▼                                                 │
│         Strict Limit (10 req/min) ◀── 10배 엄격                      │
│                    │                                                 │
│                    ▼                                                 │
│         Shadow Audit: "REDIS_FAILURE_BYPASS"                         │
│                    │                                                 │
│                    ▼                                                 │
│         Prometheus: rate_limit_degraded_mode=1                       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### 구현: L1 로컬 메모리 레이트 리미터

```python
"""
L1 Local Memory Rate Limiter (Emergency Fallback).

Redis 장애 시 활성화되는 비상 레이트 리미터.
분산 환경에서 동기화되지 않으므로 10배 엄격한 제한 적용.
"""
import time
import threading
from collections import defaultdict
from typing import Tuple

# 비상 모드 제한 (기본의 1/10)
EMERGENCY_RATE_LIMIT = 10  # 10 req/min per pod
EMERGENCY_WINDOW_SECONDS = 60


class LocalMemoryRateLimiter:
    """
    L1 로컬 메모리 기반 레이트 리미터.
    
    특징:
    - 분산 미동기화 → 엄격한 제한
    - Thread-safe
    - 자동 만료
    """
    
    def __init__(
        self,
        max_requests: int = EMERGENCY_RATE_LIMIT,
        window_seconds: int = EMERGENCY_WINDOW_SECONDS
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
    
    def is_allowed(self, key: str) -> Tuple[bool, int]:
        """
        요청 허용 여부 확인.
        
        Returns:
            (is_allowed, remaining)
        """
        now = time.time()
        window_start = now - self.window_seconds
        
        with self._lock:
            # 윈도우 내 요청만 유지
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts > window_start
            ]
            
            current_count = len(self._requests[key])
            
            if current_count >= self.max_requests:
                return (False, 0)
            
            # 요청 기록
            self._requests[key].append(now)
            remaining = self.max_requests - current_count - 1
            
            return (True, remaining)
    
    def cleanup_expired(self):
        """만료된 항목 정리 (주기적 호출)."""
        now = time.time()
        window_start = now - self.window_seconds
        
        with self._lock:
            expired_keys = []
            for key, timestamps in self._requests.items():
                self._requests[key] = [
                    ts for ts in timestamps
                    if ts > window_start
                ]
                if not self._requests[key]:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._requests[key]
```

#### 구현: Redis Health Checker + Mini Circuit Breaker

```python
"""
Redis Health Checker with Mini Circuit Breaker.

Redis 자체에 대한 Circuit Breaker로 불필요한 연결 시도 방지.
"""
import time
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class RedisHealthState(Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"


class RedisHealthChecker:
    """
    Redis 헬스 체커 + Mini Circuit Breaker.
    
    Features:
    - 주기적 ping으로 상태 확인
    - 연속 실패 시 UNHEALTHY 전환 (연결 시도 중단)
    - 복구 시 Jitter 기반 점진적 재개
    """
    
    PING_INTERVAL = 5  # 5초마다 체크
    FAILURE_THRESHOLD = 3  # 3회 연속 실패 시 UNHEALTHY
    RECOVERY_JITTER_MAX = 10  # 복구 시 최대 10초 랜덤 지연
    
    def __init__(self):
        self._state = RedisHealthState.HEALTHY
        self._consecutive_failures = 0
        self._last_check_time = 0
        self._recovery_time: Optional[float] = None
        self._redis_client = None
    
    @property
    def is_healthy(self) -> bool:
        """Redis 사용 가능 여부."""
        return self._state == RedisHealthState.HEALTHY
    
    @property
    def is_degraded(self) -> bool:
        """Degraded 모드 여부 (UNHEALTHY 또는 RECOVERING)."""
        return self._state != RedisHealthState.HEALTHY
    
    def check_health(self) -> bool:
        """
        Redis 헬스 체크 수행.
        
        Returns:
            True if healthy
        """
        now = time.time()
        
        # 체크 주기 제한
        if now - self._last_check_time < self.PING_INTERVAL:
            return self.is_healthy
        
        self._last_check_time = now
        
        try:
            if self._redis_client is None:
                self._redis_client = self._get_redis_client()
            
            # PING 테스트
            self._redis_client.ping()
            
            # 복구 처리
            if self._state == RedisHealthState.UNHEALTHY:
                self._initiate_recovery()
            elif self._state == RedisHealthState.RECOVERING:
                self._complete_recovery_if_ready()
            else:
                self._consecutive_failures = 0
            
            return self.is_healthy
            
        except Exception as e:
            self._handle_failure(e)
            return False
    
    def _handle_failure(self, error: Exception):
        """실패 처리."""
        self._consecutive_failures += 1
        
        if self._consecutive_failures >= self.FAILURE_THRESHOLD:
            if self._state != RedisHealthState.UNHEALTHY:
                self._state = RedisHealthState.UNHEALTHY
                logger.critical(
                    f"[RedisHealth] UNHEALTHY: {self._consecutive_failures} "
                    f"consecutive failures. Error: {error}"
                )
                # 메트릭 업데이트
                self._record_degraded_mode(True)
    
    def _initiate_recovery(self):
        """복구 시작 (Jitter 적용)."""
        import random
        
        jitter = random.uniform(1, self.RECOVERY_JITTER_MAX)
        self._recovery_time = time.time() + jitter
        self._state = RedisHealthState.RECOVERING
        
        logger.info(
            f"[RedisHealth] Recovery initiated with {jitter:.1f}s jitter"
        )
    
    def _complete_recovery_if_ready(self):
        """복구 완료 확인."""
        if self._recovery_time and time.time() >= self._recovery_time:
            self._state = RedisHealthState.HEALTHY
            self._consecutive_failures = 0
            self._recovery_time = None
            
            logger.info("[RedisHealth] RECOVERED - resuming normal operation")
            self._record_degraded_mode(False)
    
    def _get_redis_client(self):
        """Redis 클라이언트 획득."""
        from django.core.cache import caches
        return caches['default'].client.get_client()
    
    def _record_degraded_mode(self, is_degraded: bool):
        """Prometheus 메트릭 업데이트."""
        try:
            from selfhealing.services.metrics import (
                rate_limit_degraded_mode,
                rate_limit_failover_total,
            )
            rate_limit_degraded_mode.set(1 if is_degraded else 0)
            if is_degraded:
                rate_limit_failover_total.inc()
        except Exception:
            pass


# 싱글톤
_health_checker: Optional[RedisHealthChecker] = None


def get_redis_health_checker() -> RedisHealthChecker:
    global _health_checker
    if _health_checker is None:
        _health_checker = RedisHealthChecker()
    return _health_checker
```

#### 구현: 통합된 Hybrid Rate Limit 미들웨어

```python
class HybridRateLimitMiddleware:
    """
    지능형 하이브리드 레이트 리밋 미들웨어.
    
    L2 (Redis) 정상 → Redis 기반 Rate Limit (100 req/min)
    L2 (Redis) 장애 → L1 (로컬 메모리) 비상 Rate Limit (10 req/min)
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = self._get_redis_client()
        self.local_limiter = LocalMemoryRateLimiter()
        self.health_checker = get_redis_health_checker()
    
    def __call__(self, request):
        if not request.path.startswith(CONTROL_API_PATH_PREFIX):
            return self.get_response(request)
        
        # 헬스 체크
        redis_healthy = self.health_checker.check_health()
        
        if redis_healthy:
            # L2 (Redis) Rate Limit
            is_allowed, remaining, reset_time = self._check_redis_limit(request)
            mode = "normal"
        else:
            # L1 (로컬 메모리) 비상 Rate Limit
            is_allowed, remaining = self._check_local_limit(request)
            reset_time = int(time.time()) + EMERGENCY_WINDOW_SECONDS
            mode = "emergency"
            
            # Shadow Audit 기록
            self._log_emergency_bypass(request, is_allowed)
        
        if not is_allowed:
            return self._rate_limit_response(remaining, reset_time, mode)
        
        response = self.get_response(request)
        response['X-RateLimit-Remaining'] = str(remaining)
        response['X-RateLimit-Reset'] = str(reset_time)
        response['X-RateLimit-Mode'] = mode
        
        return response
    
    def _check_local_limit(self, request) -> Tuple[bool, int]:
        """L1 로컬 메모리 기반 제한."""
        key = self._get_client_key(request)
        return self.local_limiter.is_allowed(key)
    
    def _log_emergency_bypass(self, request, is_allowed: bool):
        """
        비상 모드 감사 로그 (Shadow Audit).
        
        Forensic Advisor가 "왜 트래픽이 튀었지?" 분석 시 활용.
        """
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="rate_limit_emergency",
                changes={
                    "mode": "REDIS_FAILURE_BYPASS",
                    "allowed": is_allowed,
                    "path": request.path,
                    "client_ip": self._get_client_ip(request),
                    "emergency_limit": EMERGENCY_RATE_LIMIT,
                },
                changed_by="system",
                reason="Rate limit operating in emergency mode due to Redis failure",
                metadata={
                    "severity": "critical",
                    "tag": "REDIS_FAILURE_BYPASS",
                }
            )
        except Exception as e:
            # Best-effort
            logger.error(f"[RateLimit] Shadow audit failed: {e}")
    
    def _rate_limit_response(self, remaining: int, reset_time: int, mode: str):
        """429 응답 (모드 포함)."""
        retry_after = max(1, reset_time - int(time.time()))
        
        message = "Too many requests to Control API"
        if mode == "emergency":
            message += " (Emergency mode: stricter limits applied)"
        
        return JsonResponse(
            {
                "error": "rate_limit_exceeded",
                "message": message,
                "mode": mode,
                "retry_after": retry_after,
            },
            status=429,
            headers={
                'X-RateLimit-Remaining': '0',
                'X-RateLimit-Reset': str(reset_time),
                'X-RateLimit-Mode': mode,
                'Retry-After': str(retry_after),
            }
        )
```

### 3.5 메트릭

```python
# Prometheus 메트릭
from prometheus_client import Counter, Gauge

# Rate Limit 초과
rate_limit_exceeded_total = Counter(
    'selfhealing_rate_limit_exceeded_total',
    'Rate limit exceeded count',
    ['client_ip', 'mode']  # mode: normal, emergency
)

# Degraded 모드 상태 (1=degraded, 0=normal)
rate_limit_degraded_mode = Gauge(
    'selfhealing_rate_limit_degraded_mode',
    'Rate limit operating in degraded mode (1=yes, 0=no)'
)

# Failover 발생 횟수
rate_limit_failover_total = Counter(
    'selfhealing_rate_limit_failover_total',
    'Number of times rate limit failed over to local memory'
)
```

### 3.6 Grafana 알림 규칙

```yaml
# Rate Limit Degraded Mode Alert
- alert: RateLimitDegradedMode
  expr: selfhealing_rate_limit_degraded_mode == 1
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Rate Limit in Emergency Mode"
    description: |
      Rate limit is operating in emergency mode due to Redis failure.
      - Local memory fallback active
      - Stricter limits (10 req/min vs 100 req/min)
      - Check Redis health immediately
```

---

## 4. API 티어링 시스템 (Criticality-Based Load Shedding)

### 4.1 개요

비상 모드 시 모든 API를 동일하게 제한하는 대신, **중요도 기반 차등 제한**을 적용합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    API Tiering System                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │   Tier 1    │  │   Tier 2    │  │   Tier 3    │                  │
│  │  Critical   │  │  Standard   │  │ Non-Essential│                  │
│  │             │  │             │  │             │                  │
│  │  50% 허용   │  │  10% 허용   │  │   즉시 차단  │                  │
│  └─────────────┘  └─────────────┘  └─────────────┘                  │
│        │                │                │                           │
│        ▼                ▼                ▼                           │
│  자가치유 액션     설정 변경       대시보드 통계                     │
│  결제 API         로그 조회       핑/헬스체크                        │
│  보안 킬스위치    DLQ 리플레이    메트릭 조회                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 티어 정의 레지스트리 (Tier Definition Registry)

사용자가 티어의 이름, 속성, 개수를 자유롭게 정의할 수 있습니다.

#### 데이터 구조

```python
@dataclass
class TierDefinition:
    """티어 정의."""
    id: str                    # 고유 ID (예: "critical")
    name: str                  # 표시 이름 (예: "최우선 보호")
    multiplier: float          # 비상 모드 배율 (0.0 ~ 1.0)
    priority: int              # 우선순위 (높을수록 중요)
    description: str = ""      # 설명
    color: str = "#000000"     # UI 표시용 색상


# 기본 제공 템플릿 (Best Practice)
DEFAULT_TIER_DEFINITIONS = [
    TierDefinition(
        id="critical",
        name="Mission Critical",
        multiplier=0.5,    # 비상시 50% 허용
        priority=100,
        description="장애 시에도 반드시 동작해야 하는 핵심 API",
        color="#FF0000"
    ),
    TierDefinition(
        id="standard",
        name="Operational",
        multiplier=0.1,    # 비상시 10% 허용
        priority=50,
        description="일반 운영 API",
        color="#FFA500"
    ),
    TierDefinition(
        id="non_essential",
        name="Non-Essential",
        multiplier=0.0,    # 비상시 즉시 차단
        priority=10,
        description="비필수 API (Load Shedding 대상)",
        color="#808080"
    ),
]
```

#### API 설정 예시

```json
PUT /api/self-healing/config/tiers/

{
  "tiers": [
    { 
      "id": "critical", 
      "name": "최우선 보호", 
      "multiplier": 0.8, 
      "priority": 100,
      "description": "결제, 자가치유 액션"
    },
    { 
      "id": "standard", 
      "name": "일반 운영", 
      "multiplier": 0.2, 
      "priority": 50,
      "description": "설정 변경, 로그 조회"
    },
    { 
      "id": "batch", 
      "name": "비필수 작업", 
      "multiplier": 0.0, 
      "priority": 10,
      "description": "대시보드, 통계"
    }
  ]
}
```

### 4.3 동적 매핑 엔진 (Dynamic Mapping Engine)

API 경로를 티어에 매핑합니다. **RegEx 및 Wildcard 지원**.

#### 데이터 구조

```python
@dataclass
class TierMapping:
    """API 경로 → 티어 매핑."""
    pattern: str           # 경로 패턴 (RegEx 또는 Wildcard)
    tier_id: str           # 매핑할 티어 ID
    pattern_type: str      # "regex" | "wildcard" | "exact"
    priority: int = 0      # 매핑 우선순위 (높을수록 먼저 매칭)
    description: str = ""


# 기본 제공 매핑 (Best Practice)
DEFAULT_TIER_MAPPINGS = [
    # Critical (Tier 1)
    TierMapping(
        pattern="/api/self-healing/control/allow/",
        tier_id="critical",
        pattern_type="exact",
        priority=100,
        description="자가치유 허용 액션"
    ),
    TierMapping(
        pattern="/api/self-healing/control/block/",
        tier_id="critical",
        pattern_type="exact",
        priority=100,
        description="자가치유 차단 액션"
    ),
    TierMapping(
        pattern="/api/payment/*",
        tier_id="critical",
        pattern_type="wildcard",
        priority=90,
        description="결제 관련 API"
    ),
    
    # Standard (Tier 2)
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="standard",
        pattern_type="wildcard",
        priority=50,
        description="설정 변경 API"
    ),
    TierMapping(
        pattern="/api/self-healing/control/dlq/*",
        tier_id="standard",
        pattern_type="wildcard",
        priority=50,
        description="DLQ 관련 API"
    ),
    
    # Non-Essential (Tier 3)
    TierMapping(
        pattern="/api/self-healing/dashboard/*",
        tier_id="non_essential",
        pattern_type="wildcard",
        priority=10,
        description="대시보드 API"
    ),
    TierMapping(
        pattern=r"/api/self-healing/metrics/.*",
        tier_id="non_essential",
        pattern_type="regex",
        priority=10,
        description="메트릭 조회 API"
    ),
]
```

#### API 설정 예시

```json
PUT /api/self-healing/config/tier-mappings/

{
  "mappings": [
    {
      "pattern": "/api/self-healing/control/*",
      "tier_id": "critical",
      "pattern_type": "wildcard",
      "priority": 100,
      "description": "모든 제어 API는 Critical"
    },
    {
      "pattern": "/api/self-healing/dashboard/.*",
      "tier_id": "non_essential",
      "pattern_type": "regex",
      "priority": 10
    }
  ]
}
```

### 4.4 유효성 검증 레이어 (Safe Boundary)

시스템을 망가뜨리는 설정을 방지하는 **아키텍트 가이드라인**.

```python
class TierConfigValidator:
    """티어 설정 유효성 검증."""
    
    # Safe Boundary 규칙
    RULES = {
        "max_multiplier": 1.0,           # 배율은 1.0 초과 불가
        "min_tiers": 1,                  # 최소 1개 티어 필수
        "max_tiers": 10,                 # 최대 10개 티어
        "require_critical_tier": True,   # critical 티어 필수
        "min_critical_multiplier": 0.1,  # critical은 최소 10% 허용
    }
    
    def validate(self, tier_definitions: list[TierDefinition]) -> ValidationResult:
        """티어 정의 검증."""
        errors = []
        warnings = []
        
        # 규칙 1: 최소 티어 수
        if len(tier_definitions) < self.RULES["min_tiers"]:
            errors.append("최소 1개의 티어가 필요합니다.")
        
        # 규칙 2: 최대 티어 수
        if len(tier_definitions) > self.RULES["max_tiers"]:
            errors.append(f"티어는 최대 {self.RULES['max_tiers']}개까지 허용됩니다.")
        
        for tier in tier_definitions:
            # 규칙 3: 배율 범위
            if tier.multiplier < 0:
                errors.append(f"티어 '{tier.id}': 배율은 0 이상이어야 합니다.")
            if tier.multiplier > self.RULES["max_multiplier"]:
                errors.append(
                    f"티어 '{tier.id}': 배율은 {self.RULES['max_multiplier']}를 "
                    f"초과할 수 없습니다. (현재: {tier.multiplier})"
                )
        
        # 규칙 4: Critical 티어 필수
        if self.RULES["require_critical_tier"]:
            critical_tiers = [t for t in tier_definitions if t.id == "critical"]
            if not critical_tiers:
                warnings.append(
                    "'critical' 티어가 없습니다. "
                    "비상 시 핵심 API 보호가 어려울 수 있습니다."
                )
            elif critical_tiers[0].multiplier < self.RULES["min_critical_multiplier"]:
                warnings.append(
                    f"'critical' 티어 배율이 너무 낮습니다. "
                    f"최소 {self.RULES['min_critical_multiplier']} 권장."
                )
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )
```

### 4.5 Tier Override (사용자별 예외)

특정 사용자/IP에 대해 티어를 오버라이드합니다.

```python
@dataclass
class TierOverride:
    """사용자별 티어 오버라이드."""
    identifier: str        # IP 또는 사용자 ID
    identifier_type: str   # "ip" | "user_id" | "api_key"
    tier_id: str           # 적용할 티어
    reason: str            # 오버라이드 사유
    expires_at: Optional[datetime] = None


# 예시: 내부 모니터링 시스템은 항상 critical
DEFAULT_OVERRIDES = [
    TierOverride(
        identifier="10.0.0.0/8",
        identifier_type="ip",
        tier_id="critical",
        reason="Internal monitoring system"
    ),
    TierOverride(
        identifier="prometheus-scraper",
        identifier_type="user_id",
        tier_id="critical",
        reason="Prometheus metrics collection"
    ),
]
```

---

## 5. Emergency Mode 고급 설정

### 5.1 Emergency Level (단계별 비상 모드)

단순 on/off가 아닌 **단계별 비상 모드**로 점진적 Load Shedding.

```python
class EmergencyLevel(Enum):
    """비상 모드 레벨."""
    NORMAL = 0       # 정상 운영
    LEVEL_1 = 1      # 경미한 장애 - Tier 3만 차단
    LEVEL_2 = 2      # 중간 장애 - Tier 2, 3 차단
    LEVEL_3 = 3      # 심각한 장애 - Tier 1만 허용 (50%)


# 레벨별 티어 적용 규칙
EMERGENCY_LEVEL_RULES = {
    EmergencyLevel.NORMAL: {
        "critical": 1.0,      # 100%
        "standard": 1.0,      # 100%
        "non_essential": 1.0  # 100%
    },
    EmergencyLevel.LEVEL_1: {
        "critical": 1.0,      # 100%
        "standard": 1.0,      # 100%
        "non_essential": 0.0  # 차단
    },
    EmergencyLevel.LEVEL_2: {
        "critical": 1.0,      # 100%
        "standard": 0.1,      # 10%
        "non_essential": 0.0  # 차단
    },
    EmergencyLevel.LEVEL_3: {
        "critical": 0.5,      # 50%
        "standard": 0.0,      # 차단
        "non_essential": 0.0  # 차단
    },
}
```

### 5.2 Graceful Degradation (점진적 차단)

급격한 전환 대신 **Tier 3 → 2 → 1 순으로 점진적 차단**.

```python
class GracefulDegradationManager:
    """점진적 Load Shedding 관리."""
    
    # 각 레벨 전환 사이 대기 시간
    LEVEL_TRANSITION_DELAY_SECONDS = 30
    
    async def escalate_emergency(self, current_level: EmergencyLevel):
        """
        비상 레벨 상승 (점진적).
        
        NORMAL → LEVEL_1 → LEVEL_2 → LEVEL_3
        각 단계 사이 30초 대기.
        """
        if current_level == EmergencyLevel.LEVEL_3:
            return  # 이미 최고 레벨
        
        next_level = EmergencyLevel(current_level.value + 1)
        
        logger.warning(
            f"[Emergency] Escalating: {current_level.name} → {next_level.name}"
        )
        
        # 레벨 적용
        self._apply_level(next_level)
        
        # Audit 기록
        self._log_level_change(current_level, next_level, "escalate")
        
        # 메트릭 업데이트
        emergency_level_gauge.set(next_level.value)
```

### 5.3 Recovery Gate (복구 안정화)

Redis 복구 후 **N분간 안정화 확인** 후 정상 모드 전환.

```python
@dataclass
class RecoveryGateConfig:
    """복구 게이트 설정 (API로 조정 가능)."""
    
    # 안정화 대기 시간 (초)
    stabilization_period_seconds: int = 300  # 5분
    
    # 메트릭 기반 복구 조건
    require_metrics_stable: bool = True
    cpu_threshold_percent: float = 80.0      # CPU 80% 미만
    memory_threshold_percent: float = 85.0   # 메모리 85% 미만
    error_rate_threshold: float = 0.05       # 에러율 5% 미만
    
    # 점진적 복구 (한 번에 NORMAL로 가지 않음)
    gradual_recovery: bool = True
    level_step_delay_seconds: int = 60       # 레벨당 1분 대기


class RecoveryGate:
    """복구 게이트 - Flapping 방지."""
    
    def __init__(self, config: RecoveryGateConfig):
        self.config = config
        self._recovery_start_time: Optional[float] = None
        self._last_healthy_check: Optional[float] = None
    
    def can_recover(self, current_level: EmergencyLevel) -> bool:
        """
        복구 가능 여부 확인.
        
        조건:
        1. Redis N분간 안정
        2. 시스템 메트릭 정상 범위
        3. 점진적 복구 시 레벨별 대기 시간 충족
        """
        now = time.time()
        
        # 안정화 기간 확인
        if self._recovery_start_time is None:
            self._recovery_start_time = now
            return False
        
        elapsed = now - self._recovery_start_time
        if elapsed < self.config.stabilization_period_seconds:
            remaining = self.config.stabilization_period_seconds - elapsed
            logger.info(
                f"[RecoveryGate] Stabilization in progress: "
                f"{remaining:.0f}s remaining"
            )
            return False
        
        # 메트릭 기반 조건 확인
        if self.config.require_metrics_stable:
            if not self._check_metrics_stable():
                logger.warning("[RecoveryGate] Metrics not stable - delaying recovery")
                return False
        
        return True
    
    def _check_metrics_stable(self) -> bool:
        """시스템 메트릭 안정성 확인."""
        try:
            # CPU, Memory, Error Rate 확인
            # (실제 구현에서는 Prometheus 쿼리 또는 시스템 메트릭 수집)
            return True
        except Exception:
            return False
```

### 5.4 Manual Emergency Trigger

운영자가 Redis 정상 상태에서도 **강제로 비상 모드 전환** 가능.

```python
class ManualEmergencyAPI(APIView):
    """
    POST /api/self-healing/emergency/trigger/
    
    수동 비상 모드 활성화.
    """
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def post(self, request):
        level = request.data.get('level', 'LEVEL_1')
        reason = request.data.get('reason', '')
        duration_minutes = request.data.get('duration_minutes', 30)
        
        if not reason:
            return Response(
                {"error": "reason is required"},
                status=400
            )
        
        # 비상 모드 활성화
        emergency_manager = get_emergency_manager()
        emergency_manager.activate_manual(
            level=EmergencyLevel[level],
            reason=reason,
            duration_minutes=duration_minutes,
            activated_by=request.user.username,
        )
        
        # Audit 기록
        log_config_change(
            config_type="emergency_mode",
            changes={
                "level": level,
                "duration_minutes": duration_minutes,
                "manual": True,
            },
            changed_by=request.user.username,
            reason=reason,
        )
        
        return Response({
            "status": "activated",
            "level": level,
            "expires_at": (
                datetime.now() + timedelta(minutes=duration_minutes)
            ).isoformat(),
            "activated_by": request.user.username,
        })


class ManualEmergencyReleaseAPI(APIView):
    """
    POST /api/self-healing/emergency/release/
    
    수동 비상 모드 해제.
    """
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def post(self, request):
        reason = request.data.get('reason', '')
        force = request.data.get('force', False)
        
        emergency_manager = get_emergency_manager()
        
        # Recovery Gate 확인 (force가 아니면)
        if not force:
            recovery_gate = get_recovery_gate()
            if not recovery_gate.can_recover(emergency_manager.current_level):
                return Response({
                    "status": "blocked",
                    "reason": "Recovery gate conditions not met",
                    "hint": "Use force=true to override"
                }, status=400)
        
        emergency_manager.deactivate(
            reason=reason,
            deactivated_by=request.user.username,
        )
        
        return Response({
            "status": "deactivated",
            "deactivated_by": request.user.username,
        })
```

### 5.5 API로 노출되는 Emergency 설정

```python
class EmergencyConfigSerializer(ApplyStrategyMixin):
    """Emergency Mode 설정 Serializer."""
    
    # L1 Local Fallback 설정
    emergency_rate_limit = serializers.IntegerField(
        required=False, min_value=1, max_value=100,
        help_text="비상 모드 시 Rate Limit (req/min per pod)"
    )
    
    # Redis Health Check 설정
    health_check_interval_seconds = serializers.IntegerField(
        required=False, min_value=1, max_value=60,
        help_text="Redis 헬스 체크 주기 (초)"
    )
    health_check_failure_threshold = serializers.IntegerField(
        required=False, min_value=1, max_value=10,
        help_text="UNHEALTHY 판정까지 연속 실패 횟수"
    )
    
    # Recovery Gate 설정
    stabilization_period_seconds = serializers.IntegerField(
        required=False, min_value=60, max_value=1800,
        help_text="복구 전 안정화 대기 시간 (초)"
    )
    gradual_recovery_enabled = serializers.BooleanField(
        required=False,
        help_text="점진적 복구 활성화"
    )
    level_step_delay_seconds = serializers.IntegerField(
        required=False, min_value=10, max_value=300,
        help_text="레벨별 복구 대기 시간 (초)"
    )
    
    # 메트릭 기반 복구 조건
    require_metrics_stable = serializers.BooleanField(
        required=False,
        help_text="메트릭 안정화 조건 적용"
    )
    cpu_threshold_percent = serializers.FloatField(
        required=False, min_value=50, max_value=100,
        help_text="CPU 임계값 (%)"
    )
    error_rate_threshold = serializers.FloatField(
        required=False, min_value=0.01, max_value=0.5,
        help_text="에러율 임계값"
    )
```

### 5.6 Dry Run Mode (시뮬레이션)

티어 설정 변경 전 **"이 설정이면 어떤 API가 영향받을까?"** 미리보기.

```python
class TierDryRunAPI(APIView):
    """
    POST /api/self-healing/config/tiers/dry-run/
    
    티어 설정 변경 시뮬레이션.
    """
    permission_classes = [IsAuthenticated, IsOperator]
    
    def post(self, request):
        """
        새 티어 설정 적용 시 영향 분석.
        
        Request:
            {
                "tiers": [...],
                "mappings": [...],
                "emergency_level": "LEVEL_2"
            }
        
        Response:
            {
                "affected_apis": [
                    {"path": "/api/...", "current_tier": "standard", "new_tier": "critical"},
                    ...
                ],
                "blocked_count": 15,
                "limited_count": 8,
                "unchanged_count": 42
            }
        """
        proposed_tiers = request.data.get('tiers', [])
        proposed_mappings = request.data.get('mappings', [])
        emergency_level = request.data.get('emergency_level', 'NORMAL')
        
        # 시뮬레이션 실행
        simulator = TierSimulator()
        result = simulator.simulate(
            tiers=proposed_tiers,
            mappings=proposed_mappings,
            level=EmergencyLevel[emergency_level],
        )
        
        return Response({
            "simulation_result": result,
            "warning": "This is a dry run. No changes have been applied.",
        })
```

---

## API 엔드포인트 요약

### Emergency & Rate Limit

| 엔드포인트 | 메서드 | 설명 | 권한 |
|-----------|--------|------|------|
| `/api/self-healing/emergency/trigger/` | POST | 수동 비상 모드 활성화 | Admin |
| `/api/self-healing/emergency/release/` | POST | 수동 비상 모드 해제 | Admin |
| `/api/self-healing/emergency/status/` | GET | 현재 비상 모드 상태 | Viewer |
| `/api/self-healing/config/emergency/` | GET/PUT | Emergency 설정 조회/변경 | Admin |

### Tier Configuration

| 엔드포인트 | 메서드 | 설명 | 권한 |
|-----------|--------|------|------|
| `/api/self-healing/config/tiers/` | GET/PUT | 티어 정의 조회/변경 | Admin |
| `/api/self-healing/config/tier-mappings/` | GET/PUT | 티어 매핑 조회/변경 | Admin |
| `/api/self-healing/config/tier-overrides/` | GET/PUT | 티어 오버라이드 조회/변경 | Admin |
| `/api/self-healing/config/tiers/dry-run/` | POST | 티어 설정 시뮬레이션 | Operator |

---

## 체크리스트

### Phase 1: RBAC
- [ ] `permissions.py` 생성 (IsViewer, IsOperator, IsSelfHealingAdmin)
- [ ] Migration으로 Django Group 생성
- [ ] View에 권한 클래스 적용
- [ ] 테스트 작성

### Phase 2: 환경변수 Audit
- [ ] `env_snapshot.py` 생성
- [ ] AppConfig.ready()에서 호출
- [ ] 테스트 작성

### Phase 3: API Rate Limit (Hybrid Throttling)
- [ ] `LocalMemoryRateLimiter` 생성 (L1 비상 리미터)
- [ ] `RedisHealthChecker` 생성 (Mini Circuit Breaker)
- [ ] `HybridRateLimitMiddleware` 생성 (통합 미들웨어)
- [ ] settings.py에 미들웨어 추가
- [ ] Prometheus 메트릭 추가 (`rate_limit_degraded_mode`, `rate_limit_failover_total`)
- [ ] Grafana 알림 규칙 추가
- [ ] L1 Fallback 동작 테스트
- [ ] Shadow Audit 기록 테스트
- [ ] Jitter 기반 복구 테스트

### Phase 4: API 티어링 시스템
- [ ] `TierDefinition` 모델/데이터클래스 생성
- [ ] `TierMapping` 모델/데이터클래스 생성 (RegEx/Wildcard 지원)
- [ ] `TierConfigValidator` 생성 (Safe Boundary)
- [ ] `TierOverride` 생성 (사용자별 예외)
- [ ] 기본 티어 템플릿 (DEFAULT_TIER_DEFINITIONS)
- [ ] 기본 매핑 템플릿 (DEFAULT_TIER_MAPPINGS)
- [ ] Tier API 엔드포인트 구현
- [ ] AuditService 연동 (티어 변경 로깅)
- [ ] 테스트 작성

### Phase 5: Emergency Mode 고급 기능
- [ ] `EmergencyLevel` Enum 생성 (단계별 비상 모드)
- [ ] `GracefulDegradationManager` 생성 (점진적 차단)
- [ ] `RecoveryGate` 생성 (복구 안정화)
- [ ] `ManualEmergencyAPI` 구현 (수동 트리거)
- [ ] `TierDryRunAPI` 구현 (시뮬레이션)
- [ ] `EmergencyConfigSerializer` 구현
- [ ] Prometheus 메트릭 추가 (`emergency_level_gauge`)
- [ ] Grafana 대시보드 업데이트
- [ ] Integration 테스트 작성

---

## 관련 문서

- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 보안
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 권한 테이블
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Part 2 (Config Versioning, Fail-Safe)

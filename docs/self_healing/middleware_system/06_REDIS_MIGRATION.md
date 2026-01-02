# 06. Redis 기본 저장소 마이그레이션 가이드

> **버전**: v1.0.0  
> **최종 수정**: 2026-01-02  
> **상태**: ✅ 권장 마이그레이션

## 1. 개요

### 1.1 마이그레이션 목적

Django/SQLAlchemy 기반 저장소에서 **Redis 기반 저장소**로 전환합니다.

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| 기본 저장소 | `django` | `redis` |
| Fallback | Django 하드코딩 | ResilientStorageBackend 내장 |
| 분산 환경 | ❌ 지원 불가 | ✅ 완전 지원 |
| 의존성 | Django + PostgreSQL | Redis만 |

### 1.2 왜 Redis인가?

```
┌─────────────────────────────────────────────────────────────────┐
│  분산 환경에서 InMemory/Django의 문제점                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Server A: Django DB → [CB: payment OPEN]                       │
│  Server B: InMemory  → [CB: payment CLOSED]                     │
│  → 상태 불일치! Server B가 장애 서비스 계속 호출                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Redis 사용 시                                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Server A ─┐                                                    │
│            ├─→ Redis: [CB: payment OPEN] ← 단일 진실의 원천      │
│  Server B ─┘                                                    │
│  → 모든 서버가 동일한 상태 공유                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

| 비교 항목 | Django | Redis |
|----------|--------|-------|
| **성능** | DB 쿼리 (수십 ms) | 메모리 기반 (~1ms) |
| **분산 환경** | 가능하나 지연 발생 | 실시간 동기화 |
| **장애 복구** | DB 장애 시 불가 | Memory+WAL fallback |
| **의존성** | Django + PostgreSQL | Redis만 |
| **비-Django** | ❌ 설치 필수 | ✅ 프레임워크 무관 |

---

## 2. ResilientStorageBackend 내장 Fallback

Redis 어댑터 내부에 이미 **완전한 Fallback 로직**이 구현되어 있습니다:

```
┌─────────────────────────────────────────────────────────────────┐
│                    ResilientStorageBackend                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  StorageMode.REDIS (정상)                                       │
│  ├── Redis에 직접 읽기/쓰기                                      │
│  └── 장애 감지 시 → DEGRADED 전환                                │
│                                                                 │
│  StorageMode.DEGRADED (장애)                                    │
│  ├── WAL-First 프로토콜:                                        │
│  │   1. WAL 디스크 기록 (fsync)                                 │
│  │   2. Memory 기록                                             │
│  └── 서버 크래시해도 WAL에서 복구 가능                           │
│                                                                 │
│  StorageMode.RECOVERING (복구 중)                               │
│  ├── Redis 정상화 감지                                          │
│  ├── WAL → Redis 리플레이                                       │
│  ├── Memory → Redis 동기화 (DriftReconciler)                    │
│  └── REDIS 모드로 전환                                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**핵심**: Django/InMemory fallback이 필요 없음 - Redis 어댑터가 자체적으로 처리

---

## 3. 삭제 대상 어댑터

### 3.1 삭제 가능 어댑터

| 어댑터 | 위치 | 삭제 이유 |
|--------|------|----------|
| `DjangoFailedOperationRepository` | `adapters/django/` | Redis로 대체 |
| `DjangoCircuitBreakerStateRepository` | `adapters/django/` | Redis로 대체 |
| `SQLAlchemyFailedOperationRepository` | `adapters/sqlalchemy/` | Redis로 대체 |
| `SQLAlchemyCircuitBreakerStateRepository` | `adapters/sqlalchemy/` | Redis로 대체 |

### 3.2 유지할 어댑터

| 어댑터 | 위치 | 유지 이유 |
|--------|------|----------|
| `InMemoryFailedOperationRepository` | `adapters/memory/` | **단위 테스트 전용** |
| `InMemoryCircuitBreakerStateRepository` | `adapters/memory/` | **단위 테스트 전용** |
| `RedisXxxRepository` | `adapters/redis/` | **프로덕션 기본값** |
| `ResilientStorageBackend` | `adapters/resilient/` | **Redis 내부 fallback** |

### 3.3 삭제 근거

1. **Redis 내장 fallback**: ResilientStorageBackend가 Memory+WAL fallback 처리
2. **분산 환경 필수**: Django/InMemory는 분산 환경에서 상태 불일치 발생
3. **유지보수 비용**: 4개 어댑터 유지보수 → 1개 (Redis)
4. **속도**: Redis (~1ms) vs Django DB (수십 ms)
5. **의존성 단순화**: Django/PostgreSQL 의존성 제거

---

## 4. 마이그레이션 단계

### 4.1 Phase 1: 기본값 변경

```python
# factory.py
class ProviderRegistry:
    _default_repo: str = "redis"  # 변경: "django" → "redis"
```

### 4.2 Phase 2: pyproject.toml 의존성 변경

```toml
[project]
dependencies = [
    "redis>=4.0",  # 필수 의존성으로 추가
]

[project.optional-dependencies]
django = [
    "django>=4.2",
    "djangorestframework>=3.14",
]
# django가 optional로 변경됨
```

### 4.3 Phase 3: Fallback 코드 제거

변경 대상 파일:

| 파일 | 변경 내용 |
|------|----------|
| `services/dlq_service.py` | Django fallback 제거 |
| `services/replay_service.py` | Django fallback 제거 |
| `services/circuit_breaker/service.py` | Django fallback 제거 |
| `adapters/celery/tasks.py` | Django fallback 제거 |
| `adapters/fastapi/dependencies.py` | InMemory → ProviderRegistry |
| `shopping/apps.py` | Django 강제 설정 제거 |

**변경 전:**
```python
try:
    self._repository = ProviderRegistry.get_failed_operation_repo()
except (ImportError, ValueError):
    from .adapters.django_repositories import DjangoFailedOperationRepository
    self._repository = DjangoFailedOperationRepository()
```

**변경 후:**
```python
self._repository = ProviderRegistry.get_failed_operation_repo()
# ResilientStorageBackend가 내부적으로 fallback 처리
```

### 4.4 Phase 4: Django 어댑터 Deprecation

```python
# adapters/django/__init__.py
import warnings

def __getattr__(name):
    if name in ("DjangoFailedOperationRepository", "DjangoCircuitBreakerStateRepository"):
        warnings.warn(
            f"{name} is deprecated. Use Redis adapter instead. "
            "Will be removed in v2.0.0",
            DeprecationWarning,
            stacklevel=2,
        )
    return globals()[name]
```

### 4.5 Phase 5: 어댑터 삭제 (v2.0.0)

```
adapters/
├── django/              # 삭제
├── sqlalchemy/          # 삭제
├── memory/              # 유지 (테스트용)
├── redis/               # 유지 (프로덕션)
└── resilient/           # 유지 (fallback)
```

---

## 5. 환경별 설정

### 5.1 프로덕션

```bash
# 환경변수
REDIS_URL=redis://redis-cluster:6379/0
SELFHEALING_WAL_DIR=/var/log/selfhealing/wal
```

### 5.2 개발/테스트

```bash
# Docker로 Redis 실행
docker run -d --name redis -p 6379:6379 redis:7-alpine

# 환경변수
REDIS_URL=redis://localhost:6379/0
```

### 5.3 단위 테스트

```python
# 테스트에서는 InMemory 사용 (Redis 없이)
from selfhealing.adapters.memory import InMemoryFailedOperationRepository

@pytest.fixture
def dlq_repository():
    return InMemoryFailedOperationRepository()
```

---

## 6. FAQ

### Q1: Redis가 없으면 어떻게 되나요?

**A**: ResilientStorageBackend가 자동으로 Memory+WAL 모드로 전환됩니다. 데이터 손실 없이 Redis 복구 시 자동 동기화됩니다.

### Q2: Django 프로젝트인데 Redis를 써야 하나요?

**A**: 네. 분산 환경(여러 워커/컨테이너)에서는 Django DB보다 Redis가 더 적합합니다. 단일 서버라도 Redis가 더 빠릅니다.

### Q3: InMemory 어댑터는 언제 사용하나요?

**A**: **단위 테스트에서만** 사용합니다. 프로덕션에서는 절대 사용하지 마세요 - 분산 환경에서 상태 불일치가 발생합니다.

### Q4: SQLAlchemy 어댑터는 왜 삭제하나요?

**A**: Redis가 프레임워크에 무관하게 동작하므로, 비-Django 환경에서도 SQLAlchemy 대신 Redis를 사용하면 됩니다.

### Q5: Redis 설치가 부담인 환경은요?

**A**: 현대 인프라(K8s, Docker, 클라우드)에서 Redis는 사실상 표준입니다. `docker run redis` 한 줄로 실행 가능합니다. 분산 환경에서 Redis 없이는 Circuit Breaker가 제대로 동작하지 않습니다.

---

## 7. 체크리스트

### 마이그레이션 전

- [ ] Redis 인스턴스 준비 (docker-compose.yml 또는 클라우드)
- [ ] `REDIS_URL` 환경변수 설정
- [ ] WAL 디렉토리 권한 확인

### 마이그레이션 중

- [ ] factory.py `_default_repo = "redis"` 변경
- [ ] pyproject.toml `redis>=4.0` 필수 의존성 추가
- [ ] Django fallback 코드 제거
- [ ] FastAPI dependencies 수정
- [ ] 테스트 실행 확인

### 마이그레이션 후

- [ ] Django 어댑터 deprecation 경고 추가
- [ ] 문서 업데이트
- [ ] v2.0.0에서 Django/SQLAlchemy 어댑터 삭제

---

## 8. 다음 단계 (구현 예정)

문서 승인 후 아래 코드 변경을 진행합니다:

### 8.1 변경 대상 파일

| # | 파일 | 변경 내용 |
|---|------|----------|
| 1 | `packages/selfhealing-python/src/selfhealing/factory.py` | `_default_repo = "redis"` |
| 2 | `packages/selfhealing-python/pyproject.toml` | `dependencies = ["redis>=4.0"]` |
| 3 | `packages/selfhealing-python/src/selfhealing/services/dlq_service.py` | Django fallback 제거 |
| 4 | `packages/selfhealing-python/src/selfhealing/services/replay_service.py` | Django fallback 제거 |
| 5 | `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py` | Django fallback 제거 |
| 6 | `packages/selfhealing-python/src/selfhealing/adapters/celery/tasks.py` | Django fallback 제거 |
| 7 | `packages/selfhealing-python/src/selfhealing/adapters/fastapi/dependencies.py` | InMemory → ProviderRegistry |
| 8 | `shopping/apps.py` | Django 강제 설정 제거 |

### 8.2 변경 상세

#### (1) factory.py - 기본값 변경
```python
# Before
_default_repo: str = "django"

# After
_default_repo: str = "redis"
```

#### (2) pyproject.toml - Redis 필수 의존성
```toml
# Before
dependencies = []

# After
dependencies = [
    "redis>=4.0",
]
```

#### (3-6) 서비스 fallback 제거
```python
# Before
@property
def repository(self) -> "FailedOperationRepository":
    if self._repository is None:
        try:
            from selfhealing.factory import ProviderRegistry
            self._repository = ProviderRegistry.get_failed_operation_repo()
        except (ImportError, ValueError):
            from .adapters.django_repositories import DjangoFailedOperationRepository
            self._repository = DjangoFailedOperationRepository()
    return self._repository

# After
@property
def repository(self) -> "FailedOperationRepository":
    if self._repository is None:
        from selfhealing.factory import ProviderRegistry
        self._repository = ProviderRegistry.get_failed_operation_repo()
    return self._repository
```

#### (7) FastAPI dependencies - ProviderRegistry 사용
```python
# Before
def get_dlq_service():
    from selfhealing.adapters.memory import InMemoryFailedOperationRepository
    global _failed_operation_repo
    if _failed_operation_repo is None:
        _failed_operation_repo = InMemoryFailedOperationRepository()
    return DLQService(repository=_failed_operation_repo)

# After
def get_dlq_service():
    from selfhealing.factory import ProviderRegistry
    return DLQService(repository=ProviderRegistry.get_failed_operation_repo())
```

#### (8) shopping/apps.py - Django 강제 설정 제거
```python
# Before
def _configure_selfhealing(self):
    try:
        from selfhealing.factory import ProviderRegistry
        ProviderRegistry._default_repo = "django"
    except ImportError:
        pass

# After
def _configure_selfhealing(self):
    # Redis가 기본값이므로 별도 설정 불필요
    # ProviderRegistry는 자동으로 "redis"를 사용
    pass
```

### 8.3 영향 범위

| 영역 | 영향 |
|------|------|
| **프로덕션** | Redis 필수 (ResilientStorageBackend fallback 있음) |
| **개발환경** | `docker run redis` 필요 |
| **단위 테스트** | 변경 없음 (InMemory DI 사용) |
| **통합 테스트** | Redis 필요 (docker-compose.test.yml) |

### 8.4 롤백 계획

문제 발생 시:
```python
# factory.py
_default_repo: str = "django"  # 롤백
```

---

## 9. 관련 문서

| 문서 | 설명 |
|------|------|
| [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) | Redis 저장소 상세 구현 |
| [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) | 어댑터 아키텍처 |
| [00_INDEX.md](00_INDEX.md) | 문서 인덱스 |

---

**결론**: Redis를 기본값으로 전환하고, Django/SQLAlchemy 어댑터를 deprecate → 삭제하는 것이 권장됩니다. ResilientStorageBackend 내에 완전한 fallback 로직이 있으므로 별도의 Django fallback이 필요 없습니다.

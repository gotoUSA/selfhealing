# 06. Redis 기본 저장소 마이그레이션 가이드

> **버전**: v2.0.0  
> **최종 수정**: 2026-01-02  
> **상태**: ✅ 마이그레이션 완료

## 1. 개요

### 1.1 마이그레이션 목적

Django/SQLAlchemy 기반 저장소에서 **Redis 기반 저장소**로 전환합니다.

| 항목 | 변경 전 | 변경 후 | 상태 |
|------|---------|---------|:----:|
| 기본 저장소 | `django` | `redis` | ✅ 완료 |
| Fallback | Django 하드코딩 | ResilientStorageBackend 내장 | ✅ 완료 |
| 분산 환경 | ❌ 지원 불가 | ✅ 완전 지원 | ✅ 완료 |
| 의존성 | Django + PostgreSQL | Redis만 | ✅ 완료 |
| Django 어댑터 | 존재 | **삭제됨** | ✅ 완료 |
| SQLAlchemy 어댑터 | 존재 | **삭제됨** | ✅ 완료 |

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

### 4.4 Phase 4: Django 어댑터 Deprecation ✅ 완료

```python
# adapters/django/__init__.py (삭제됨)
# Deprecation 경고가 추가된 후 v2.0.0에서 삭제됨
```

### 4.5 Phase 5: 어댑터 삭제 ✅ 완료 (v2.0.0)

```
adapters/
├── django/              # ✅ 삭제됨
├── sqlalchemy/          # ✅ 삭제됨
├── django_repositories.py  # ✅ 삭제됨
├── memory/              # 유지 (테스트용)
├── redis/               # 유지 (프로덕션)
└── resilient/           # 유지 (fallback)
```

**삭제된 파일:**
- `adapters/django/` 전체 디렉토리 (models.py, repositories.py, admin.py, migrations/ 등)
- `adapters/sqlalchemy/` 전체 디렉토리
- `adapters/django_repositories.py`

**수정된 파일:**
- `factory.py`: Django/SQLAlchemy 어댑터 등록 코드 제거
- `adapters/__init__.py`: Django 참조 제거, Redis/InMemory 어댑터로 대체
- `myproject/settings/base.py`: `selfhealing.adapters.django` INSTALLED_APPS에서 제거

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

## 7. 체크리스트 ✅ 완료

### 마이그레이션 전

- [x] Redis 인스턴스 준비 (docker-compose.yml 또는 클라우드)
- [x] `REDIS_URL` 환경변수 설정
- [x] WAL 디렉토리 권한 확인

### 마이그레이션 중

- [x] factory.py `_default_repo = "redis"` 변경
- [x] pyproject.toml `redis>=4.0` 필수 의존성 추가
- [x] Django fallback 코드 제거
- [x] FastAPI dependencies 수정
- [x] 테스트 실행 확인

### 마이그레이션 후

- [x] Django 어댑터 deprecation 경고 추가 → 삭제 완료
- [x] 문서 업데이트
- [x] v2.0.0에서 Django/SQLAlchemy 어댑터 삭제 ✅

---

## 8. 구현 완료 ✅

모든 마이그레이션이 완료되었습니다:

### 8.1 변경된 파일

| # | 파일 | 변경 내용 | 상태 |
|---|------|----------|:----:|
| 1 | `packages/selfhealing-python/src/selfhealing/factory.py` | `_default_repo = "redis"` | ✅ |
| 2 | `packages/selfhealing-python/pyproject.toml` | `dependencies = ["redis>=4.0"]` | ✅ |
| 3 | `packages/selfhealing-python/src/selfhealing/services/dlq_service.py` | Django fallback 제거 | ✅ |
| 4 | `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py` | Django fallback 제거 | ✅ |
| 5 | `packages/selfhealing-python/src/selfhealing/adapters/celery/tasks.py` | Django fallback 제거 | ✅ |
| 6 | `packages/selfhealing-python/src/selfhealing/adapters/fastapi/dependencies.py` | InMemory → ProviderRegistry | ✅ |
| 7 | `shopping/apps.py` | Django 강제 설정 제거 | ✅ |
| 8 | `packages/selfhealing-python/src/selfhealing/adapters/__init__.py` | Django → Redis/InMemory | ✅ |
| 9 | `myproject/settings/base.py` | INSTALLED_APPS에서 제거 | ✅ |

### 8.2 삭제된 파일/디렉토리

| 삭제 대상 | 내용 |
|----------|------|
| `adapters/django/` | 전체 디렉토리 (models.py, repositories.py, admin.py, apps.py, config_provider.py, migrations/) |
| `adapters/sqlalchemy/` | 전체 디렉토리 (base.py, models.py, repositories.py 등) |
| `adapters/django_repositories.py` | Django 레포지토리 re-export 파일 |

### 8.3 현재 어댑터 구조

```
adapters/
├── airgap/          # Air-gap 모드 지원
├── alert/           # 알림 어댑터
├── audit/           # 감사 로그 어댑터
├── cache/           # 캐시 어댑터 (Redis, InMemory)
├── celery/          # Celery 태스크
├── fastapi/         # FastAPI 의존성 주입
├── frameworks/      # 프레임워크 통합
├── health_checker.py
├── memory/          # InMemory 레포지토리 (테스트용) ✅ 유지
├── metrics/         # 메트릭 어댑터
├── observability/   # 관측성 어댑터
├── queues/          # 태스크 큐 어댑터
├── rate_limit/      # 레이트 리밋 어댑터
├── redis/           # Redis 레포지토리 (프로덕션) ✅ 유지
├── resilient/       # ResilientStorageBackend ✅ 유지
└── __init__.py
```

### 8.4 영향 범위

| 영역 | 영향 |
|------|------|
| **프로덕션** | Redis 필수 (ResilientStorageBackend fallback 있음) |
| **개발환경** | `docker run redis` 필요 |
| **단위 테스트** | 변경 없음 (InMemory DI 사용) |
| **통합 테스트** | Redis 필요 (docker-compose.test.yml) |

### 8.5 롤백 불가

Django/SQLAlchemy 어댑터가 삭제되었으므로 롤백이 필요한 경우 Git에서 복원해야 합니다:

```bash
# 롤백이 필요한 경우
git checkout HEAD~1 -- packages/selfhealing-python/src/selfhealing/adapters/django/
git checkout HEAD~1 -- packages/selfhealing-python/src/selfhealing/adapters/sqlalchemy/
```

---

## 9. 관련 문서

| 문서 | 설명 |
|------|------|
| [07_HYBRID_STORAGE_ARCHITECTURE.md](07_HYBRID_STORAGE_ARCHITECTURE.md) | 하이브리드 스토리지 아키텍처 **(후속 문서)** |
| [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) | Redis 저장소 상세 구현 |
| [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) | 어댑터 아키텍처 |
| [00_INDEX.md](00_INDEX.md) | 문서 인덱스 |

---

**결론**: Redis 기본값 전환 및 Django/SQLAlchemy 어댑터 삭제가 완료되었습니다. 단, 복잡한 통계/집계 기능은 [07_HYBRID_STORAGE_ARCHITECTURE.md](07_HYBRID_STORAGE_ARCHITECTURE.md)에서 정의한 하이브리드 아키텍처를 통해 ORM 어댑터로 지원됩니다.

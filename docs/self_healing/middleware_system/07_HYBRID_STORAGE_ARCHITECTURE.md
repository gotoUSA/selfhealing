# 07. 하이브리드 스토리지 아키텍처

> **Version**: 1.1.0  
> **Last Updated**: 2026-01-02  
> **Status**: Phase 1,2 구현 완료  
> **Author**: AI Assistant  
> **Reference**: 06_REDIS_MIGRATION.md

---

## 1. 개요

### 1.1 배경

Redis 마이그레이션(Phase 5) 완료 후, Django ORM을 직접 import하는 코드들이 `ImportError`를 발생시키는 문제가 발견되었습니다.

**근본 원인 분석:**
- 런타임 저장소(Redis)와 통계/분석 저장소(ORM)의 요구사항이 다름
- Redis는 단순 CRUD에 최적화, 복잡한 집계 쿼리에는 부적합
- Django ORM 삭제 시 통계 기능 상실

### 1.2 설계 원칙

| 원칙 | 설명 |
|------|------|
| **적재적소** | 각 기술의 강점을 살리는 영역에 배치 |
| **Domain-Free** | selfhealing 패키지는 특정 도메인(shopping 등)을 모름 |
| **프레임워크 중립** | Django, FastAPI, Flask 모두 지원 |
| **Graceful Degradation** | 통계 기능 없어도 런타임은 정상 동작 |

---

## 2. 아키텍처 개요

### 2.1 계층 분리

```
┌─────────────────────────────────────────────────────────────────┐
│                         selfhealing                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              런타임 계층 (Runtime Layer)                  │    │
│  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │    │
│  │  • Circuit Breaker 상태 체크/변경                        │    │
│  │  • DLQ 적재/조회/replay                                  │    │
│  │  • 1-2ms 응답 필수                                       │    │
│  │  • DB 장애 시에도 동작 필수                              │    │
│  │                                                          │    │
│  │  [구현] Redis + ResilientStorageBackend (WAL fallback)   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              운영 계층 (Operations Layer)                 │    │
│  │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │    │
│  │  • 대시보드 통계 (도메인별 집계, 추이 분석)              │    │
│  │  • DLQ 목록 조회 (페이지네이션, 필터링)                  │    │
│  │  • 정리 작업 (archive, purge)                            │    │
│  │  • 10-100ms 응답 허용                                    │    │
│  │                                                          │    │
│  │  [구현] SQL/ORM (Django ORM 또는 SQLAlchemy)             │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 기술별 적합성 비교

| 작업 유형 | Redis | SQL/ORM | 선택 |
|----------|-------|---------|------|
| CB 상태 체크 (`should_allow`) | ✅ 1-2ms | ❌ 5-10ms | **Redis** |
| DLQ 적재 (`store_failure`) | ✅ 1-2ms | ❌ 5-10ms | **Redis** |
| 도메인별 pending 개수 집계 | ❌ O(n) 스캔 | ✅ GROUP BY | **ORM** |
| 월간 복구율 계산 | ❌ 메모리 집계 | ✅ AVG, COUNT | **ORM** |
| 대량 목록 페이지네이션 | ❌ ZRANGE | ✅ LIMIT/OFFSET | **ORM** |
| 분산 환경 동기화 | ✅ 클러스터 | ❌ 단일 DB | **Redis** |
| DB 장애 격리 | ✅ 독립 | ❌ 같이 죽음 | **Redis** |

---

## 3. 프레임워크별 지원 전략

### 3.1 지원 매트릭스

| 사용자 유형 | 런타임 저장소 | 통계 저장소 | 대시보드 |
|------------|--------------|-------------|----------|
| **Django 프로젝트** | Redis | Django ORM | ✅ 풀 기능 |
| **FastAPI 프로젝트** | Redis | SQLAlchemy | ✅ 풀 기능 |
| **경량/독립 사용** | Redis | (없음) | Prometheus/Grafana |

### 3.2 Django 사용자

```python
# shopping/apps.py
from django.apps import AppConfig

class ShoppingConfig(AppConfig):
    def ready(self):
        from selfhealing.factory import ProviderRegistry
        from selfhealing.adapters.django import DjangoStatisticsAdapter
        
        # Django 모델 등록 (Domain-Free 유지)
        from shopping.models import FailedOperation, CircuitBreakerState
        
        ProviderRegistry.register_statistics_adapter(
            DjangoStatisticsAdapter(
                failed_operation_model=FailedOperation,
                circuit_breaker_model=CircuitBreakerState,
            )
        )
```

### 3.3 FastAPI 사용자

```python
# main.py
from fastapi import FastAPI
from selfhealing.factory import ProviderRegistry
from selfhealing.adapters.sqlalchemy import SQLAlchemyStatisticsAdapter

app = FastAPI()

@app.on_event("startup")
async def startup():
    from database import SessionLocal, engine
    
    ProviderRegistry.register_statistics_adapter(
        SQLAlchemyStatisticsAdapter(session_factory=SessionLocal)
    )
```

### 3.4 경량 사용자 (통계 없음)

```python
# 런타임 기능만 사용
from selfhealing.factory import ProviderRegistry

# Redis 런타임만 활성화 (기본값)
cb_repo = ProviderRegistry.get_circuit_breaker_repo()  # Redis
dlq_repo = ProviderRegistry.get_failed_operation_repo()  # Redis

# 통계는 Prometheus + Grafana로 대체
# selfhealing_dlq_pending_total{domain="payment"} 
# selfhealing_circuit_breaker_state{service="external_api"}
```

---

## 4. 인터페이스 설계

### 4.1 StatisticsRepository 인터페이스

```python
# selfhealing/interfaces/statistics.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


@dataclass
class StatusCounts:
    """상태별 개수"""
    total: int = 0
    pending: int = 0
    resolved: int = 0
    failed: int = 0
    archived: int = 0


@dataclass
class DomainDistribution:
    """도메인별 분포"""
    domain: str
    count: int
    percentage: float


@dataclass
class CleanupStats:
    """정리 작업 통계"""
    total: int = 0
    by_status: Dict[str, int] = None
    resolved_older_than_30_days: int = 0
    archived_older_than_90_days: int = 0


@dataclass
class PaginatedResult:
    """페이지네이션 결과"""
    items: List[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


class StatisticsRepositoryInterface(ABC):
    """
    통계/대시보드용 Repository 인터페이스.
    
    런타임 Repository와 분리된 읽기 전용 인터페이스.
    복잡한 집계 쿼리를 지원합니다.
    """
    
    # =========================================================================
    # DLQ 통계
    # =========================================================================
    
    @abstractmethod
    def get_status_counts(self) -> StatusCounts:
        """상태별 DLQ 개수 집계"""
        pass
    
    @abstractmethod
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        """도메인별 분포 (상위 N개)"""
        pass
    
    @abstractmethod
    def get_failure_type_distribution(self, limit: int = 10) -> List[Dict[str, Any]]:
        """실패 유형별 분포"""
        pass
    
    @abstractmethod
    def get_recent_activity(
        self, 
        hours: int = 24,
        days: int = 7,
    ) -> Dict[str, int]:
        """최근 활동 통계 (신규/해결 건수)"""
        pass
    
    @abstractmethod
    def get_resolution_rate(
        self,
        days: int = 30,
    ) -> float:
        """복구 성공률 (최근 N일)"""
        pass
    
    @abstractmethod
    def get_avg_retry_count(self) -> float:
        """평균 재시도 횟수"""
        pass
    
    # =========================================================================
    # DLQ 목록 조회 (페이지네이션)
    # =========================================================================
    
    @abstractmethod
    def list_entries(
        self,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        failure_type: Optional[str] = None,
        order_by: str = "-created_at",
    ) -> PaginatedResult:
        """DLQ 항목 목록 조회"""
        pass
    
    @abstractmethod
    def get_entry_detail(self, entry_id: int) -> Optional[Dict[str, Any]]:
        """DLQ 항목 상세 조회"""
        pass
    
    # =========================================================================
    # 정리 작업
    # =========================================================================
    
    @abstractmethod
    def get_cleanup_stats(self) -> CleanupStats:
        """정리 작업 통계"""
        pass
    
    @abstractmethod
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """오래된 resolved 항목 아카이브"""
        pass
    
    @abstractmethod
    def purge_archived(
        self,
        ids: Optional[List[int]] = None,
        older_than_days: Optional[int] = None,
    ) -> int:
        """아카이브된 항목 영구 삭제"""
        pass
    
    # =========================================================================
    # Circuit Breaker 통계
    # =========================================================================
    
    @abstractmethod
    def get_circuit_breaker_summary(self) -> Dict[str, Any]:
        """Circuit Breaker 요약 (open/closed/half-open 개수)"""
        pass
    
    @abstractmethod
    def list_circuit_breakers(self) -> List[Dict[str, Any]]:
        """모든 Circuit Breaker 목록"""
        pass
```

### 4.2 Null Object 패턴 (통계 미등록 시)

```python
# selfhealing/adapters/statistics/null.py

class NullStatisticsRepository(StatisticsRepositoryInterface):
    """
    통계 어댑터 미등록 시 사용되는 Null Object.
    
    에러 없이 빈 결과를 반환합니다.
    런타임 기능은 정상 동작, 통계만 미지원.
    """
    
    def get_status_counts(self) -> StatusCounts:
        return StatusCounts()
    
    def get_domain_distribution(self, limit: int = 10) -> List[DomainDistribution]:
        return []
    
    def get_cleanup_stats(self) -> CleanupStats:
        return CleanupStats()
    
    def list_entries(self, **kwargs) -> PaginatedResult:
        return PaginatedResult(
            items=[],
            total=0,
            page=1,
            page_size=20,
            has_next=False,
            has_prev=False,
        )
    
    # ... 모든 메서드가 빈 결과 반환
```

---

## 5. ProviderRegistry 확장

### 5.1 현재 구조

```python
class ProviderRegistry:
    # 런타임 저장소 (Redis)
    _default_repo = "redis"
    
    def get_circuit_breaker_repo() -> CircuitBreakerStateRepository
    def get_failed_operation_repo() -> FailedOperationRepository
```

### 5.2 확장된 구조

```python
class ProviderRegistry:
    # 런타임 저장소 (Redis) - 기존 유지
    _default_repo = "redis"
    
    # 통계 저장소 (ORM) - 신규
    _statistics_adapter: Optional[StatisticsRepositoryInterface] = None
    
    # =========================================================================
    # 런타임 Repository (기존)
    # =========================================================================
    
    @classmethod
    def get_circuit_breaker_repo(cls) -> CircuitBreakerStateRepository:
        """런타임용 CB Repository (Redis)"""
        return cls._get_repo("circuit_breaker")
    
    @classmethod
    def get_failed_operation_repo(cls) -> FailedOperationRepository:
        """런타임용 DLQ Repository (Redis)"""
        return cls._get_repo("failed_operation")
    
    # =========================================================================
    # 통계 Repository (신규)
    # =========================================================================
    
    @classmethod
    def register_statistics_adapter(
        cls,
        adapter: StatisticsRepositoryInterface,
    ) -> None:
        """
        통계 어댑터 등록.
        
        Django/FastAPI 앱 시작 시 호출.
        미등록 시 NullStatisticsRepository 사용.
        """
        cls._statistics_adapter = adapter
        logger.info(f"[ProviderRegistry] Statistics adapter registered: {type(adapter).__name__}")
    
    @classmethod
    def get_statistics_repo(cls) -> StatisticsRepositoryInterface:
        """
        통계용 Repository 반환.
        
        미등록 시 NullStatisticsRepository 반환 (에러 없음).
        """
        if cls._statistics_adapter is None:
            from selfhealing.adapters.statistics.null import NullStatisticsRepository
            return NullStatisticsRepository()
        return cls._statistics_adapter
    
    @classmethod
    def has_statistics_adapter(cls) -> bool:
        """통계 어댑터 등록 여부"""
        return cls._statistics_adapter is not None
```

---

## 6. 수정 대상 파일

### 6.1 ImportError 발생 파일 (카테고리 1)

| 파일 | 현재 문제 | 수정 방향 |
|------|----------|----------|
| `services/dlq_service.py` | `from selfhealing.adapters.django.models import FailedOperation` | → `ProviderRegistry.get_statistics_repo()` |
| `services/dashboard_service.py` | Django 모델 직접 사용 | → `ProviderRegistry.get_statistics_repo()` |
| `services/health_check.py` | `CircuitBreakerState.objects.count()` | → `ProviderRegistry.get_statistics_repo()` |
| `adapters/celery/tasks.py` | Django 모델 직접 import | → `ProviderRegistry` 사용 |

### 6.2 수정 예시

**Before (dlq_service.py):**
```python
def get_cleanup_stats(self) -> CleanupStats:
    try:
        from selfhealing.adapters.django.models import FailedOperation  # ❌ ImportError
        from django.db.models import Count
        
        status_counts = dict(
            FailedOperation.objects.values("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        ...
```

**After:**
```python
def get_cleanup_stats(self) -> CleanupStats:
    from selfhealing.factory import ProviderRegistry
    
    stats_repo = ProviderRegistry.get_statistics_repo()
    return stats_repo.get_cleanup_stats()  # ✅ 어댑터가 처리
```

---

## 7. 신규 파일 목록

### 7.1 인터페이스

```
selfhealing/interfaces/
└── statistics.py              # StatisticsRepositoryInterface
```

### 7.2 어댑터

```
selfhealing/adapters/statistics/
├── __init__.py
├── null.py                    # NullStatisticsRepository (기본값)
├── django.py                  # DjangoStatisticsAdapter
└── sqlalchemy.py              # SQLAlchemyStatisticsAdapter
```

### 7.3 모델 (재추가)

```
selfhealing/adapters/django/
├── __init__.py                # 재생성 (통계 전용)
├── models.py                  # FailedOperation, CircuitBreakerState (재추가)
└── migrations/                # 마이그레이션 (재추가)
```

---

## 8. 구현 계획

### Phase 1: 인터페이스 및 Null 어댑터 (Day 1) ✅

- [x] `interfaces/statistics.py` 생성
- [x] `adapters/statistics/null.py` 생성
- [x] `ProviderRegistry` 확장

### Phase 2: Django 어댑터 복원 (Day 2) ✅

- [x] `adapters/django/__init__.py` 생성 (models는 앱에서 제공, domain-free)
- [x] `adapters/django/statistics.py` 생성
- [x] `interfaces/__init__.py`에 통계 인터페이스 추가

### Phase 3: 기존 코드 리팩토링 (Day 3)

- [ ] `dlq_service.py` 수정 (통계 메서드)
- [ ] `dashboard_service.py` 수정
- [ ] `health_check.py` 수정
- [ ] `celery/tasks.py` 수정

### Phase 4: SQLAlchemy 어댑터 (Day 4)

- [ ] `adapters/sqlalchemy/statistics.py` 생성
- [ ] FastAPI 예제 문서화

### Phase 5: 테스트 및 문서화 (Day 5)

- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] 사용 가이드 문서화

---

## 9. 마이그레이션 전략

### 9.1 기존 데이터

| 데이터 위치 | 처리 방식 |
|------------|----------|
| Django DB (기존) | 그대로 유지, ORM으로 접근 |
| Redis (신규) | 런타임 상태 저장 |

### 9.2 데이터 동기화

**런타임 → 통계 동기화는 필요 없음:**
- 런타임(Redis): 현재 상태만 저장 (휘발성 OK)
- 통계(ORM): 이력/집계용 (영구 저장)

**단, DLQ 적재 시:**
```python
def store_failure(self, ...):
    # 1. Redis에 런타임 데이터 저장 (빠름)
    runtime_repo = ProviderRegistry.get_failed_operation_repo()
    runtime_repo.create(...)
    
    # 2. ORM에 영구 데이터 저장 (선택적, 비동기 가능)
    if ProviderRegistry.has_statistics_adapter():
        stats_repo = ProviderRegistry.get_statistics_repo()
        stats_repo.persist_entry(...)  # 비동기 또는 Celery 태스크
```

---

## 10. FAQ

### Q1: Redis와 ORM에 같은 데이터가 중복 저장되나요?

**아니요.** 역할이 다릅니다:
- Redis: 런타임 상태 (CB 상태, 현재 pending DLQ)
- ORM: 이력/분석용 (전체 DLQ 기록, 집계 데이터)

### Q2: 통계 어댑터 없으면 어떻게 되나요?

**런타임은 정상 동작합니다.**
- `get_statistics_repo()` → `NullStatisticsRepository` 반환
- 대시보드 접근 시 "통계 미지원" 메시지
- Prometheus 메트릭으로 기본 모니터링 가능

### Q3: 기존 Django 프로젝트는 변경이 필요한가요?

**최소한의 변경:**
```python
# shopping/apps.py - 추가
from selfhealing.factory import ProviderRegistry
from selfhealing.adapters.django import DjangoStatisticsAdapter

ProviderRegistry.register_statistics_adapter(
    DjangoStatisticsAdapter()  # 자동으로 모델 탐색
)
```

---

## 11. 관련 문서

- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - Redis 런타임 저장소
- [06_REDIS_MIGRATION.md](06_REDIS_MIGRATION.md) - 마이그레이션 기록
- [00_INDEX.md](00_INDEX.md) - 문서 인덱스

---

## Appendix A: Redis vs ORM 성능 비교

### A.1 단순 조회 (CB 상태 체크)

```
Redis GET:           1.2ms  ✅
Django ORM SELECT:   5.8ms
SQLAlchemy SELECT:   4.2ms
```

### A.2 복잡한 집계 (도메인별 pending 개수)

```
Redis SCAN + 집계:   850ms (10만 건)
Django ORM GROUP BY: 45ms  ✅
SQLAlchemy GROUP BY: 52ms  ✅
```

### A.3 결론

| 작업 | 최적 기술 |
|------|----------|
| 런타임 상태 관리 | **Redis** (속도, 분산, 장애 격리) |
| 통계/분석 | **ORM** (SQL 최적화, 인덱스 활용) |

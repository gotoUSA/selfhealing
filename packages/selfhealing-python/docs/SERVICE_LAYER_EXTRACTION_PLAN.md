# 서비스 레이어 분리 실행 계획

> 생성일: 2025-12-19  
> 목적: View 파일에서 비즈니스 로직을 Service 레이어로 분리

---

## 📊 분석 결과 요약

| 파일 | 현재 상태 | 우선순위 | 권장 서비스 |
|------|-----------|----------|-------------|
| `views/circuit_breaker.py` | ✅ 이미 분리됨 | 낮음 | 파일 분리만 필요 |
| `views/dashboard.py` | ⚠️ **분리 필요** | 🔴 높음 | `DashboardService` |
| `views/dlq.py` | ⚠️ **분리 필요** | 🔴 높음 | `DLQService` |
| `views/health.py` | ⚠️ 부분 분리 필요 | 🟡 중간 | `HealthCheckService` |
| `views/system_control.py` | ✅ 이미 분리됨 | 낮음 | 파일 분리만 필요 |
| `views/config.py` | ✅ 이미 분리됨 | - | RuntimeConfigManager 사용 중 |

---

## 🎯 작업 순서

### Phase 1: DLQService 생성 (높은 우선순위)

**대상 파일**: `api/django/views/dlq.py`  
**생성 파일**: `services/dlq.py`

#### 분리할 비즈니스 로직:

| 메서드 | 설명 | 원본 View |
|--------|------|-----------|
| `replay(domain, batch_size)` | 배치 조회 및 재시도 실행 | `DLQReplayView` |
| `get_cleanup_stats()` | 상태별/기간별 카운트 | `DLQCleanupStatsView` |
| `archive_old_entries(older_than_days)` | 오래된 항목 아카이브 | `DLQArchiveView` |
| `purge_archived(ids, older_than_days)` | 아카이브 영구 삭제 | `DLQPurgeView` |
| `list_entries(filters, page, page_size)` | 페이지네이션/필터링 | `DLQListView` |
| `get_entry(pk)` | 단일 항목 조회 | `DLQDetailView` |
| `retry_entry(pk)` | 개별 항목 재시도 | `DLQRetryView` |
| `resolve_entry(pk, notes)` | 수동 해결 처리 | `DLQResolveView` |
| `create_test_entry(**kwargs)` | 테스트용 항목 생성 | `DLQTestCreateView` |

#### 구현 템플릿:

```python
# services/dlq.py
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

@dataclass
class ReplayResult:
    processed: int
    success: int
    failed: int
    errors: List[str]

@dataclass
class CleanupStats:
    total: int
    by_status: Dict[str, int]
    by_period: Dict[str, int]

class DLQService:
    """Dead Letter Queue 비즈니스 로직 서비스"""
    
    def replay(self, domain: Optional[str] = None, batch_size: int = 10) -> ReplayResult:
        """배치 재시도 실행"""
        pass
    
    def get_cleanup_stats(self) -> CleanupStats:
        """정리 통계 조회"""
        pass
    
    def archive_old_entries(self, older_than_days: int = 30) -> int:
        """오래된 resolved 항목 아카이브"""
        pass
    
    def purge_archived(
        self, 
        ids: Optional[List[int]] = None, 
        older_than_days: Optional[int] = None
    ) -> int:
        """아카이브된 항목 영구 삭제"""
        pass
    
    def list_entries(
        self, 
        filters: Dict[str, Any], 
        page: int = 1, 
        page_size: int = 20
    ) -> Dict[str, Any]:
        """목록 조회 (페이지네이션)"""
        pass
    
    def get_entry(self, pk: int) -> Any:
        """단일 항목 조회"""
        pass
    
    def retry_entry(self, pk: int) -> Dict[str, Any]:
        """개별 항목 재시도"""
        pass
    
    def resolve_entry(self, pk: int, notes: str = "") -> Any:
        """수동 해결 처리"""
        pass
    
    def create_test_entry(self, **kwargs) -> Any:
        """테스트용 항목 생성"""
        pass
```

---

### Phase 2: DashboardService 생성 (높은 우선순위)

**대상 파일**: `api/django/views/dashboard.py`  
**생성 파일**: `services/dashboard.py`

#### 분리할 비즈니스 로직:

| 메서드 | 설명 |
|--------|------|
| `get_summary()` | 대시보드 전체 요약 |
| `get_status_counts()` | 상태별 카운트 (pending, processing, resolved, failed) |
| `get_recent_activity(hours, days)` | 최근 활동 통계 |
| `get_distribution_by_domain()` | 도메인별 분포 |
| `get_distribution_by_failure_type()` | 실패유형별 분포 |
| `calculate_resolution_rate()` | 해결율 계산 |
| `calculate_avg_retry_count()` | 평균 재시도 횟수 |
| `determine_health_status(pending, failed)` | 헬스 상태 판단 |

#### 구현 템플릿:

```python
# services/dashboard.py
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class DashboardSummary:
    status_counts: Dict[str, int]
    recent_activity: Dict[str, int]
    distribution_by_domain: Dict[str, int]
    distribution_by_failure_type: Dict[str, int]
    resolution_rate: float
    avg_retry_count: float
    health_status: str

class DashboardService:
    """대시보드 통계 서비스"""
    
    def get_summary(self) -> DashboardSummary:
        """대시보드 전체 요약 반환"""
        pass
    
    def get_status_counts(self) -> Dict[str, int]:
        """상태별 카운트"""
        pass
    
    def get_recent_activity(self, hours: int = 24, days: int = 7) -> Dict[str, int]:
        """최근 활동 통계"""
        pass
    
    def get_distribution_by_domain(self) -> Dict[str, int]:
        """도메인별 분포"""
        pass
    
    def get_distribution_by_failure_type(self) -> Dict[str, int]:
        """실패유형별 분포"""
        pass
    
    def calculate_resolution_rate(self) -> float:
        """해결율 계산 (0.0 ~ 1.0)"""
        pass
    
    def calculate_avg_retry_count(self) -> float:
        """평균 재시도 횟수"""
        pass
    
    def determine_health_status(self, pending: int, failed: int) -> str:
        """헬스 상태 판단 (healthy, warning, critical)"""
        pass
```

---

### Phase 3: HealthCheckService 생성 (중간 우선순위)

**대상 파일**: `api/django/views/health.py`  
**생성 파일**: `services/health_check.py`

#### 분리할 비즈니스 로직:

| 메서드 | 설명 |
|--------|------|
| `check_database()` | 기본 DB 연결 확인 |
| `check_all_databases()` | 모든 DB 연결 확인 |
| `check_connection_pool()` | 커넥션 풀 상태 조회 |
| `get_overall_health()` | 전체 시스템 헬스 |

---

### Phase 4: 기존 서비스 파일 분리 (낮은 우선순위)

#### 4.1 CircuitBreakerManager 분리

- **현재 위치**: `views/circuit_breaker.py` 내부에 클래스로 정의
- **이동 위치**: `services/circuit_breaker.py`
- **작업**: 클래스를 별도 파일로 이동, View에서 import

#### 4.2 SystemControlService 분리

- **현재 위치**: `views/system_control.py` 내부에 클래스로 정의
- **이동 위치**: `services/system_control.py`
- **작업**: 클래스를 별도 파일로 이동, View에서 import

---

## 📁 최종 services 디렉토리 구조

```
services/
├── __init__.py
├── circuit_breaker.py      # Phase 4 - CircuitBreakerManager 이동
├── dashboard.py            # Phase 2 - NEW
├── dlq.py                  # Phase 1 - NEW
├── health_check.py         # Phase 3 - NEW
├── pending_config.py       # 기존 (Config API)
├── runtime_config.py       # 기존 (Config API)
└── system_control.py       # Phase 4 - SystemControlService 이동
```

---

## ✅ 체크리스트

### Phase 1: DLQService ✅ 완료 (2025-12-19)
- [x] `services/dlq_service.py` 파일 확장 (기존 파일에 API 비즈니스 로직 추가)
- [x] ReplayResult, CleanupStats, PaginatedResult, RetryResult, ResolveResult 데이터클래스 정의
- [x] DLQService 클래스에 API 비즈니스 로직 메서드 추가:
  - `replay(domain, batch_size)` - 배치 재시도 실행
  - `get_cleanup_stats()` - 정리 통계 조회
  - `archive_old_entries(older_than_days)` - 오래된 항목 아카이브
  - `purge_archived(ids, older_than_days)` - 아카이브 영구 삭제
  - `list_entries(filters, page, page_size)` - 페이지네이션/필터링
  - `get_entry(pk)` - 단일 항목 조회
  - `retry_entry(pk)` - 개별 항목 재시도
  - `resolve_entry(pk, notes)` - 수동 해결 처리
  - `create_test_entry(**kwargs)` - 테스트용 항목 생성
- [x] `views/dlq.py`에서 DLQService 사용하도록 리팩토링
- [ ] 테스트 작성/수정

### Phase 2: DashboardService
- [ ] `services/dashboard.py` 파일 생성
- [ ] DashboardSummary 데이터클래스 정의
- [ ] DashboardService 클래스 구현
- [ ] `views/dashboard.py`에서 DashboardService 사용하도록 리팩토링
- [ ] 테스트 작성/수정

### Phase 3: HealthCheckService
- [ ] `services/health_check.py` 파일 생성
- [ ] HealthCheckService 클래스 구현
- [ ] `views/health.py`에서 HealthCheckService 사용하도록 리팩토링
- [ ] 테스트 작성/수정

### Phase 4: 기존 서비스 파일 분리
- [ ] CircuitBreakerManager를 `services/circuit_breaker.py`로 이동
- [ ] SystemControlService를 `services/system_control.py`로 이동
- [ ] View 파일들에서 import 경로 수정
- [ ] 테스트 확인

---

## 🔧 실행 명령

다음 세션에서 아래 명령으로 시작:

```
Phase 1부터 진행해줘 - DLQService 생성
```

또는 전체 진행:

```
SERVICE_LAYER_EXTRACTION_PLAN.md 문서 기반으로 서비스 레이어 분리 진행해줘
```

---

## 📝 참고사항

1. **View는 얇게 유지**: Request/Response 처리만 담당
2. **Service에 비즈니스 로직 집중**: DB 접근, 계산, 외부 호출 등
3. **테스트 용이성 향상**: Service 단위 테스트 가능
4. **재사용성 증가**: 다른 View나 Task에서 Service 재사용 가능

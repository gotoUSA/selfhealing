# 41. Wrapper 시스템 리팩토링 계획

> **작성일**: 2026-01-16 (PART4 추가: 2026-01-17)  
> **상태**: Phase 4 진행 중 ⏳  
> **관련 문서**: PART1~PART4 상세 문서 참조

---

## 1. 개요

셀프힐링 시스템 내에서 확인된 **11개의 Wrapper 패턴**에 대한 리팩토링 계획입니다.

### 1.1 Wrapper 목록

| # | Wrapper 유형 | 파일 위치 | 줄 수 | 우선순위 |
|---|---|---|---|---|
| 1 | SafeGauge | `metrics/safe_gauge.py` | 565 | **높음** |
| 2 | Celery Task Wrappers | `tasks/chaos_scheduler.py` | 490 | 중간 |
| 3 | TLSResilientClient | `core/tls_handler.py` | 342 | 낮음 |
| 4 | ChaosAwareMetricsAdapter | `services/auto_tuning/chaos_aware_metrics.py` | 140 | 낮음 |
| 5 | with_retry Decorator | `services/retry_handler.py` | ~50 | 중간 |
| 6 | rate_limit_aware Decorator | `services/rate_limit_coordinator.py` | ~40 | 중간 |
| 7 | automation_gate Decorator | `services/error_budget_gate/gate.py` | ~20 | 낮음 |
| 8 | Metric Tracking Decorators | `metrics/decorators.py` | 232 | 중간 |
| 9 | with_jitter Decorator | `metrics/jitter.py` | 211 | 낮음 |
| 10 | track_replay Decorator | `services/metrics/updaters.py` | ~30 | 낮음 |
| 11 | MetricsProviderWrapper | `services/auto_tuning/service.py` | ~30 | 낮음 |

---

## 2. 상세 문서 구조

리팩토링 계획은 **4개 파트**로 분할:

| 파트 | 문서 | 내용 |
|---|---|---|
| PART 1 | [41_WRAPPER_REFACTORING_PART1.md](41_WRAPPER_REFACTORING_PART1.md) | SafeGauge 모듈화 (#1) |
| PART 2 | [41_WRAPPER_REFACTORING_PART2.md](41_WRAPPER_REFACTORING_PART2.md) | Task Wrapper 일관성 (#2, cleanup_tasks, daily_report) |
| PART 3 | [41_WRAPPER_REFACTORING_PART3.md](41_WRAPPER_REFACTORING_PART3.md) | Decorator 통합 및 기타 Wrapper (#3~#11) |
| PART 4 | [41_WRAPPER_REFACTORING_PART4.md](41_WRAPPER_REFACTORING_PART4.md) | 설계 보완 (LRU 캐시, Universal Async, 테스트 격리, 포렌식) |

---

## 3. 우선순위 근거

### 3.1 높음 (SafeGauge)

**코드 근거** (`metrics/safe_gauge.py`):

```python
# 565줄 단일 파일에 5개 책임 혼합:
class SyncStatus(Enum):           # 1. 동기화 상태 Enum
class SyncInfo:                   # 2. 동기화 정보 Dataclass (100줄+)
class SafeGaugeChild:             # 3. 라벨된 Gauge Child (150줄+)
class SafeGauge:                  # 4. 메인 Wrapper (100줄+)
class _NoOpGaugeChild:            # 5. No-op 구현
def clamp_non_negative(): ...     # 6. 유틸리티 함수들
def clamp_percentage(): ...
def safe_set_gauge(): ...
```

**문제점**:
- SRP (단일 책임 원칙) 위반
- 테스트 시 모든 의존성 로드 필요
- 동기화 로직 변경 시 전체 파일 영향

### 3.2 중간 (Task Wrapper 일관성)

**코드 근거 - 일관된 패턴** (`tasks/chaos_scheduler.py`):
```python
def run_scheduled_experiments() -> Dict[str, Any]:
    """
    This function is a thin wrapper that delegates to ChaosExecutionService.
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    service = get_chaos_execution_service()
    result = service.run_scheduled_experiments()
    return result.to_dict()
```

**코드 근거 - 불일치 패턴** (`tasks/cleanup_tasks.py`):
```python
class ArchiveOldDLQEntriesTask(BaseNotifyingTask):
    """클래스 기반 - 비즈니스 로직 직접 포함"""
    def run(self, older_than_days: int = 30) -> Dict[str, Any]:
        from selfhealing.services.dlq_service import get_dlq_service
        service = get_dlq_service()
        count = service.archive_old_entries(older_than_days=older_than_days)
        # ... 비즈니스 로직이 Task 내부에 있음
```

### 3.3 낮음 (기타 Wrapper)

- TLSResilientClient: ABC + 단일 구현체 (YAGNI 위반이지만 확장성 고려)
- ChaosAwareMetricsAdapter: Delegate 패턴 잘 적용됨 (140줄, 적정 크기)
- 데코레이터들: 각 30~50줄로 적정 크기

---

## 4. 리팩토링 원칙

### 4.1 Thin Task, Fat Service

```
┌─────────────────┐     ┌─────────────────────┐
│  Celery Task    │ ──▶ │   Service Layer     │
│  (Thin Wrapper) │     │   (Business Logic)  │
└─────────────────┘     └─────────────────────┘
       │                         │
       │  - 로깅만               │  - 거버넌스 체크
       │  - Audit 기록           │  - 에러 버짓 확인
       │  - 예외 재시도          │  - 실제 작업 수행
       │                         │  - 결과 반환
```

### 4.2 Wrapper 설계 원칙

1. **단일 책임**: 하나의 Wrapper는 하나의 책임만
2. **투명성**: 원본 인터페이스 유지
3. **확장성**: 새로운 기능 추가 시 기존 코드 수정 최소화
4. **테스트 용이성**: 각 구성요소 독립 테스트 가능

---

## 5. 진행 로드맵

```
Phase 1: SafeGauge 분리 (PART1) ✅ COMPLETED
├── Week 1: 모듈 구조 설계 ✅
├── Week 2: 코드 분리 및 테스트 ✅
└── Week 3: 기존 코드 마이그레이션 ✅
    - safe_gauge/ 패키지 생성: core.py, sync.py, clamping.py, noop.py
    - 180개 metrics 테스트 통과

Phase 2: Task Wrapper 일관성 (PART2) ✅ COMPLETED
├── Week 4: CleanupService 생성 ✅
│   - services/cleanup_service.py (276줄)
│   - CleanupResult dataclass
├── Week 5: cleanup_tasks 리팩토링 ✅
│   - 클래스 기반 → 함수 기반
│   - Thin Wrapper 패턴 적용
│   - 24개 테스트 통과
└── Week 6: daily_report 리팩토링 ✅
    - services/daily_report/ 패키지 생성
      - models.py: TaskResultEntry, DailyAutonomousReport
      - formatters.py: format_report_for_slack, format_report_for_email
      - aggregator.py: DailyReportCollector
      - service.py: DailyReportService
    - tasks/daily_report.py: 638줄 → 166줄 (74% 감소)

Phase 3: Decorator 통합 (PART3) ✅ COMPLETED
├── track_replay 중복 제거 ✅
│   - metrics/decorators.py에 통합 (domain + replay_type 지원)
│   - services/metrics/updaters.py deprecated 처리
├── automation_gate 개선 ✅
│   - @functools.wraps 적용
│   - 수동 __name__/__doc__ 복사 제거
├── with_jitter 위치 이동 ✅
│   - metrics/jitter.py → utils/jitter.py 이동
│   - 기존 위치 backward compatibility 유지 (DeprecationWarning)
│   - utils/__init__.py에 export 추가
└── MetricsProviderWrapper 분리 ✅
    - services/auto_tuning/metrics_provider.py 생성
    - service.py 내부 클래스 → 외부 클래스로 분리

Phase 4: 설계 보완 (PART4) ⏳ IN PROGRESS
├── SafeGauge LRU 캐시 ⏳
│   - OrderedDict 기반 max_label_combinations
│   - Eviction 메트릭 및 콜백
├── Universal Async Decorators ⏳
│   - track_replay, automation_gate, track_dlq_* async 지원
│   - utils/decorator_utils.py 헬퍼
├── Task Layer exc_info 일관성 ⏳
│   - cleanup_tasks.py exc_info=True 추가
│   - daily_report.py exc_info=True 추가
├── ProviderRegistry 테스트 격리 ⏳
│   - override_provider Context Manager
│   - isolated_test_context
└── Audit 포렌식 필드 ⏳
    - blocked_request_trace_id 추가
    - actor_roles_at_block 추가
```

---

## 6. 성공 지표

| 지표 | 현재 | 목표 | 상태 |
|---|---|---|---|
| safe_gauge.py 줄 수 | 565 | < 150 (core.py) | ✅ 완료 |
| Task 패턴 일관성 | 60% | 100% | ✅ 완료 (cleanup_tasks, daily_report) |
| 테스트 커버리지 (metrics/) | - | > 90% | ✅ 180개 테스트 통과 |
| 데코레이터 중복 | 2개 (track_replay) | 1개 | ✅ 완료 |
| with_jitter 위치 | metrics/ | utils/ | ✅ 완료 |
| MetricsProviderWrapper 위치 | 내부 클래스 | 외부 파일 | ✅ 완료 |
| SafeGauge 메모리 관리 | 없음 | LRU 캐시 | ⏳ PART4 |
| Decorator 비동기 지원 | 부분적 | Universal | ⏳ PART4 |
| Task exc_info 일관성 | 부분적 | 100% | ⏳ PART4 |
| ProviderRegistry 테스트 격리 | 없음 | Context Manager | ⏳ PART4 |
| Audit 포렌식 필드 | trace_id 자동 | 명시적 포함 | ⏳ PART4 |

---

## 7. 참조

- [PART 1: SafeGauge 모듈화](41_WRAPPER_REFACTORING_PART1.md)
- [PART 2: Task Wrapper 일관성](41_WRAPPER_REFACTORING_PART2.md)
- [PART 3: Decorator 통합](41_WRAPPER_REFACTORING_PART3.md)
- [PART 4: 설계 보완](41_WRAPPER_REFACTORING_PART4.md)

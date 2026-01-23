# Phase 3: Re-export 정리

> **상위 문서**: [80_DEPRECATION_CLEANUP_MASTER_PLAN.md](80_DEPRECATION_CLEANUP_MASTER_PLAN.md)  
> **예상 작업 시간**: 8-12시간  
> **난이도**: 상

---

## 1. Re-export 분류

### 1.1 유지해야 할 Re-export (건드리지 않음)

| 파일 | 용도 | 유지 이유 |
|------|------|----------|
| `selfhealing/services/__init__.py` | Public API 진입점 | 패키지 표준 패턴 |
| `selfhealing/services/metrics/__init__.py` | 메트릭 패키지 facade | 의도적 설계 |
| `selfhealing/audit/integrity/__init__.py` | 무결성 패키지 facade | 의도적 설계 |
| `selfhealing/services/chaos/experiments/__init__.py` | Chaos 실험 facade | 의도적 설계 |
| `selfhealing/services/runtime_config/__init__.py` | 런타임 설정 facade | 의도적 설계 |
| `selfhealing/api/django/views/__init__.py` | View facade (Lazy) | URL 라우팅 호환 |
| `shopping/admin/__init__.py` | Django Admin 표준 | Django 표준 패턴 |

### 1.2 정리 대상 Re-export (제거)

| 파일 | 원본 | 정리 방법 |
|------|------|----------|
| `shopping/tasks/self_healing_tasks.py` | `selfhealing.celery_tasks` | 직접 import로 전환 |
| `shopping/tasks/dlq_replay_tasks.py` | `selfhealing.celery_tasks` | 직접 import로 전환 |
| `shopping/tasks/drift_detection_tasks.py` | `selfhealing.celery_tasks` | 직접 import로 전환 |
| `shopping/models/point_history.py` | `shopping.models.point` | 직접 import로 전환 |

### 1.3 검토 후 결정할 Wrapper 모듈

| 파일 | 원본 패키지 | 결정 |
|------|------------|------|
| `selfhealing/services/dlq_service.py` | `selfhealing/services/dlq/` | ⚠️ 유지 (외부 사용) |
| `selfhealing/services/audit_helpers.py` | `selfhealing/services/audit/` | ⚠️ 유지 (외부 사용) |
| `selfhealing/services/circuit_breaker_service.py` | `selfhealing/services/circuit_breaker/` | ⚠️ 유지 (외부 사용) |
| `selfhealing/services/error_budget_service.py` | `selfhealing/services/error_budget/` | ⚠️ 유지 (외부 사용) |
| `selfhealing/core/config.py` | `selfhealing/settings/` | ⚠️ 유지 (설정 레이어) |
| `selfhealing/api/django/views/l2_storage.py` | 개별 l2_storage_*.py | ⚠️ 유지 (편의성) |

---

## 2. 실행 순서

### Step 3.1: 사용처 분석

```bash
# shopping/tasks re-export 사용처
grep -rn "from shopping.tasks.self_healing_tasks import" --include="*.py"
grep -rn "from shopping.tasks.dlq_replay_tasks import" --include="*.py"
grep -rn "from shopping.tasks.drift_detection_tasks import" --include="*.py"

# shopping/models/point_history 사용처
grep -rn "from shopping.models.point_history import" --include="*.py"
grep -rn "from shopping.models import.*PointHistory" --include="*.py"
```

### Step 3.2: shopping 앱 Re-export 제거

#### 3.2.1 self_healing_tasks.py 정리

**변경 전후 매핑**:

| Before | After |
|--------|-------|
| `from shopping.tasks.self_healing_tasks import hunt_zombie_experiments` | `from selfhealing.celery_tasks import hunt_zombie_experiments` |
| `from shopping.tasks.self_healing_tasks import check_circuit_breaker_recovery` | `from selfhealing.celery_tasks import check_circuit_breaker_recovery` |

**작업 순서**:
1. 사용처 검색
2. 모든 사용처를 직접 import로 변경
3. `shopping/tasks/self_healing_tasks.py` 파일 제거 또는 deprecation 경고 추가

#### 3.2.2 dlq_replay_tasks.py 정리

| Before | After |
|--------|-------|
| `from shopping.tasks.dlq_replay_tasks import replay_single_dlq_entry` | `from selfhealing.celery_tasks import replay_single_dlq_entry` |
| `from shopping.tasks.dlq_replay_tasks import replay_on_circuit_breaker_close` | `from selfhealing.celery_tasks import conditional_replay_on_circuit_close` |

**주의**: `replay_on_circuit_breaker_close`는 별칭으로, 실제 이름 `conditional_replay_on_circuit_close` 사용

#### 3.2.3 drift_detection_tasks.py 정리

| Before | After |
|--------|-------|
| `from shopping.tasks.drift_detection_tasks import check_sla_drift` | `from selfhealing.celery_tasks import check_sla_drift` |

#### 3.2.4 point_history.py 정리

| Before | After |
|--------|-------|
| `from shopping.models.point_history import PointHistory` | `from shopping.models.point import PointHistory` |

### Step 3.3: Celery Beat 설정 확인

`shopping/tasks/` re-export 제거 시 Celery Beat 설정 확인 필요:

```bash
grep -rn "self_healing_tasks\|dlq_replay_tasks\|drift_detection_tasks" --include="*.py" --include="*.yaml" --include="*.yml"
```

**확인 파일**:
- `myproject/celery.py`
- `myproject/settings/*.py`
- `docker-compose*.yml`

### Step 3.4: 통합 테스트

```bash
# Task 관련 테스트
pytest tests/ -k "task" -v

# Import 테스트
python -c "from selfhealing.celery_tasks import hunt_zombie_experiments; print('OK')"
```

---

## 3. 상세 파일별 작업

### 3.1 shopping/tasks/self_healing_tasks.py

| 작업 | 설명 |
|------|------|
| 사용처 검색 | 모든 import 확인 |
| 사용처 변경 | 직접 import로 수정 |
| 파일 처리 | DeprecationWarning 추가 후 유지 또는 제거 |

### 3.2 shopping/tasks/dlq_replay_tasks.py

| 작업 | 설명 |
|------|------|
| 사용처 검색 | 별칭 `replay_on_circuit_breaker_close` 포함 |
| 사용처 변경 | 직접 import + 실제 함수명 사용 |
| 파일 처리 | 제거 또는 deprecation |

### 3.3 shopping/tasks/drift_detection_tasks.py

| 작업 | 설명 |
|------|------|
| 사용처 검색 | 2개 함수만 있음 |
| 사용처 변경 | 직접 import |
| 파일 처리 | 제거 |

### 3.4 shopping/models/point_history.py

| 작업 | 설명 |
|------|------|
| 사용처 검색 | PointHistory import 경로 확인 |
| 사용처 변경 | `shopping.models.point` 사용 |
| 파일 처리 | 제거 |

---

## 4. Wrapper 모듈 유지 (선택적 정리)

### 4.1 유지하되 문서화

다음 모듈들은 외부 호환성을 위해 유지하되, 새 코드에서는 직접 패키지 사용 권장:

| Wrapper 모듈 | 권장 Import |
|--------------|------------|
| `selfhealing.services.dlq_service` | `selfhealing.services.dlq` |
| `selfhealing.services.audit_helpers` | `selfhealing.services.audit` |
| `selfhealing.services.circuit_breaker_service` | `selfhealing.services.circuit_breaker` |
| `selfhealing.services.error_budget_service` | `selfhealing.services.error_budget` |

### 4.2 문서 업데이트

각 wrapper 모듈의 docstring에 명확한 안내 추가:

```python
"""
⚠️ COMPATIBILITY LAYER

이 모듈은 하위 호환성을 위해 유지됩니다.
새 코드에서는 직접 패키지 import를 권장합니다:

    # 권장
    from selfhealing.services.dlq import DLQService
    
    # 하위 호환 (유지되지만 권장하지 않음)
    from selfhealing.services.dlq_service import DLQService
"""
```

---

## 5. 완료 체크리스트

### 5.1 shopping 앱 Re-export

- [x] `self_healing_tasks.py` DeprecationWarning 추가 완료
- [x] `dlq_replay_tasks.py` DeprecationWarning 추가 완료
- [x] `drift_detection_tasks.py` DeprecationWarning 추가 완료
- [x] `point_history.py` DeprecationWarning 추가 완료
- [x] 테스트 파일 직접 import로 변경 완료
- [x] 파일 deprecation 추가 (제거는 v3.0.0 예정)

### 5.2 Wrapper 모듈

- [x] 유지 결정 (외부 호환성)
- [x] 권장 import 안내 docstring 확인됨

### 5.3 테스트

- [x] chaos 테스트 통과 (24 + 23 = 47개)
- [x] DeprecationWarning 정상 발생 확인
- [x] Import 구조 변경 검증 완료

---

## 6. 주의사항

### 6.1 Breaking Change 가능성

`shopping/tasks/*.py` 제거 시:
- Celery worker가 해당 경로로 task를 찾을 수 있음
- 반드시 Celery Beat 설정 업데이트 필요

### 6.2 권장 접근법

1. **즉시 제거** 대신 **deprecation 경고 추가**
2. 충분한 마이그레이션 기간 후 제거
3. 또는 파일 유지 + 내부에서 직접 import 호출

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 1.0 | 2026-01-23 | 초안 작성 |
| 1.1 | 2026-01-23 | Phase 3 완료 - Re-export 모듈에 DeprecationWarning 추가, 테스트 파일 직접 import로 변경 |

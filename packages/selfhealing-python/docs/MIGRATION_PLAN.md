# Self-Healing SaaS 전환 마이그레이션 계획

> **Created**: 2025-12-10  
> **Status**: TODO  
> **Branch**: feature/self-healing-extraction

---

## 📊 현황 요약

| 구분 | 파일 수 | import 수 | 비고 |
|------|---------|-----------|------|
| **self_healing 내부** | 27개 | - | 삭제 대상 |
| **외부 핵심 코드** | 8개 | ~20개 | 수정 필요 |
| **테스트 코드** | 28개 | ~150개 | 수정 필요 |
| **총계** | 62개 | 170개 | |

---

## 🚀 3단계 마이그레이션 계획

```
Phase 1: 핵심 코드 전환 (8개 파일)     ← 예상 30분
Phase 2: 테스트 코드 전환 (28개 파일)  ← 예상 1시간
Phase 3: 정리 및 검증                  ← 예상 30분
```

---

## Phase 1: 핵심 코드 전환

### 대상 파일 (8개)

| # | 파일 | 작업 | 우선순위 |
|---|------|------|----------|
| 1 | `shopping/admin/circuit_breaker_admin.py` | import 변경 | 🔴 |
| 2 | `shopping/admin/dlq_admin.py` | import 변경 | 🔴 |
| 3 | `shopping/models/failed_operation.py` | 패키지로 이동 or import 변경 | 🔴 |
| 4 | `shopping/tasks/dlq_replay_tasks.py` | import 변경 | 🔴 |
| 5 | `shopping/tasks/self_healing_tasks.py` | import 변경 | 🔴 |
| 6 | `shopping/views/self_healing_views.py` | import 변경 | 🔴 |
| 7 | `shopping/management/commands/generate_self_healing_alerts.py` | import 변경 | 🟡 |
| 8 | `shopping/management/commands/security_review.py` | import 변경 | 🟡 |

### 변환 규칙

```python
# Before
from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService
from shopping.services.self_healing.dlq_service import DLQService, store_to_dlq
from shopping.services.self_healing.replay_service import ReplayService
from shopping.services.self_healing.retry_handler import RetryHandler
from shopping.services.self_healing.backoff_calculator import BackoffCalculator
from shopping.services.self_healing.config import SLAThresholds

# After
from selfhealing.services import CircuitBreakerService, DLQService, store_to_dlq
from selfhealing.services import ReplayService, RetryHandler
from selfhealing.core import BackoffCalculator, SLAThresholds
```

---

## Phase 2: 테스트 코드 전환

### 대상 폴더 (28개 파일)

| 폴더 | 파일 수 | 처리 방법 |
|------|---------|-----------|
| `shopping/tests/unit/self_healing/` | 11개 | sed 일괄 변환 |
| `shopping/tests/integration/self_healing/` | 13개 | sed 일괄 변환 |
| `shopping/tests/integration/chaos/` | 4개 | sed 일괄 변환 |
| `shopping/tests/e2e/self_healing/` | 1개 | sed 일괄 변환 |
| `shopping/tests/e2e/` | 1개 | 개별 확인 |

### 일괄 변환 명령어

```bash
# 1. import 문 일괄 변환
find shopping/tests -name "*.py" -exec sed -i \
  's/from shopping\.services\.self_healing/from selfhealing/g' {} \;

# 2. 변환 결과 확인
grep -r "from shopping.services.self_healing" shopping/tests --include="*.py"
```

### 테스트 이동 (선택사항)

```bash
# 테스트도 패키지로 이동할 경우
mv shopping/tests/unit/self_healing/* packages/selfhealing-python/tests/unit/
mv shopping/tests/integration/self_healing/* packages/selfhealing-python/tests/integration/
```

---

## Phase 3: 정리 및 검증

### 3.1 폴더 삭제

```bash
# self_healing 서비스 폴더 삭제
rm -rf shopping/services/self_healing/

# 빈 테스트 폴더 정리 (이동한 경우)
rm -rf shopping/tests/unit/self_healing/
rm -rf shopping/tests/integration/self_healing/
rm -rf shopping/tests/e2e/self_healing/
```

### 3.2 검증

```bash
# 1. 남은 import 확인 (0개여야 함)
grep -r "from shopping.services.self_healing" . --include="*.py" | grep -v __pycache__

# 2. 테스트 실행
pytest packages/selfhealing-python/tests/ -v

# 3. 쇼핑몰 테스트 실행 (self-healing 의존 부분)
pytest shopping/tests/ -k "self_healing or circuit_breaker or dlq" -v
```

### 3.3 의존성 설정

```toml
# myproject/pyproject.toml 에 추가
[project]
dependencies = [
    "selfhealing @ file:packages/selfhealing-python",
    # 또는 PyPI 배포 후
    # "selfhealing>=0.1.0",
]
```

---

## 📁 최종 구조

```
myproject/
├── shopping/                      # 쇼핑몰 앱 (self_healing 폴더 없음)
│   ├── admin/
│   ├── models/
│   ├── views/
│   ├── tasks/
│   └── tests/                     # 쇼핑몰 테스트만
│
├── packages/
│   └── selfhealing-python/        # 독립 패키지
│       ├── src/selfhealing/
│       ├── tests/                 # self-healing 테스트
│       └── docs/
│
└── docs/                          # 쇼핑몰 문서만
```

---

## ⚠️ 주의사항

1. **Django 모델 의존성**: `FailedOperation`, `CircuitBreakerState` 모델이 Django에 의존
   - 옵션 A: 모델은 shopping에 유지, 서비스만 패키지
   - 옵션 B: 패키지에 Django adapter로 모델 포함

2. **Celery 태스크**: `self_healing_tasks.py`가 Celery 앱에 등록되어 있음
   - 패키지의 tasks를 shopping의 celery app에 등록 필요

3. **Admin 등록**: circuit_breaker_admin, dlq_admin
   - 패키지에서 제공하거나 shopping에서 직접 등록

---

## 📅 실행 시점

- [ ] Phase 1 완료
- [ ] Phase 2 완료  
- [ ] Phase 3 완료
- [ ] 테스트 통과 확인
- [ ] 커밋 & PR

---

*Last Updated: 2025-12-10*

# Self-Healing 분리 작업 계획서 (세부 스프린트)

> **작성일**: 2025-12-10
> **기반 문서**: SELF_HEALING_EXTRACTION_PLAN.md
> **총 예상 기간**: 4-6주 → **12개 스프린트로 분할**

---

## 📋 개요

원본 계획서의 6개 Phase를 실제 작업 가능한 **12개 스프린트**로 분할했습니다.
각 스프린트는 **반나절~1일** 분량으로, 독립적으로 커밋/테스트 가능합니다.

---

## 🗓️ 스프린트 구성

| 스프린트 | Phase | 내용 | 예상 시간 | 난이도 |
|---------|-------|------|----------|--------|
| 1 | 0 | 준비 & 기준선 테스트 | 2-3시간 | ⭐ |
| 2 | 1-A | 인터페이스 정의 (Repository) | 3-4시간 | ⭐⭐ |
| 3 | 1-B | Django 어댑터 구현 | 4-5시간 | ⭐⭐⭐ |
| 4 | 2-A | DLQ/Replay 서비스 리팩토링 | 4-5시간 | ⭐⭐⭐ |
| 5 | 2-B | CircuitBreaker/Retry 리팩토링 | 4-5시간 | ⭐⭐⭐ |
| 6 | 2-C | Factory 패턴 & 기존 함수 연결 | 3-4시간 | ⭐⭐ |
| 7 | 3 | 새 패키지 구조 생성 | 3-4시간 | ⭐⭐ |
| 8 | 4-A | Core 모듈 마이그레이션 | 4-6시간 | ⭐⭐⭐ |
| 9 | 4-B | Adapters/API 마이그레이션 | 4-6시간 | ⭐⭐⭐ |
| 10 | 4-C | 테스트 마이그레이션 | 4-6시간 | ⭐⭐⭐ |
| 11 | 5 | 쇼핑몰 통합 & import 변경 | 3-4시간 | ⭐⭐ |
| 12 | 6 | 정리, 문서화, 최종 검증 | 3-4시간 | ⭐⭐ |

---

## 📌 스프린트 1: 준비 & 기준선 테스트

**목표**: 분리 작업 전 안정적인 기준선 확보

### 작업 목록

- [ ] 1.1 새 브랜치 생성
  ```bash
  git checkout -b feature/self-healing-extraction
  ```

- [ ] 1.2 전체 테스트 실행 & 결과 기록
  ```bash
  pytest shopping/tests/ -v --tb=short > baseline_test_results.txt
  ```

- [ ] 1.3 현재 테스트 커버리지 기록
  ```bash
  pytest --cov=shopping --cov-report=html shopping/tests/
  # htmlcov/ 백업
  ```

- [ ] 1.4 분리 대상 파일 목록 검증
  - `shopping/services/self_healing/` 14개 파일 존재 확인
  - `shopping/models/failed_operation.py` 등 모델 확인
  - `shopping/tasks/self_healing_tasks.py` 등 태스크 확인

- [ ] 1.5 의존성 분석 스크립트 실행 (선택)
  ```bash
  # 실제 import 관계 확인
  grep -r "from shopping.services.self_healing" shopping/ --include="*.py"
  grep -r "from shopping.models.failed" shopping/ --include="*.py"
  ```

### 완료 조건
- 모든 테스트 통과
- 커버리지 수치 기록됨
- 분리 대상 파일 목록 일치 확인

### 커밋 메시지
```
chore: prepare baseline for self-healing extraction

- Record test results and coverage
- Verify file list matches extraction plan
```

---

## 📌 스프린트 2: 인터페이스 정의 (Repository 패턴)

**목표**: Django 모델 의존성을 추상화할 인터페이스 정의

### 작업 목록

- [ ] 2.1 interfaces 폴더 생성
  ```
  shopping/services/self_healing/interfaces/
  ├── __init__.py
  └── repositories.py
  ```

- [ ] 2.2 FailedOperationRepository 인터페이스 정의
  ```python
  from abc import ABC, abstractmethod
  from dataclasses import dataclass
  from typing import Optional, List
  from datetime import datetime

  @dataclass
  class FailedOperationData:
      id: int
      domain: str
      failure_type: str
      status: str
      created_at: datetime
      context: dict
      error_message: str
      retry_count: int
      # ... 필요 필드
  ```

- [ ] 2.3 CircuitBreakerStateRepository 인터페이스 정의
  ```python
  @dataclass
  class CircuitBreakerStateData:
      service_name: str
      state: str  # 'closed', 'open', 'half_open'
      failure_count: int
      last_failure_at: Optional[datetime]
      # ...
  ```

- [ ] 2.4 SecurityIncidentRepository 인터페이스 정의

- [ ] 2.5 __init__.py에서 export

### 완료 조건
- 인터페이스 파일 생성됨
- 타입 힌트 완비
- 기존 테스트 여전히 통과 (아직 사용 안 함)

### 커밋 메시지
```
feat(self-healing): add repository interfaces for abstraction

- FailedOperationRepository interface
- CircuitBreakerStateRepository interface
- SecurityIncidentRepository interface
```

---

## 📌 스프린트 3: Django 어댑터 구현

**목표**: 인터페이스의 Django ORM 구현체 작성

### 작업 목록

- [ ] 3.1 adapters 폴더 생성
  ```
  shopping/services/self_healing/adapters/
  ├── __init__.py
  └── django_repositories.py
  ```

- [ ] 3.2 DjangoFailedOperationRepository 구현
  ```python
  from shopping.models.failed_operation import FailedOperation
  from ..interfaces.repositories import (
      FailedOperationRepository,
      FailedOperationData,
  )

  class DjangoFailedOperationRepository(FailedOperationRepository):
      def create(self, **kwargs) -> FailedOperationData:
          obj = FailedOperation.objects.create(**kwargs)
          return self._to_data(obj)

      def _to_data(self, obj) -> FailedOperationData:
          # Django 모델 → DataClass 변환
          ...
  ```

- [ ] 3.3 DjangoCircuitBreakerStateRepository 구현

- [ ] 3.4 DjangoSecurityIncidentRepository 구현

- [ ] 3.5 어댑터 단위 테스트 작성
  ```
  shopping/tests/unit/self_healing/test_django_repositories.py
  ```

### 완료 조건
- 어댑터가 인터페이스 계약 준수
- 어댑터 단위 테스트 통과
- 기존 테스트 여전히 통과

### 커밋 메시지
```
feat(self-healing): implement Django repository adapters

- DjangoFailedOperationRepository
- DjangoCircuitBreakerStateRepository
- DjangoSecurityIncidentRepository
- Add unit tests for adapters
```

---

## 📌 스프린트 4: DLQ/Replay 서비스 리팩토링

**목표**: dlq_service.py, replay_service.py가 Repository 인터페이스 사용

### 작업 목록

- [ ] 4.1 dlq_service.py 리팩토링
  - 생성자에서 Repository 주입받도록 변경
  - 직접 `FailedOperation.objects` 호출 제거
  - Repository 메서드 호출로 대체

- [ ] 4.2 replay_service.py 리팩토링
  - 동일한 패턴 적용

- [ ] 4.3 기존 get_dlq_service() 함수 임시 수정
  ```python
  def get_dlq_service():
      from .adapters.django_repositories import DjangoFailedOperationRepository
      return DLQService(repository=DjangoFailedOperationRepository())
  ```

- [ ] 4.4 관련 테스트 수정 (mock 주입)

- [ ] 4.5 통합 테스트 실행
  ```bash
  pytest shopping/tests/integration/self_healing/test_dlq_*.py -v
  ```

### 완료 조건
- DLQ 관련 테스트 모두 통과
- 직접 모델 import 제거됨

### 커밋 메시지
```
refactor(self-healing): DLQ services use repository pattern

- dlq_service.py now uses FailedOperationRepository
- replay_service.py now uses FailedOperationRepository
- Existing tests pass with DI
```

---

## 📌 스프린트 5: CircuitBreaker/Retry 리팩토링

**목표**: circuit_breaker_service.py, retry_handler.py 리팩토링

### 작업 목록

- [ ] 5.1 circuit_breaker_service.py 리팩토링 (1105줄 - 가장 큼)
  - CircuitBreakerStateRepository 주입
  - 직접 모델 호출 제거
  - ⚠️ 주의: 이 파일이 가장 복잡함

- [ ] 5.2 retry_handler.py 리팩토링

- [ ] 5.3 idempotency_service.py 검토
  - Redis 의존성은 유지 (graceful degradation 이미 적용됨)

- [ ] 5.4 security_violation_service.py 리팩토링
  - SecurityIncidentRepository 주입

- [ ] 5.5 security_notification_service.py 리팩토링

- [ ] 5.6 관련 테스트 수정 및 실행
  ```bash
  pytest shopping/tests/unit/self_healing/test_circuit_breaker_service.py -v
  pytest shopping/tests/integration/self_healing/test_circuit_breaker*.py -v
  ```

### 완료 조건
- CircuitBreaker 관련 테스트 모두 통과
- 모든 서비스가 Repository 패턴 사용

### 커밋 메시지
```
refactor(self-healing): CircuitBreaker services use repository pattern

- circuit_breaker_service.py uses CircuitBreakerStateRepository
- retry_handler.py uses FailedOperationRepository
- security services use SecurityIncidentRepository
```

---

## 📌 스프린트 6: Factory 패턴 & 기존 함수 연결

**목표**: Service Factory 생성 및 기존 코드와 호환 유지

### 작업 목록

- [ ] 6.1 factory.py 생성
  ```python
  # shopping/services/self_healing/factory.py
  
  from .adapters.django_repositories import (
      DjangoFailedOperationRepository,
      DjangoCircuitBreakerStateRepository,
      DjangoSecurityIncidentRepository,
  )
  from .dlq_service import DLQService
  from .circuit_breaker_service import CircuitBreakerService
  from .replay_service import ReplayService
  
  def create_dlq_service() -> DLQService:
      return DLQService(repository=DjangoFailedOperationRepository())
  
  def create_circuit_breaker_service() -> CircuitBreakerService:
      return CircuitBreakerService(
          repository=DjangoCircuitBreakerStateRepository()
      )
  
  # ... 기타 서비스
  ```

- [ ] 6.2 기존 get_*_service() 함수들이 factory 사용하도록 변경

- [ ] 6.3 __init__.py 업데이트
  - 새로운 factory 함수 export

- [ ] 6.4 전체 테스트 실행
  ```bash
  pytest shopping/tests/ -v --tb=short
  ```

- [ ] 6.5 쇼핑몰 코드에서 호출 검증
  - `from shopping.services.self_healing import get_dlq_service` 여전히 작동

### 완료 조건
- Factory 패턴 적용 완료
- 기존 API 호환성 유지
- 전체 테스트 통과

### 커밋 메시지
```
feat(self-healing): add service factory for DI

- Create factory.py with service creation functions
- Maintain backward compatibility with existing API
- All tests pass
```

---

## 📌 스프린트 7: 새 패키지 구조 생성

**목표**: 독립 PyPI 패키지 구조 준비 (아직 코드 이동 안 함)

### 작업 목록

- [ ] 7.1 새 저장소/폴더 생성
  ```bash
  # 옵션 A: 같은 저장소 내 별도 폴더
  mkdir -p packages/selfhealing-python
  
  # 옵션 B: 별도 저장소 (권장)
  # GitHub에서 selfhealing-python 저장소 생성
  ```

- [ ] 7.2 기본 패키지 구조 생성
  ```
  selfhealing-python/
  ├── pyproject.toml
  ├── README.md
  ├── LICENSE
  ├── src/
  │   └── selfhealing/
  │       ├── __init__.py
  │       ├── core/
  │       │   └── __init__.py
  │       ├── interfaces/
  │       │   └── __init__.py
  │       ├── adapters/
  │       │   ├── __init__.py
  │       │   └── django/
  │       │       └── __init__.py
  │       ├── api/
  │       │   └── __init__.py
  │       └── metrics/
  │           └── __init__.py
  └── tests/
      ├── unit/
      └── integration/
  ```

- [ ] 7.3 pyproject.toml 작성
  ```toml
  [project]
  name = "selfhealing"
  version = "0.1.0"
  description = "Self-Healing Reliability Layer for Python Applications"
  requires-python = ">=3.10"
  dependencies = []
  
  [project.optional-dependencies]
  django = ["django>=4.2"]
  celery = ["celery>=5.0"]
  prometheus = ["prometheus-client>=0.17"]
  all = ["selfhealing[django,celery,prometheus]"]
  ```

- [ ] 7.4 기본 README.md 작성

### 완료 조건
- 패키지 구조 생성됨
- pyproject.toml 유효함
- 빈 패키지로 설치 가능 (`pip install -e .`)

### 커밋 메시지
```
chore: initialize selfhealing-python package structure

- Create package skeleton
- Add pyproject.toml with optional dependencies
- Add README.md
```

---

## 📌 스프린트 8: Core 모듈 마이그레이션

**목표**: 프레임워크 독립적인 핵심 로직 이동

### 이동 대상 파일

| 원본 | 대상 |
|------|------|
| `backoff_calculator.py` | `selfhealing/core/backoff.py` |
| `config.py` (순수 로직 부분) | `selfhealing/core/config.py` |
| `forensic_context.py` | `selfhealing/core/forensic.py` |
| `metrics.py` | `selfhealing/metrics/prometheus.py` |
| `interfaces/*.py` | `selfhealing/interfaces/*.py` |

### 작업 목록

- [ ] 8.1 core/backoff.py 마이그레이션
  - Django 의존성 없는 순수 Python
  - 테스트도 함께 이동

- [ ] 8.2 core/config.py 마이그레이션
  - 설정 로딩 로직 분리
  - Django settings에서 읽는 부분은 adapters로

- [ ] 8.3 interfaces/ 마이그레이션
  - 스프린트 2에서 만든 인터페이스 이동

- [ ] 8.4 metrics/ 마이그레이션
  - Prometheus 메트릭 정의

- [ ] 8.5 core/types.py 생성
  - 공통 타입 정의 (Enum, TypedDict 등)

- [ ] 8.6 새 패키지 단위 테스트 실행
  ```bash
  cd selfhealing-python
  pytest tests/unit/ -v
  ```

### 완료 조건
- Core 모듈이 Django 없이 테스트 가능
- 원본 프로젝트 테스트 여전히 통과 (아직 import 안 바꿈)

### 커밋 메시지
```
feat(selfhealing): migrate core modules

- backoff calculator
- config management
- interfaces
- prometheus metrics
- common types
```

---

## 📌 스프린트 9: Adapters/API 마이그레이션

**목표**: Django/Celery 어댑터 및 REST API 이동

### 이동 대상 파일

| 원본 | 대상 |
|------|------|
| `adapters/django_repositories.py` | `selfhealing/adapters/django/repositories.py` |
| `self_healing_views.py` | `selfhealing/api/django/views.py` |
| `self_healing_serializers.py` | `selfhealing/api/django/serializers.py` |
| Celery 태스크들 | `selfhealing/adapters/celery/tasks.py` |
| Admin 파일들 | `selfhealing/adapters/django/admin.py` |

### 작업 목록

- [ ] 9.1 adapters/django/ 마이그레이션
  - repositories.py
  - models.py (FailedOperation, CircuitBreakerState, SecurityIncident)
  - admin.py

- [ ] 9.2 adapters/celery/ 마이그레이션
  - tasks.py (self_healing_tasks, dlq_replay_tasks 통합)

- [ ] 9.3 api/django/ 마이그레이션
  - views.py
  - serializers.py
  - urls.py

- [ ] 9.4 Django 테스트 설정
  ```python
  # tests/integration/django/conftest.py
  import django
  from django.conf import settings
  
  def pytest_configure():
      settings.configure(
          DEBUG=True,
          DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', ...}},
          INSTALLED_APPS=['selfhealing.adapters.django', ...],
      )
      django.setup()
  ```

- [ ] 9.5 Django 어댑터 통합 테스트 실행

### 완료 조건
- 어댑터들이 새 패키지에서 작동
- Django 통합 테스트 통과

### 커밋 메시지
```
feat(selfhealing): migrate Django and Celery adapters

- Django repositories and models
- Django admin integration
- Celery tasks
- REST API views and serializers
```

---

## 📌 스프린트 10: 테스트 마이그레이션

**목표**: 모든 테스트를 새 패키지로 이동

### 이동 대상

| 분류 | 파일 수 | 대상 위치 |
|------|---------|-----------|
| Unit 테스트 | 15개 | `selfhealing/tests/unit/` |
| Integration 테스트 | 25개 | `selfhealing/tests/integration/` |
| Chaos 테스트 | 6개 | `selfhealing/tests/chaos/` |
| 일부 E2E 테스트 | 2개 | `selfhealing/tests/e2e/` |

### 작업 목록

- [ ] 10.1 Unit 테스트 이동
  - import 경로 수정
  - `from shopping.services.self_healing` → `from selfhealing`

- [ ] 10.2 Integration 테스트 이동
  - conftest.py 포함
  - Django 설정 테스트 환경 구성

- [ ] 10.3 Chaos 테스트 이동
  - conftest.py 포함

- [ ] 10.4 fixtures 및 factories 정리
  - 공통 fixtures를 패키지 conftest.py로

- [ ] 10.5 전체 테스트 실행
  ```bash
  cd selfhealing-python
  pytest tests/ -v --tb=short
  ```

### ⚠️ 쇼핑몰에 남겨야 할 테스트

- `test_l3_self_healing.py` - 전체 쇼핑몰 통합 테스트
- `test_user_invisible_flows.py` - 쇼핑몰 E2E 테스트
- L2 테스트들 (`test_l2_*.py`)

### 완료 조건
- 새 패키지 테스트 모두 통과
- 테스트 커버리지 유지

### 커밋 메시지
```
feat(selfhealing): migrate all tests

- Unit tests (15 files)
- Integration tests (25 files)
- Chaos tests (6 files)
- Update import paths
```

---

## 📌 스프린트 11: 쇼핑몰 통합 & import 변경

**목표**: 쇼핑몰이 새 패키지를 사용하도록 변경

### 작업 목록

- [ ] 11.1 requirements.txt 업데이트
  ```
  # 로컬 개발 시
  -e ../selfhealing-python[all]
  
  # 또는 배포 시
  selfhealing[django,celery,prometheus]>=0.1.0
  ```

- [ ] 11.2 import 경로 일괄 변경
  ```bash
  # 검색 & 치환
  find shopping/ -name "*.py" -exec sed -i \
    's/from shopping.services.self_healing/from selfhealing/g' {} \;
  ```

- [ ] 11.3 INSTALLED_APPS 추가
  ```python
  INSTALLED_APPS = [
      # ...
      'selfhealing.adapters.django',
  ]
  ```

- [ ] 11.4 settings 마이그레이션
  - `PAYMENT_RECOVERY` → `SELFHEALING` 설정 통합
  - 또는 selfhealing 패키지가 기존 설정 읽도록

- [ ] 11.5 Celery Beat 스케줄 업데이트
  ```python
  CELERY_BEAT_SCHEDULE = {
      'collect-self-healing-metrics': {
          'task': 'selfhealing.adapters.celery.tasks.collect_metrics',
          'schedule': 60.0,
      },
  }
  ```

- [ ] 11.6 URL 설정 업데이트
  ```python
  urlpatterns = [
      path('self-healing/', include('selfhealing.api.django.urls')),
  ]
  ```

- [ ] 11.7 전체 테스트 실행
  ```bash
  pytest shopping/tests/ -v --tb=short
  ```

### 완료 조건
- 쇼핑몰이 selfhealing 패키지 사용
- 모든 테스트 통과
- Admin 페이지 정상

### 커밋 메시지
```
refactor: integrate selfhealing package into shopping mall

- Update requirements.txt
- Change all import paths
- Update INSTALLED_APPS
- Update Celery beat schedule
- Update URL configuration
```

---

## 📌 스프린트 12: 정리, 문서화, 최종 검증

**목표**: 최종 정리 및 배포 준비

### 작업 목록

- [ ] 12.1 쇼핑몰에서 이전 파일 삭제
  ```bash
  rm -rf shopping/services/self_healing/
  rm shopping/models/failed_operation.py
  rm shopping/admin/circuit_breaker_admin.py
  rm shopping/admin/dlq_admin.py
  # ... 기타 분리된 파일들
  ```

- [ ] 12.2 쇼핑몰 __init__.py 정리
  - 제거된 모델/서비스 export 삭제

- [ ] 12.3 문서 마이그레이션
  - `docs/self_healing/` → selfhealing-python/docs/
  - `docs/L3_SELF_HEALING_SYSTEM.md` → selfhealing-python/docs/

- [ ] 12.4 새 패키지 README 완성
  - 설치 방법
  - 빠른 시작 가이드
  - API 문서 링크

- [ ] 12.5 CHANGELOG 작성

- [ ] 12.6 최종 검증 체크리스트
  - [ ] Circuit Breaker 정상 작동
  - [ ] DLQ 저장/조회 정상
  - [ ] Replay 기능 정상
  - [ ] Control API 모든 엔드포인트 정상
  - [ ] Admin 페이지 정상
  - [ ] Prometheus 메트릭 수집 정상
  - [ ] Grafana 대시보드 정상
  - [ ] Celery 태스크 정상 실행

- [ ] 12.7 Git 태그 생성
  ```bash
  git tag -a v1.0.0-self-healing-extracted -m "Self-healing extraction complete"
  ```

### 완료 조건
- 이전 파일 모두 삭제됨
- 새 패키지 문서 완비
- 모든 기능 정상 작동

### 커밋 메시지
```
chore: complete self-healing extraction

- Remove legacy self_healing files from shopping
- Migrate documentation
- Add CHANGELOG
- Tag release
```

---

## 📊 진행 상황 추적

### 스프린트 체크리스트

| # | 스프린트 | 상태 | 시작일 | 완료일 | 비고 |
|---|---------|------|-------|-------|------|
| 1 | 준비 & 기준선 | ⬜ | | | |
| 2 | 인터페이스 정의 | ⬜ | | | |
| 3 | Django 어댑터 | ⬜ | | | |
| 4 | DLQ/Replay 리팩토링 | ⬜ | | | |
| 5 | CircuitBreaker 리팩토링 | ⬜ | | | |
| 6 | Factory 패턴 | ⬜ | | | |
| 7 | 패키지 구조 생성 | ⬜ | | | |
| 8 | Core 마이그레이션 | ⬜ | | | |
| 9 | Adapters 마이그레이션 | ⬜ | | | |
| 10 | 테스트 마이그레이션 | ⬜ | | | |
| 11 | 쇼핑몰 통합 | ⬜ | | | |
| 12 | 정리 & 문서화 | ⬜ | | | |

상태: ⬜ 대기 | 🔄 진행중 | ✅ 완료 | ❌ 차단됨

---

## 🚨 리스크 및 대응

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| 테스트 실패 | 중 | 높음 | 각 스프린트 후 전체 테스트 |
| 순환 import | 중 | 중간 | 인터페이스로 의존성 역전 |
| 성능 저하 | 낮 | 중간 | 벤치마크 비교 |
| 롤백 필요 | 낮 | 높음 | Git 태그로 복원점 관리 |

---

## 📝 참고 사항

1. **각 스프린트는 독립적으로 커밋 가능** - 중간에 멈춰도 롤백 용이
2. **스프린트 7 이후부터 두 저장소 동시 작업** - 주의 필요
3. **스프린트 11이 가장 위험** - 실제 import 변경, 충분한 테스트 필요
4. **문서는 마지막에 정리** - 코드 안정화 후 작성

---

*이 문서는 SELF_HEALING_EXTRACTION_PLAN.md를 기반으로 실제 작업 가능한 단위로 분할한 계획서입니다.*

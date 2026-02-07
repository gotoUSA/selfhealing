# 205. Docstring 언어 통일 계획

> **상태**: 📋 계획
> **목적**: 모듈 레벨 docstring의 언어 사용 패턴을 분석하고 일관된 규칙을 수립한다.

---

## 1. 현황

### 1-1. 전체 통계

| 분류 | 파일 수 | 비율 |
|------|---------|------|
| **순수 영문** (docstring 전체가 영어) | ~375 | 41.9% |
| **영문 제목 + 한글 본문** (혼합) | ~339 | 37.8% |
| **한글 제목** (제목 줄에 한글 포함) | ~180 | 20.1% |
| docstring 없음 | 2 | 0.2% |

약 **58%** (519개)의 파일이 모듈 docstring에 한글을 포함.

### 1-2. 패턴별 코드 예시

#### A. 순수 영문 docstring

```python
# factory.py
"""
Provider Registry and Factory for the Self-Healing System.
"""

# adapters/airgap/base.py
"""
Air-Gap Storage Adapter Base Interface.
"""

# adapters/cache/redis_adapter.py
"""
Redis Cache Adapter for Self-Healing System
"""

# adapters/celery/signal_hooks.py
"""
Celery Signal Hooks for Self-Healing System.
"""
```

#### B. 영문 제목 + 한글 본문 (혼합 — 최다 패턴)

```python
# adapters/audit/kafka_adapter.py
"""
Kafka Audit Adapter.

고처리량 감사 이벤트를 Kafka로 스트리밍하는 어댑터.
"""

# adapters/deployment/base.py
"""
Deployment Adapter Base Interface and Data Models.

외부 배포 시스템과 연동하기 위한 추상 인터페이스와 데이터 모델을 정의합니다.
"""

# adapters/memory/drift_reconciliation.py
"""
Drift Reconciliation Module

L2 복구 시 L1과 L2 간 상태 불일치(드리프트)를 해결합니다.
"""

# services/postmortem_store.py
"""
Post-mortem Incident Storage Service.

실제 장애에 대한 Post-mortem 인시던트를 저장하고 조회합니다.
"""

# services/error_budget_service.py
"""
Error Budget Service (Re-export Module)

SRE Error Budget 계산기 및 배포 정책 어드바이저.
"""
```

#### C. 한글 제목

```python
# adapters/health_checker.py
"""
이식 가능한 고성능 헬스 체커 (Platinum SLA 최적화)
"""

# adapters/ipc/auth.py
"""
사이드카 Static Bearer Token 인증.
"""

# adapters/ipc/exceptions.py
"""
IPC 통신 관련 예외 클래스.
"""

# adapters/kafka/config.py
"""
Kafka 설정 모듈.
"""

# audit/checkpoint_strategy.py
"""
통합 체크포인트 저장 전략 모듈.
"""

# api/django/exceptions/classifier.py
"""
예외 분류기.
"""
```

---

## 2. 문제점

| 항목 | 설명 |
|------|------|
| **제목 줄 언어 불일치** | 같은 하위 패키지 내에서도 한글/영문 제목이 혼재 |
| **API 문서 생성 영향** | Sphinx/MkDocs 등 영문 기반 도구로 자동 문서 생성 시 한글 제목 처리 문제 |
| **동일 레이어 불일치** | `adapters/ipc/auth.py`는 한글 제목, `adapters/ipc/uds_client.py`도 한글 → 일관적이나, `adapters/airgap/`은 전부 영문 |
| **서브패키지별 패턴 편차** | `adapters/ipc/` 는 한글 제목 지배적, `adapters/airgap/` 는 순수 영문, `adapters/audit/` 는 혼합 |

### 2-1. 서브패키지별 언어 패턴 편차

| 서브패키지 | 주요 패턴 |
|-----------|----------|
| `adapters/ipc/` | 한글 제목 (90%+) |
| `adapters/kafka/` | 한글 제목 (80%+) |
| `adapters/airgap/` | 순수 영문 (100%) |
| `adapters/cache/` | 순수 영문 (100%) |
| `adapters/audit/` | 혼합 (영문제목+한글본문) |
| `adapters/deployment/` | 혼합 (영문제목+한글본문) |
| `services/namespace_emergency/` | 한글 인라인 docstring 다수 |
| `api/django/exceptions/` | 한글 제목 |
| `interfaces/` | 순수 영문 |

---

## 3. 수정 계획

### 3-1. 확정 규칙

```
모듈 docstring 작성 규칙:
─────────────────────────
1. 제목 줄 (첫 줄): 반드시 영문
2. 상세 설명: 한글 허용 (선택)
3. 형식:
   """
   English Title Here.

   한글 상세 설명 (선택).
   """
```

**"영문 제목 + 한글 본문" 패턴을 표준으로 채택**:
- 이미 프로젝트 최다 패턴 (339개, 37.8%)
- API 문서 자동 생성 시 제목은 영문으로 일관
- 한글 설명으로 내부 개발자 이해도 유지

### 3-2. 변경 대상

**한글 제목 → 영문 제목으로 변환해야 하는 파일: ~180개**

대표 변환 예시:

| 파일 | 현재 | 변환 후 |
|------|------|---------|
| `adapters/health_checker.py` | `이식 가능한 고성능 헬스 체커` | `Portable High-Performance Health Checker` |
| `adapters/ipc/auth.py` | `사이드카 Static Bearer Token 인증.` | `Sidecar Static Bearer Token Authentication.` |
| `adapters/ipc/exceptions.py` | `IPC 통신 관련 예외 클래스.` | `IPC Communication Exception Classes.` |
| `adapters/kafka/config.py` | `Kafka 설정 모듈.` | `Kafka Configuration Module.` |
| `audit/checkpoint_strategy.py` | `통합 체크포인트 저장 전략 모듈.` | `Unified Checkpoint Storage Strategy Module.` |
| `api/django/exceptions/classifier.py` | `예외 분류기.` | `Exception Classifier.` |

### 3-3. 인라인 docstring (속성/변수)

```python
# 현재 (services/namespace_emergency/tracker.py L78)
"""로컬 캐시 TTL (30초). 권장: _get_cache_ttl_seconds() 사용."""

# 현재 (audit/cascade_config.py L89)
"""기본 Cascade 체인 설정."""
```

인라인 속성 docstring은 한글 유지 가능 (내부 설명 목적). 모듈 docstring 규칙과 분리.

### 3-4. 검증 항목

- [ ] 자동 변환 스크립트 작성 (AST 파싱으로 모듈 docstring만 추출)
- [ ] 변환 후 의미 보존 확인
- [ ] 기존 한글 설명은 영문 제목 아래 한글 본문으로 보존
- [ ] `__init__.py` 파일의 패키지 docstring도 동일 규칙 적용

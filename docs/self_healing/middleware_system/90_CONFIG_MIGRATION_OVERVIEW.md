# Configuration Migration Overview

> 하드코딩된 설정값을 계층형 설정 시스템으로 마이그레이션하기 위한 개요 문서

## 1. 현재 상태 분석

### 1.1 문제점

`packages/selfhealing-python` 디렉토리 내에 약 200개 이상의 하드코딩된 설정값이 산재해 있음.
이로 인해 발생하는 문제:

- 운영 중 설정 변경 시 코드 배포 필요
- 환경별(dev/staging/prod) 다른 값 적용의 어려움
- 설정값 추적 및 감사(audit) 불가
- A/B 테스트나 점진적 롤아웃 불가

### 1.2 설정 분류 기준

| 분류 | 정의 | 변경 빈도 | 관리 방식 |
|------|------|----------|----------|
| 불변 설정 | 시스템 식별자, 스키마 버전 등 변경 시 호환성 문제 발생 | 거의 없음 | Django Settings / ENV |
| 가변 설정 | 임계값, TTL, 배치 크기 등 운영 중 조정 필요 | 빈번 | RuntimeConfigManager |

---

## 2. 기존 인프라 분석

### 2.1 LayeredProvider 계층 구조

프로젝트에는 이미 4계층 설정 시스템이 구현되어 있음.

| 계층 | 우선순위 | 소스 | 용도 |
|------|----------|------|------|
| Layer 1 | 최하위 | Pydantic Field default | 코드 내 기본값 |
| Layer 2 | 중하위 | ENV / .env | 환경별 고정값 |
| Layer 3 | 중상위 | DB/Redis (RuntimeConfigManager) | 동적 런타임 설정 |
| Layer 4 | 최상위 | Request-scoped (ContextVar) | 요청별 임시 오버라이드 |

**핵심**: 상위 계층 값이 하위 계층을 자동으로 오버라이드함.

### 2.2 불변 설정 관리 (Layer 1-2)

**적용 대상**:
- `REDIS_KEY_PREFIX`: Redis 키 네이밍 규칙
- `VERSION`: 설정 스키마 버전

**관리 방식**:
- Pydantic Settings의 `Field(default=...)` 또는
- Django Settings의 `getattr(settings, KEY, default)` 또는
- 환경변수 `os.environ.get(KEY, default)`

**특징**:
- 앱 시작 시 1회 로드
- 변경 시 재배포/재시작 필요
- 변경 이력 관리 불필요

### 2.3 가변 설정 관리 (Layer 3)

**적용 대상**:
- `failure_threshold`: Circuit Breaker 실패 임계값
- `recovery_timeout`: 복구 대기 시간
- `cache_ttl_seconds`: 캐시 유효 시간
- `batch_size`: 배치 처리 크기
- 기타 모든 운영 조정 가능 파라미터

**관리 방식**:
- `RuntimeConfigManager`를 통한 DB/Redis 저장
- `StateBackend` 인터페이스로 영속화

**특징**:
- 런타임에 즉시 변경 가능
- 변경 이력 자동 기록
- 감사(Audit) 로깅 지원

### 2.4 요청 스코프 오버라이드 (Layer 4)

**적용 대상**:
- 특정 요청에서만 임시로 다른 설정 적용
- A/B 테스트, 카나리 배포

**관리 방식**:
- `set_request_override()` / `clear_request_overrides()`
- `ContextVar` 기반 스레드 안전 구현

---

## 3. 마이그레이션 전략

### 3.1 Phase 1: 불변 설정 분리

**대상 파일**:
- 시스템 식별자 관련 상수
- 스키마 버전 정보

**작업 내용**:
1. Pydantic Settings 클래스에 불변 설정 필드 정의
2. `env_prefix` 설정으로 환경변수 매핑
3. `Field(frozen=True)` 또는 주석으로 불변성 명시

### 3.2 Phase 2: 가변 설정 RuntimeConfigManager 등록

**대상**:
- TTL, Timeout, Interval 값
- Threshold, Limit, Count 값
- Batch Size 값
- Max Retries 값

**작업 내용**:
1. `CONFIG_CLASSES`에 새 설정 타입 등록
2. `STORAGE_KEYS`에 저장소 키 등록
3. 기존 하드코딩 값을 `RuntimeConfigManager.get_config()` 호출로 대체

### 3.3 Phase 3: API 노출

**작업 내용**:
1. 설정 조회/수정 API 엔드포인트 추가
2. 권한 검증 (RBAC) 적용
3. 변경 감사 로그 연동

### 3.4 Phase 4: 기존 코드 마이그레이션

**작업 내용**:
1. `get_layered_settings()` 호출로 하드코딩 값 대체
2. 테스트 코드 수정
3. 문서화

---

## 4. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                        Application Code                          │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │              get_layered_settings()                        │   │
│  │         (selfhealing.settings.layered_provider)           │   │
│  └───────────────────────────────────────────────────────────┘   │
│                              │                                    │
│         ┌────────────────────┼────────────────────┐              │
│         ▼                    ▼                    ▼              │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐        │
│  │  Layer 4    │     │  Layer 3    │     │  Layer 1-2  │        │
│  │  Request    │     │  Runtime    │     │  Static     │        │
│  │  Override   │     │  Config     │     │  Config     │        │
│  │  (ContextVar│     │  (DB/Redis) │     │  (ENV)      │        │
│  └─────────────┘     └─────────────┘     └─────────────┘        │
│                              │                                    │
│                              ▼                                    │
│                    ┌─────────────────┐                           │
│                    │  StateBackend   │                           │
│                    │  (Redis/Memory) │                           │
│                    └─────────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 관련 문서

- [91_CONFIG_INVENTORY.md](91_CONFIG_INVENTORY.md) - 하드코딩된 설정값 인벤토리
- [92_CONFIG_IMPLEMENTATION_GUIDE.md](92_CONFIG_IMPLEMENTATION_GUIDE.md) - 구현 가이드 및 순서
- [93_CONFIG_MIGRATION_CHECKLIST.md](93_CONFIG_MIGRATION_CHECKLIST.md) - 마이그레이션 체크리스트

---

## 6. 참조 코드 위치

| 컴포넌트 | 파일 경로 |
|----------|----------|
| LayeredProvider | `selfhealing/settings/layered_provider.py` |
| RuntimeConfigManager | `selfhealing/services/runtime_config/base.py` |
| StateBackend | `selfhealing/core/state_backend.py` |
| CONFIG_CLASSES | `selfhealing/services/runtime_config/constants.py` |
| PendingConfigService | `selfhealing/services/runtime_config/pending_config.py` |
| GlobalConfigPropagator | `selfhealing/services/runtime_config/propagator.py` |

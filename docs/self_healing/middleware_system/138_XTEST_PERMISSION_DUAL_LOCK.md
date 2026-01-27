# X-Test Permission 2중 보안 장치

**문서 번호:** 138  
**작성일:** 2026-01-27  
**상태:** 구현 완료  
**구현일:** 2026-01-27  
**선행 문서:** 137_XTEST_CONTEXT_GLOBAL_TAGGING.md

---

## 1. 목적

X-Test-Mode API에 Django RBAC 기반 2중 보안 장치를 적용하여, 헤더 검증만으로는 부족한 보안을 강화한다.

### 1.1 현재 보안 수준

| 레이어 | 현재 상태 |
|--------|----------|
| 헤더 검증 | ✅ `X-Test-Mode: chaos-monkey` |
| 환경 변수 | ✅ `DEBUG=True` 또는 `CHAOS_ENABLED=true` |
| 프로덕션 차단 | ✅ `ENVIRONMENT != production` |
| **Django 인증** | ❌ `authentication_classes = []` |
| **Django 권한** | ❌ `permission_classes = [AllowAny]` |

### 1.2 목표 보안 수준

| 레이어 | 목표 |
|--------|------|
| 1차: Django RBAC | `HasChaosTestPermission` 권한 클래스 |
| 2차: XTestModeMixin | 헤더 + 환경 변수 검증 |

---

## 2. 현재 상태 분석

### 2.1 기존 권한 클래스 패턴

| 파일 | 클래스 | 패턴 |
|------|--------|------|
| `api/django/permissions.py` | `IsSelfHealingAuthenticated` | 테스트 바이패스 지원 |
| `api/django/permissions.py` | `IsSelfHealingAdmin` | 그룹 기반 RBAC |
| `api/django/permissions.py` | `IsSelfHealingOperator` | 운영자 권한 |
| `api/django/permissions.py` | `RiskBasedApprovalPermission` | 4-Eyes 듀얼 승인 |

### 2.2 XTestModeMixin 현황

| 파일 | 현재 설정 |
|------|----------|
| `api/django/views/xtest/base.py` | `authentication_classes = []` |
| `api/django/views/xtest/base.py` | `permission_classes = [AllowAny]` |

### 2.3 테스트 바이패스 패턴

| 파일 | 함수 | 용도 |
|------|------|------|
| `api/django/permissions.py` | `_is_auth_disabled()` | `DISABLE_SELFHEALING_AUTH=true` 시 바이패스 |

---

## 3. 설계

### 3.1 HasChaosTestPermission 클래스

**위치:** `api/django/permissions.py`

**권한 조건:**

| 조건 | 설명 |
|------|------|
| 테스트 바이패스 | `_is_auth_disabled()` 시 허용 |
| 프로덕션 차단 | `ENVIRONMENT == production` 시 무조건 거부 (Fail-Secure) |
| 인증 필요 | `request.user.is_authenticated` |
| 관리자 허용 | `is_superuser` 자동 허용 |
| 그룹 기반 | `selfhealing_admin` 또는 `selfhealing_chaos_tester` 그룹 |

### 3.2 신규 Django 그룹

| 그룹명 | 역할 |
|--------|------|
| `selfhealing_chaos_tester` | X-Test/Chaos 실험 전용 권한 |

### 3.3 위험 작업 4-Eyes 듀얼 승인

| 작업 유형 | 승인 요구 |
|----------|----------|
| 일반 X-Test (DLQ 주입, CB 조작) | 단일 승인 |
| 고위험 (리전 격리, GLOBAL 상태 변경) | 듀얼 승인 권장 |

---

## 4. 구현 순서

### Step 1: HasChaosTestPermission 생성

**파일:** `api/django/permissions.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `HasChaosTestPermission` 클래스 추가 |
| 1-2 | `has_permission()` 메서드 구현 |
| 1-3 | Fail-Secure 패턴 적용 (`try/except` → `return False`) |
| 1-4 | 로깅 추가 (거부 사유 기록) |

### Step 2: XTestModeMixin 수정

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `authentication_classes` 설정 (기본 인증 활성화) |
| 2-2 | `permission_classes = [HasChaosTestPermission]` |
| 2-3 | 기존 `check_chaos_permission()` 유지 (2차 검증) |

### Step 3: 모든 X-Test View 업데이트

**대상 파일:**

| 파일 | View 수 |
|------|---------|
| `views/xtest/dlq.py` | 4개 |
| `views/xtest/replay.py` | 4개 |
| `views/xtest/retry.py` | 4개 |
| `views/xtest/rate_limit.py` | 5개 |
| `views/xtest/idempotency.py` | 5개 |
| `views/xtest/integration.py` | 4개 |
| `views/xtest/circuit_breaker.py` | 5개 |
| `views/xtest/error_budget.py` | 3개 |

### Step 4: 그룹 생성 마이그레이션

**파일:** 신규 마이그레이션 또는 fixture

| 순서 | 항목 |
|------|------|
| 4-1 | `selfhealing_chaos_tester` 그룹 생성 |
| 4-2 | 기존 `selfhealing_admin` 사용자에게 자동 권한 부여 |

---

## 5. 보안 고려사항

### 5.1 Fail-Secure 설계

| 상황 | 동작 |
|------|------|
| 그룹 조회 실패 | 거부 (`return False`) |
| 사용자 정보 없음 | 거부 |
| 예외 발생 | 거부 + 로깅 |

### 5.2 로깅 요구사항

| 이벤트 | 로그 레벨 | 내용 |
|--------|----------|------|
| 권한 거부 | WARNING | 사용자, 이유, 엔드포인트 |
| 프로덕션 차단 | ERROR | 프로덕션 접근 시도 기록 |
| 권한 허용 | DEBUG | 허용된 사용자 정보 |

### 5.3 Audit 연동

| 파일 | 연동 |
|------|------|
| `services/audit/xtest_audit.py` | 권한 체크 결과 WAL 기록 |

---

## 6. 테스트 계획

### 6.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_chaos_tester_group_allowed` | 그룹 멤버 허용 |
| `test_admin_always_allowed` | superuser 허용 |
| `test_anonymous_denied` | 미인증 거부 |
| `test_production_always_denied` | 프로덕션 거부 |
| `test_auth_disabled_bypass` | 테스트 바이패스 |

### 6.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 권한 있는 사용자 + 올바른 헤더 → 허용 |
| 권한 있는 사용자 + 헤더 없음 → 거부 (2차 검증) |
| 권한 없는 사용자 + 올바른 헤더 → 거부 (1차 검증) |

---

## 7. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `api/django/permissions.py` | `IsSelfHealingAdmin` 그룹 체크 패턴 |
| `api/django/permissions.py` | `_is_auth_disabled()` 바이패스 함수 |
| `api/django/permissions.py` | `RiskBasedApprovalPermission` 4-Eyes 패턴 |
| `api/django/views/xtest/base.py` | `XTestModeMixin` 클래스 |

---

**다음 문서:** 139_XTEST_ARTIFACT_CLEANER.md

# X-Test Regional Boundary (리전 스코프 강제)

**문서 번호:** 140  
**작성일:** 2026-01-27  
**상태:** ✅ 구현 완료  
**선행 문서:** 139_XTEST_ARTIFACT_CLEANER.md

---

## 1. 목적

X-Test API 호출이 현재 리전 범위를 벗어나지 않도록 강제하여, 서울에서 호출한 테스트가 도쿄 리전에 영향을 주는 것을 물리적으로 차단한다.

### 1.1 현재 위험

| 시나리오 | 위험 |
|---------|------|
| `set_emergency_global` 호출 | 모든 리전에 STRICT 모드 적용 |
| `isolate_region` 호출 | 다른 리전 격리 가능 |
| GLOBAL Redis 접근 | 리전 간 상태 공유 |

### 1.2 목표

| 항목 | 목표 |
|------|------|
| 리전 헤더 | `X-Region` 헤더 도입 |
| 스코프 제한 | LOCAL scope API만 허용, GLOBAL scope 차단 |
| 403 반환 | 리전 불일치 시 명확한 에러 |

---

## 2. 현재 상태 분석

### 2.1 리전 식별 인프라

| 파일 | 구성요소 | 역할 |
|------|---------|------|
| `core/cluster_identity.py` | `ClusterIdentity.region` | 현재 클러스터 리전 |
| `core/cluster_identity.py` | `SELFHEALING_REGION` 환경변수 | 리전 설정 |
| `settings/namespace.py` | `get_effective_namespace()` | 동적 네임스페이스 |

### 2.2 리전 격리 인프라

| 파일 | 클래스 | 역할 |
|------|--------|------|
| `services/isolation/regional_gate.py` | `RegionalIsolationGate` | 리전 격리 상태 관리 |
| `core/tiered_redis.py` | `RedisScope.LOCAL/GLOBAL` | Redis 계층 분리 |

### 2.3 현재 X-Test 리전 체크

| 현재 상태 |
|----------|
| X-Test API에서 리전 제한 로직 없음 |
| GLOBAL scope API 호출 가능 |

---

## 3. 설계

### 3.1 X-Region 헤더

| 헤더 | 용도 | 필수 여부 |
|------|------|----------|
| `X-Region` | 타겟 리전 명시 | GLOBAL scope API에서 필수 |

### 3.2 API 스코프 분류

| 스코프 | API 예시 | 리전 체크 |
|--------|---------|----------|
| LOCAL | DLQ 주입, CB 조작, Replay | 현재 리전만 영향 |
| GLOBAL | Emergency 설정, 리전 격리 | X-Region 헤더 필수 + 일치 검증 |

### 3.3 검증 로직

| 단계 | 검증 |
|------|------|
| 1 | GLOBAL scope API인지 확인 |
| 2 | `X-Region` 헤더 존재 확인 |
| 3 | 헤더 값과 현재 클러스터 리전 일치 확인 |
| 4 | 불일치 시 403 Forbidden |

### 3.4 403 응답 형식

| 필드 | 값 |
|------|-----|
| `error` | `cross_region_xtest_denied` |
| `message` | 상세 메시지 |
| `current_region` | 현재 클러스터 리전 |
| `target_region` | 요청된 타겟 리전 |

---

## 4. 구현 순서

### Step 1: GLOBAL Scope API 목록 정의

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `GLOBAL_SCOPE_ENDPOINTS` 상수 정의 |
| 1-2 | 엔드포인트 패턴 목록 |

**GLOBAL Scope API 목록:**

| 엔드포인트 | 이유 |
|-----------|------|
| `xtest/emergency/global/*` | 전역 Emergency 상태 |
| `xtest/isolation/region/*` | 리전 격리 |
| `xtest/governance/global/*` | 전역 거버넌스 |

### Step 2: XTestModeMixin 확장

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `check_regional_scope()` 메서드 추가 |
| 2-2 | `get_current_region()` 헬퍼 메서드 |
| 2-3 | `is_global_scope_endpoint()` 판정 메서드 |

### Step 3: 검증 로직 통합

**파일:** `api/django/views/xtest/base.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `check_chaos_permission()`에 리전 체크 추가 |
| 3-2 | GLOBAL scope일 때만 체크 수행 |
| 3-3 | 403 Response 생성 |

### Step 4: 관련 View 업데이트

**대상 View:**

| 파일 | View | 스코프 |
|------|------|--------|
| `views/xtest/integration.py` | `FullSnapshotView` | LOCAL (읽기 전용) |
| `views/xtest/emergency.py` | `SetEmergencyGlobalView` | GLOBAL |
| `views/xtest/isolation.py` | `IsolateRegionView` | GLOBAL |

### Step 5: 문서 업데이트

**파일:** `api/django/views/xtest/__init__.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `X-Region` 헤더 설명 추가 |
| 5-2 | GLOBAL scope API 목록 명시 |

---

## 5. 예외 처리

### 5.1 리전 미설정 환경

| 상황 | 동작 |
|------|------|
| `SELFHEALING_REGION` 미설정 | LOCAL scope만 허용, GLOBAL 차단 |
| 개발 환경 (`development`) | 리전 체크 경고만 (선택적) |

### 5.2 Fail-Secure

| 상황 | 동작 |
|------|------|
| 리전 정보 조회 실패 | 거부 (`return 403`) |
| 예외 발생 | 거부 + 로깅 |

---

## 6. 테스트 계획

### 6.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_local_scope_no_header_required` | LOCAL API는 헤더 불필요 |
| `test_global_scope_header_required` | GLOBAL API는 헤더 필수 |
| `test_region_mismatch_denied` | 리전 불일치 시 403 |
| `test_region_match_allowed` | 리전 일치 시 허용 |

### 6.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 서울 클러스터 → `X-Region: seoul` → 허용 |
| 서울 클러스터 → `X-Region: tokyo` → 403 |
| 서울 클러스터 → LOCAL API (헤더 없음) → 허용 |

---

## 7. 모니터링

### 7.1 메트릭

| 메트릭 | 설명 |
|--------|------|
| `xtest_cross_region_denied_total` | 리전 불일치 거부 횟수 |
| `xtest_global_scope_requests_total` | GLOBAL scope 요청 수 |

### 7.2 알림

| 조건 | 알림 |
|------|------|
| cross_region 거부 > 10/min | WARNING |
| 동일 IP에서 반복 거부 | SECURITY WARNING |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `core/cluster_identity.py` | `ClusterIdentity.region` 조회 |
| `services/isolation/regional_gate.py` | 리전 격리 패턴 |
| `core/tiered_redis.py` | LOCAL/GLOBAL 분리 |
| `api/django/views/xtest/base.py` | `XTestModeMixin` |

---

**다음 문서:** 141_XTEST_EMERGENCY_RECOVERY_SCENARIO.md

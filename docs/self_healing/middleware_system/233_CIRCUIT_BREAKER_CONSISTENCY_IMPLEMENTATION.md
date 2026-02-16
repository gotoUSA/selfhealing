# 233. Circuit Breaker 정합성 이슈 구현 문서

작성일: 2026-02-13
범위: `packages/selfhealing-python/src/selfhealing`
근거 기준: 코드 본문(추측 없음)

---

## 1) 목적

본 문서는 Circuit Breaker 경로에서 확인된 **정합성 위반 지점**을 코드 근거로 고정하고,
이를 구현(수정)하기 위한 작업 단위를 정의한다.

---

## 2) 코드 근거 기반 확정 이슈

### 이슈 A. `update_state` 인터페이스/호출 불일치

- 저장소 인터페이스(`CircuitBreakerStateRepository.update_state`)는 다음 인자만 허용한다.
  - `service_name`, `state`, `failure_count`, `success_count`, `opened_at`
- 근거:
  - `interfaces/repositories.py` L602-L610
- 반면 서비스 코드에서는 `manually_controlled`를 전달한다.
- 근거:
  - `services/circuit_breaker/service.py` L897-L899

**판단:** 인터페이스 계약 위반 호출이 존재한다.

---

### 이슈 B. Layered 저장소의 인터페이스 시그니처 불일치

- 인터페이스 계약:
  - `set_manual_control(service_name, state, controlled_by_id, reason, expires_at)`
  - `clear_manual_control(service_name, preserve_reason=False)`
- 근거:
  - `interfaces/repositories.py` L624-L646
- Layered 구현:
  - `set_manual_control(service_name, controlled_by_id, reason, ttl_minutes)`
  - `clear_manual_control(service_name, reason="")`
- 근거:
  - `adapters/memory/layered_repository/repository_operations.py` L251-L270

**판단:** Layered 구현이 저장소 인터페이스 계약과 다르다.

---

### 이슈 C. Layered -> L1 전달 인자 순서/의미 불일치

- Layered 구현은 L1 호출 시 다음과 같이 전달한다.
  - `self._l1.set_manual_control(service_name, controlled_by_id, reason, ttl_minutes)`
- 근거:
  - `adapters/memory/layered_repository/repository_operations.py` L259-L260
- L1 구현 시그니처는 두 번째 인자가 `state`이다.
  - `set_manual_control(self, service_name, state, controlled_by_id, reason, expires_at)`
- 근거:
  - `adapters/memory/circuit_breaker.py` L171-L177

**판단:** Layered 호출은 L1 시그니처 의미와 맞지 않는다.

---

### 이슈 D. 만료 처리 플로우에서 어댑터별 상태 의미 불일치

- 만료 처리 플로우는 먼저 `HALF_OPEN` 전환 후, `clear_manual_control(..., preserve_reason=True)`를 호출한다.
- 근거:
  - `services/circuit_breaker/manual_control.py` L507-L516
- Memory 어댑터의 `clear_manual_control`은 상태를 강제로 `CLOSED`로 재설정하고 카운터를 0으로 리셋한다.
- 근거:
  - `adapters/memory/circuit_breaker.py` L206-L232
- Redis 어댑터의 `clear_manual_control`은 manual 관련 필드만 해제하고 상태는 변경하지 않는다.
- 근거:
  - `adapters/redis/circuit_breaker.py` L524-L548

**판단:** 동일한 만료 흐름이 저장소 구현에 따라 서로 다른 최종 상태를 만든다.

---

### 이슈 E. AUTO_OPEN 감사 reason 구성 시 snapshot 키 불일치

- snapshot 구조는 `snapshot["circuit_breaker"]["failure_count"]` 및 `threshold_config` 하위로 저장된다.
- 근거:
  - `services/circuit_breaker/service.py` L580-L592
- reason 문자열은 최상위 키 `snapshot.get('failure_count')`, `snapshot.get('threshold')`를 조회한다.
- 근거:
  - `services/circuit_breaker/service.py` L658-L660

**판단:** reason에 실제 값이 아닌 `N/A`가 기록될 수 있는 구조적 불일치가 존재한다.

---

## 3) 구현 범위 (수정 대상)

1. `services/circuit_breaker/service.py`
   - `manual_control(..., action="auto")` 경로에서 인터페이스 계약에 맞는 API 사용으로 변경
2. `adapters/memory/layered_repository/repository_operations.py`
   - `set_manual_control`, `clear_manual_control` 시그니처를 인터페이스와 동일하게 정렬
   - L1 호출 인자 매핑 수정
3. `adapters/memory/circuit_breaker.py`
   - `clear_manual_control`의 상태/카운터 리셋 동작을 계약 의도(수동 제어 해제)에 맞게 재검토 및 정렬
4. `adapters/redis/circuit_breaker.py`
   - `clear_manual_control`와 Memory 동작의 의미 일치화
5. `services/circuit_breaker/service.py`
   - AUTO_OPEN 감사 reason 생성 시 snapshot 실제 키 경로 사용

---

## 4) 구현 원칙

- 인터페이스(`CircuitBreakerStateRepository`)를 단일 계약 기준으로 삼는다.
- 동일 유스케이스(수동 제어 해제/만료)에서 저장소별 결과 상태가 달라지지 않도록 맞춘다.
- 상태 전이 시 카운터 리셋 여부는 명시적 전이 함수에서만 수행하고, 수동 제어 해제 함수의 책임은 분리한다.
- 감사(reason) 문자열은 snapshot 실제 구조를 직접 참조한다.

---

## 5) 수용 기준 (Acceptance Criteria)

1. `manual_control(..., action="auto")` 실행 시 저장소 호출이 인터페이스 계약과 일치한다.
2. Layered 저장소가 인터페이스 시그니처를 그대로 구현한다.
3. Layered -> L1 전달 인자가 `state` 의미를 보존한다.
4. `check_and_expire_manual_overrides()` 흐름의 최종 상태가 Memory/Redis에서 동일하다.
5. AUTO_OPEN 감사 reason에 `failure_count`/threshold 관련 실제 값이 반영된다.

---

## 6) 검증 계획

- 단위 검증:
  - Memory/Redis/Layered 각각에 대해 `set_manual_control`, `clear_manual_control`, 만료 처리 시나리오 실행
- 서비스 검증:
  - `manual_control(action="auto")` 호출 시 예외 없이 상태가 기대대로 전환되는지 확인
- 감사 검증:
  - AUTO_OPEN 감사 로그 reason 문자열에 `N/A`가 아닌 snapshot 실제 값이 들어가는지 확인

---

## 7) 비범위(이번 문서에서 다루지 않음)

- 새로운 정책/기능 추가
- 임계치 튜닝
- 운영 파라미터 변경

본 문서는 정합성 위반 해소를 위한 구현 범위만 다룬다.

---

## 8) 구현 완료 기록

구현일: 2026-02-17

### 이슈 A 수정 내용

- `services/circuit_breaker/service.py` `manual_control(action="auto")` 경로에서
  `update_state(manually_controlled=False)` 호출을 제거하고
  `clear_manual_control(service_name, preserve_reason=True)` 호출로 교체하였다.
- 인터페이스 계약에 없는 `manually_controlled` 인자 전달이 제거되었다.

### 이슈 B+C 수정 내용

- `adapters/memory/layered_repository/repository_operations.py`의
  `set_manual_control` 시그니처를 인터페이스와 동일하게 변경하였다:
  - 변경 전: `(service_name, controlled_by_id, reason, ttl_minutes)`
  - 변경 후: `(service_name, state, controlled_by_id, reason, expires_at)`
- `clear_manual_control` 시그니처를 인터페이스와 동일하게 변경하였다:
  - 변경 전: `(service_name, reason="")`
  - 변경 후: `(service_name, preserve_reason=False)`
- L1 호출 인자가 `state` 위치를 포함하여 인터페이스 시그니처와 일치하도록 수정하였다.

### 이슈 D 수정 내용

- `adapters/memory/circuit_breaker.py`의 `clear_manual_control`에서
  상태를 CLOSED로 강제 전환하고 카운터를 0으로 리셋하는 동작을 제거하였다.
- 수정 후 동작: 수동 제어 플래그(`manually_controlled`, `controlled_by_id`,
  `manual_override_expires_at`)만 해제하고, 상태(state)·카운터·opened_at는 유지한다.
- 이로써 `check_and_expire_manual_overrides()` 만료 흐름에서
  Memory/Redis 어댑터 모두 동일하게 HALF_OPEN 상태가 보존된다.

### 이슈 E 수정 내용

- `services/circuit_breaker/service.py`의 `_log_circuit_open_audit`에서
  snapshot 최상위 키(`snapshot.get('failure_count')`, `snapshot.get('threshold')`)를
  실제 구조 경로로 수정하였다:
  - `snapshot["circuit_breaker"]["failure_count"]`
  - `snapshot["circuit_breaker"]["threshold_config"]["failure_threshold"]`
- 감사 reason 문자열에 `N/A` 대신 실제 값이 기록된다.

### 기존 테스트 정합성 수정

- `tests/unit/adapters/memory_repositories/test_circuit_breaker.py`의
  `test_clear_manual_control`에서 `clear_manual_control` 후 상태 검증을
  `CLOSED` → `OPEN`(수동 제어 설정 시 지정한 상태 유지)으로 변경하였다.

### 통합 테스트 판단

- 이번 수정은 내부 계약 정합성에 한정되며 외부 시스템 연동이 필요하지 않으므로
  통합 테스트 추가는 불필요하다.

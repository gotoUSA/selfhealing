# X-Test-Mode Retry Handler 테스트 API 설계

**문서 번호:** 119  
**작성일:** 2026-01-26  
**상태:** ✅ 구현 완료  
**선행 문서:** 118_XTEST_MODE_REPLAY.md

---

## 1. 목적

Retry Handler의 Exponential Backoff, DLQ 라우팅, Rate Limit 인식 동작을 X-Test-Mode 환경에서 관찰할 수 있는 API 설계.

### 1.1 테스트 목표

| 목표 | 설명 |
|------|------|
| Backoff 계산 | 지수 백오프 + 지터 계산 확인 |
| 재시도 시퀀스 | 재시도 간격 시퀀스 미리보기 |
| DLQ 라우팅 | 최대 재시도 초과 시 DLQ 이동 확인 |
| Rate Limit 인식 | Self-DDoS 방지 동작 확인 |

### 1.2 Retry Handler 주요 기능 (코드 기준)

`services/retry_handler.py`에서 제공:

| 기능 | 클래스/메서드 | 설명 |
|------|-------------|------|
| 설정 | `RetryConfig` | 재시도 설정 (max_attempts, backoff_base 등) |
| 실행 | `RetryHandler.execute()` | 재시도 로직 실행 |
| 판정 | `RetryHandler.should_retry()` | 재시도 여부 판정 |
| 백오프 | `BackoffCalculator` | 지수 백오프 계산 |

---

## 2. API 엔드포인트 설계

### 2.1 Backoff 계산 미리보기

**엔드포인트:** `GET /api/self-healing/xtest/retry/backoff-preview/`

**목적:** 설정 기반 재시도 간격 시퀀스 미리보기

**쿼리 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| max_attempts | int | X | 최대 재시도 (기본: 설정값) |
| backoff_base | int | X | 백오프 기본값 (기본: 4) |
| backoff_max | int | X | 최대 대기 (기본: 180) |
| jitter_percent | int | X | 지터 퍼센트 (기본: 25) |

**응답:**

| 필드 | 설명 |
|------|------|
| config | 적용된 설정 |
| delays | 재시도별 대기 시간 배열 (예: [4, 16, 64, 180]) |
| delays_with_jitter | 지터 적용 시 범위 (min, max) |
| total_max_delay | 최대 총 대기 시간 |

### 2.2 재시도 시뮬레이션

**엔드포인트:** `POST /api/self-healing/xtest/retry/simulate/`

**목적:** 실패 시나리오 시뮬레이션 및 재시도 동작 관찰

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| failure_count | int | O | 연속 실패 횟수 (시뮬레이션) |
| max_attempts | int | X | 최대 재시도 |
| domain | string | X | 도메인 (DLQ 연동용) |
| simulate_dlq | bool | X | DLQ 저장 시뮬레이션 (기본 false) |

**응답:**

| 필드 | 설명 |
|------|------|
| total_attempts | 총 시도 횟수 |
| final_action | 최종 액션 (RETRY, DLQ, ABORT) |
| retry_sequence | 각 시도별 결과 및 대기 시간 |
| dlq_routed | DLQ 라우팅 여부 |
| dlq_id | DLQ 저장 시 ID (simulate_dlq=true) |

### 2.3 Rate Limit 인식 상태

**엔드포인트:** `GET /api/self-healing/xtest/retry/rate-limit-status/`

**목적:** Retry의 Rate Limit 인식 상태 확인

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 도메인 (rate_limit_key) |

**응답:**

| 필드 | 설명 |
|------|------|
| rate_limit_aware | Rate Limit 인식 활성화 여부 |
| current_rate | 현재 재시도 비율 |
| throttled | 현재 스로틀링 상태 |
| recommended_delay | 권장 대기 시간 |

### 2.4 RetryConfig 조회

**엔드포인트:** `GET /api/self-healing/xtest/retry/config/`

**목적:** 현재 적용된 Retry 설정 조회

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 도메인별 설정 조회 |

**응답:**

| 필드 | 설명 |
|------|------|
| source | 설정 소스 (runtime, settings, default) |
| config | RetryConfig 전체 |
| domain_overrides | 도메인별 오버라이드 |

---

## 3. 서비스 레이어 연동

### 3.1 기존 서비스 활용

| 서비스/클래스 | 위치 | 용도 |
|-------------|------|------|
| `RetryConfig.from_settings()` | `services/retry_handler.py` | 설정 로드 |
| `BackoffCalculator.calculate()` | `services/backoff_calculator.py` | 백오프 계산 |
| `BackoffCalculator.get_delays_sequence()` | `services/backoff_calculator.py` | 시퀀스 조회 |

### 3.2 BackoffCalculator 메서드

`services/backoff_calculator.py`에서:

| 메서드 | 용도 |
|--------|------|
| `calculate(attempt)` | 특정 시도의 대기 시간 |
| `get_delays_sequence(max_attempts)` | 전체 시퀀스 |
| `with_jitter(delay)` | 지터 적용 |

---

## 4. 구현 순서

### Step 1: View 파일 생성

**파일:** `api/django/views/xtest/retry.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `BackoffPreviewView` | 백오프 미리보기 |
| 1-2 | `RetrySimulateView` | 재시도 시뮬레이션 |
| 1-3 | `RetryRateLimitStatusView` | Rate Limit 상태 |
| 1-4 | `RetryConfigView` | 설정 조회 |

### Step 2: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 2-1 | `xtest/retry/backoff-preview/` | `BackoffPreviewView` |
| 2-2 | `xtest/retry/simulate/` | `RetrySimulateView` |
| 2-3 | `xtest/retry/rate-limit-status/` | `RetryRateLimitStatusView` |
| 2-4 | `xtest/retry/config/` | `RetryConfigView` |

### Step 3: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가

### Step 4: 테스트 작성

**파일:** `tests/unit/api/xtest/test_retry_views.py`

| 테스트 케이스 |
|--------------|
| backoff-preview 계산 테스트 |
| simulate 성공 테스트 |
| simulate DLQ 라우팅 테스트 |
| rate-limit-status 조회 테스트 |
| config 조회 테스트 |

---

## 5. 테스트 시나리오

### 5.1 Backoff 시퀀스 확인

```
1. GET /xtest/retry/backoff-preview/?max_attempts=4&backoff_base=4
   → delays: [4, 16, 64, 180]
   → delays_with_jitter: [{min: 3, max: 5}, {min: 12, max: 20}, ...]
```

### 5.2 DLQ 라우팅 시나리오

```
1. POST /xtest/retry/simulate/
   {failure_count: 5, max_attempts: 3, simulate_dlq: true, domain: "external"}
   → final_action: "DLQ"
   → dlq_routed: true
   → dlq_id: 123
```

### 5.3 Rate Limit 인식 시나리오

```
1. 연속 재시도 시뮬레이션
2. GET /xtest/retry/rate-limit-status/?domain=payment
   → throttled: true
   → recommended_delay: 30
```

---

## 6. RetryAction 열거형

`services/retry_handler.py`에 정의:

| 값 | 설명 |
|----|------|
| `RETRY` | 재시도 수행 |
| `DLQ` | DLQ로 라우팅 |
| `ABORT` | 중단 (재시도 불가 예외) |
| `SUCCESS` | 성공 |

---

## 7. 시뮬레이션 vs 실제 실행

| 모드 | 설명 |
|------|------|
| `simulate_dlq=false` | 계산만, DLQ 저장 안 함 |
| `simulate_dlq=true` | 테스트 DLQ 항목 생성 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/retry_handler.py` | RetryHandler, RetryConfig, RetryAction |
| `services/backoff_calculator.py` | BackoffCalculator, BackoffConfig |
| `services/rate_limit_coordinator.py` | RateLimitCoordinator (Rate Limit 인식) |
| `api/django/views/xtest/base.py` | XTestModeMixin 패턴 |

---

**다음 문서:** 120_XTEST_MODE_RATE_LIMITER.md

# X-Test-Mode Rate Limiter 테스트 API 설계

**문서 번호:** 120
**작성일:** 2026-01-26
**상태:** ✅ 구현 완료
**선행 문서:** 119_XTEST_MODE_RETRY.md

---

## 구현 완료 사항

| 항목 | 파일 | 상태 |
|------|------|------|
| View 클래스 5개 | `api/django/views/xtest/rate_limit.py` | ✅ |
| 히스토리 수집 | `api/django/rate_limit.py` (ring buffer) | ✅ |
| URL 라우팅 | `api/django/urls.py` | ✅ |
| 모듈 export | `api/django/views/xtest/__init__.py` | ✅ |
| 단위 테스트 19개 | `tests/self_healing/api/test_xtest_rate_limit_views.py` | ✅ |

---

## 1. 목적

Rate Limiter(L1)의 동작을 X-Test-Mode 환경에서 **관찰**할 수 있는 API 설계.

### 1.1 중요: Bypass vs Observe

| 기존 X-Test-Mode | 신규 추가 |
|-----------------|----------|
| Rate Limiter **우회** | Rate Limiter **관찰** |
| 테스트 트래픽 허용 | 실제 동작 확인 |

### 1.2 테스트 목표

| 목표 | 설명 |
|------|------|
| 상태 조회 | 현재 Rate Limit 상태 확인 |
| 임계값 확인 | 설정된 임계값 조회 |
| 히스토리 | 최근 Rate Limit 발생 기록 |
| Redis 상태 | L2 Redis 연결 상태 확인 |
| Fallback 상태 | L1 Fallback 모드 확인 |

### 1.3 Rate Limiter 구조 (코드 기준)

`api/django/rate_limit.py`에서:

| 계층 | 클래스 | 설명 |
|------|--------|------|
| L2 (Primary) | Redis 기반 | 분산 Rate Limit |
| L1 (Fallback) | `LocalMemoryRateLimiter` | Redis 장애 시 로컬 |
| Middleware | `HybridRateLimitMiddleware` | 통합 미들웨어 |

---

## 2. API 엔드포인트 설계

### 2.1 Rate Limit 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/rate-limit/status/`

**목적:** 현재 Rate Limit 상태 전체 조회

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| client_key | string | 특정 클라이언트 키 조회 |

**응답:**

| 필드 | 설명 |
|------|------|
| mode | 현재 모드 (normal, emergency, degraded) |
| redis_healthy | Redis 연결 상태 |
| fallback_active | L1 Fallback 활성화 여부 |
| current_config | 현재 적용 설정 |
| global_stats | 전역 통계 |

### 2.2 클라이언트별 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/rate-limit/client/`

**목적:** 특정 클라이언트의 Rate Limit 상태

**쿼리 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| client_key | string | O | 클라이언트 식별자 (IP 등) |
| window | string | X | 윈도우 타입 (minute, hour) |

**응답:**

| 필드 | 설명 |
|------|------|
| client_key | 클라이언트 키 |
| current_count | 현재 요청 수 |
| limit | 제한 값 |
| remaining | 남은 요청 수 |
| reset_at | 리셋 시간 |
| blocked | 현재 차단 상태 |

### 2.3 Rate Limit 히스토리

**엔드포인트:** `GET /api/self-healing/xtest/rate-limit/history/`

**목적:** 최근 Rate Limit 발생 기록 조회

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| limit | int | 조회 개수 (기본 20, 최대 100) |
| client_key | string | 특정 클라이언트 필터 |

**응답:**

| 필드 | 설명 |
|------|------|
| total_exceeded | 총 Rate Limit 초과 횟수 |
| recent_events | 최근 이벤트 목록 |
| by_client | 클라이언트별 집계 |

### 2.4 설정 조회

**엔드포인트:** `GET /api/self-healing/xtest/rate-limit/config/`

**목적:** 현재 Rate Limit 설정 조회

**응답:**

| 필드 | 설명 |
|------|------|
| source | 설정 소스 (runtime, settings, fallback) |
| normal_config | 일반 모드 설정 |
| emergency_config | 긴급 모드 설정 |
| path_prefix | 적용 경로 프리픽스 |
| excluded_paths | 제외 경로 목록 |

### 2.5 Rate Limit 카운터 초기화 (테스트용)

**엔드포인트:** `POST /api/self-healing/xtest/rate-limit/reset/`

**목적:** 테스트용 Rate Limit 카운터 초기화

**요청 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| client_key | string | 특정 클라이언트만 (선택) |
| reset_all | bool | 전체 초기화 (기본 false) |

**응답:**

| 필드 | 설명 |
|------|------|
| reset_count | 초기화된 키 수 |
| clients_reset | 초기화된 클라이언트 목록 |

---

## 3. 서비스 레이어 연동

### 3.1 기존 Rate Limiter 활용

| 함수/클래스 | 위치 | 용도 |
|------------|------|------|
| `get_rate_limit_config()` | `api/django/rate_limit.py` | 설정 조회 |
| `LocalMemoryRateLimiter` | `api/django/rate_limit.py` | L1 상태 조회 |
| `_reset_state()` | `api/django/rate_limit.py` | 테스트용 초기화 |

### 3.2 Redis 상태 확인

| 함수 | 위치 | 용도 |
|------|------|------|
| `RedisHealthChecker.is_healthy()` | `api/django/rate_limit.py` | Redis 상태 |
| `_get_redis_client()` | `api/django/rate_limit.py` | Redis 클라이언트 |

---

## 4. 구현 순서

### Step 1: View 파일 생성

**파일:** `api/django/views/xtest/rate_limit.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `RateLimitStatusView` | 전체 상태 |
| 1-2 | `RateLimitClientView` | 클라이언트별 상태 |
| 1-3 | `RateLimitHistoryView` | 히스토리 |
| 1-4 | `RateLimitConfigXTestView` | 설정 조회 |
| 1-5 | `RateLimitResetView` | 카운터 초기화 |

### Step 2: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 2-1 | `xtest/rate-limit/status/` | `RateLimitStatusView` |
| 2-2 | `xtest/rate-limit/client/` | `RateLimitClientView` |
| 2-3 | `xtest/rate-limit/history/` | `RateLimitHistoryView` |
| 2-4 | `xtest/rate-limit/config/` | `RateLimitConfigXTestView` |
| 2-5 | `xtest/rate-limit/reset/` | `RateLimitResetView` |

### Step 3: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가

### Step 4: 히스토리 수집 (선택)

Rate Limit 이벤트를 수집하려면:

- 기존 `FALLBACK_LOG_PATH` 활용 또는
- 인메모리 링 버퍼 추가

### Step 5: 테스트 작성

**파일:** `tests/unit/api/xtest/test_rate_limit_views.py`

| 테스트 케이스 |
|--------------|
| status 조회 테스트 |
| client 상태 테스트 |
| history 조회 테스트 |
| config 조회 테스트 |
| reset 테스트 |

---

## 5. 테스트 시나리오

### 5.1 Rate Limit 상태 확인

```
1. GET /xtest/rate-limit/status/
   → mode: "normal"
   → redis_healthy: true
   → fallback_active: false
```

### 5.2 Redis 장애 시 Fallback 확인

```
1. Redis 연결 끊김 시뮬레이션
2. GET /xtest/rate-limit/status/
   → mode: "emergency"
   → redis_healthy: false
   → fallback_active: true
```

### 5.3 클라이언트 Rate Limit 확인

```
1. 특정 클라이언트 요청 다수 발생
2. GET /xtest/rate-limit/client/?client_key=192.168.1.1
   → current_count: 95
   → limit: 100
   → remaining: 5
   → blocked: false
```

---

## 6. 모드 정의

`api/django/rate_limit.py` 기준:

| 모드 | 조건 | Rate Limit |
|------|------|-----------|
| `normal` | Redis 정상 | 100 req/min |
| `emergency` | Redis 장애 | 10 req/min |
| `degraded` | 부분 장애 | 50 req/min |

---

## 7. 보안 고려사항

### 7.1 기본 보안 (XTestModeMixin)

- `X-Test-Mode: chaos-monkey` 헤더 필수
- 프로덕션 환경 완전 차단

### 7.2 추가 보안

| 항목 | 조치 |
|------|------|
| reset_all | 명시적 플래그 필요 |
| 히스토리 | 최대 100개 제한 |
| 클라이언트 정보 | IP 해싱 옵션 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `api/django/rate_limit.py` | LocalMemoryRateLimiter, get_rate_limit_config |
| `api/django/rate_limit.py` | HybridRateLimitMiddleware |
| `api/django/rate_limit.py` | RedisHealthChecker |
| `settings/api_rate_limit.py` | ApiRateLimitSettings |
| `api/django/views/xtest/base.py` | XTestModeMixin 패턴 |

---

**다음 문서:** 121_XTEST_MODE_IDEMPOTENCY.md

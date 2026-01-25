# 106. API 모듈 하드코딩된 설정값 리팩토링 계획

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **관련 문서**: 102_HARDCODED_CONFIG_FINAL_AUDIT.md
- **대상 디렉토리**: `packages/selfhealing-python/src/selfhealing/api/`

---

## 1. 개요

API 모듈(주로 Django 통합)에서 발견된 하드코딩된 설정값들을 Pydantic Settings 체계로 마이그레이션하는 상세 계획.

---

## 2. 대상 파일 및 설정값

### 2.1 api/django/rate_limit.py

**위치**: L52-57  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_RATE_PER_MINUTE` | 60 | 분당 기본 요청 수 | `SELFHEALING_API_RATE_DEFAULT_PER_MINUTE` |
| `DEFAULT_RATE_PER_HOUR` | 1000 | 시간당 기본 요청 수 | `SELFHEALING_API_RATE_DEFAULT_PER_HOUR` |
| `DEFAULT_BURST_SIZE` | 10 | 기본 버스트 크기 | `SELFHEALING_API_RATE_DEFAULT_BURST` |
| `MIN_RETRY_AFTER_SECONDS` | 1 | 최소 재시도 대기 시간 | `SELFHEALING_API_RATE_MIN_RETRY_AFTER` |
| `MAX_RETRY_AFTER_SECONDS` | 3600 | 최대 재시도 대기 시간 | `SELFHEALING_API_RATE_MAX_RETRY_AFTER` |
| `DEFAULT_WINDOW_SIZE_SECONDS` | 60 | 기본 윈도우 크기 | `SELFHEALING_API_RATE_WINDOW_SIZE` |

---

### 2.2 api/django/rate_limit.py (추가)

**위치**: L128-135  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `SLIDING_WINDOW_PRECISION` | 100 | 슬라이딩 윈도우 정밀도 | `SELFHEALING_API_RATE_SLIDING_PRECISION` |
| `TOKEN_BUCKET_REFILL_INTERVAL` | 1.0 | 토큰 버킷 리필 간격 | `SELFHEALING_API_RATE_TOKEN_REFILL_INTERVAL` |

---

### 2.3 api/django/middleware_health.py

**위치**: L85-88  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `HEALTH_CHECK_INTERVAL` | 30 | 헬스체크 간격 (초) | `SELFHEALING_API_HEALTH_CHECK_INTERVAL` |
| `UNHEALTHY_THRESHOLD` | 3 | 비정상 임계값 | `SELFHEALING_API_HEALTH_UNHEALTHY_THRESHOLD` |
| `RECOVERY_THRESHOLD` | 2 | 복구 임계값 | `SELFHEALING_API_HEALTH_RECOVERY_THRESHOLD` |

---

### 2.4 api/django/circuit_breaker_middleware.py

**위치**: L42-46  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `FAILURE_THRESHOLD` | 5 | 실패 임계값 | `SELFHEALING_API_CB_FAILURE_THRESHOLD` |
| `RECOVERY_TIMEOUT` | 30 | 복구 타임아웃 (초) | `SELFHEALING_API_CB_RECOVERY_TIMEOUT` |
| `HALF_OPEN_MAX_CALLS` | 3 | Half-Open 최대 호출 | `SELFHEALING_API_CB_HALF_OPEN_MAX` |

---

### 2.5 api/django/timeout_middleware.py

**위치**: L38-40  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_TIMEOUT_SECONDS` | 30 | 기본 타임아웃 | `SELFHEALING_API_DEFAULT_TIMEOUT` |
| `MAX_TIMEOUT_SECONDS` | 300 | 최대 타임아웃 | `SELFHEALING_API_MAX_TIMEOUT` |

---

### 2.6 api/fastapi/dependencies.py (존재 시)

**예상 위치**: 함수 기본값

| 설정 | 용도 | 환경변수명 (제안) |
|-----|------|------------------|
| 요청 제한 | 요청 제한 설정 | `SELFHEALING_FASTAPI_RATE_*` |
| 타임아웃 | 타임아웃 설정 | `SELFHEALING_FASTAPI_TIMEOUT_*` |

---

## 3. 신규/확장 Settings 모듈 구조

```
settings/
├── api_rate_limit.py        # NEW - Rate Limiting 전용
├── api_middleware.py        # NEW - 미들웨어 통합
│   ├── health_check
│   ├── circuit_breaker
│   └── timeout
└── ... (기존 파일들)
```

---

## 4. settings/api_rate_limit.py 설계

### 구조
```
ApiRateLimitSettings(BaseSettings)
├── default_rate_per_minute: int = 60
├── default_rate_per_hour: int = 1000
├── default_burst_size: int = 10
├── min_retry_after_seconds: int = 1
├── max_retry_after_seconds: int = 3600
├── default_window_size_seconds: int = 60
├── sliding_window_precision: int = 100
└── token_bucket_refill_interval: float = 1.0
```

### 환경변수 Prefix
`SELFHEALING_API_RATE_`

---

## 5. settings/api_middleware.py 설계

### 구조
```
ApiMiddlewareSettings(BaseSettings)
├── health_check_interval: int = 30
├── health_unhealthy_threshold: int = 3
├── health_recovery_threshold: int = 2
├── circuit_breaker_failure_threshold: int = 5
├── circuit_breaker_recovery_timeout: int = 30
├── circuit_breaker_half_open_max: int = 3
├── default_timeout_seconds: int = 30
└── max_timeout_seconds: int = 300
```

### 환경변수 Prefix
`SELFHEALING_API_MIDDLEWARE_`

---

## 6. 구현 순서

### Step 1: 신규 Settings 생성 (1일)
1. `settings/api_rate_limit.py` 생성
2. `settings/api_middleware.py` 생성
3. 환경변수 파싱 및 검증 로직 추가

### Step 2: Rate Limit 모듈 리팩토링 (1일)
4. `api/django/rate_limit.py` - settings 연동
5. Rate Limit 관련 테스트 업데이트

### Step 3: Middleware 모듈 리팩토링 (1일)
6. `api/django/middleware_health.py` - settings 연동
7. `api/django/circuit_breaker_middleware.py` - settings 연동
8. `api/django/timeout_middleware.py` - settings 연동
9. 미들웨어 테스트 업데이트

### Step 4: FastAPI 통합 (해당 시) (0.5일)
10. `api/fastapi/` 모듈 검토 및 적용

### Step 5: 테스트 및 문서화 (0.5일)
11. 통합 테스트 검증
12. 환경변수 문서 업데이트

---

## 7. 예상 소요 시간

| 단계 | 예상 소요 |
|-----|----------|
| 신규 Settings 생성 | 1일 |
| Rate Limit 리팩토링 | 1일 |
| Middleware 리팩토링 | 1일 |
| FastAPI 통합 | 0.5일 |
| 테스트 및 문서화 | 0.5일 |
| **총계** | **4일** |

---

## 8. 환경별 권장 설정

### 개발 환경
```
SELFHEALING_API_RATE_DEFAULT_PER_MINUTE=1000  # 개발 시 제한 완화
SELFHEALING_API_DEFAULT_TIMEOUT=60             # 디버깅 시간 확보
```

### 스테이징 환경
```
SELFHEALING_API_RATE_DEFAULT_PER_MINUTE=100   # 프로덕션 근사치
SELFHEALING_API_DEFAULT_TIMEOUT=30
```

### 프로덕션 환경
```
SELFHEALING_API_RATE_DEFAULT_PER_MINUTE=60    # 기본값 사용
SELFHEALING_API_RATE_DEFAULT_PER_HOUR=1000
SELFHEALING_API_RATE_DEFAULT_BURST=10
SELFHEALING_API_DEFAULT_TIMEOUT=30
```

---

## 9. Rate Limit 알고리즘별 설정 분리

현재 `rate_limit.py`에서 여러 알고리즘을 지원하는 경우:

### Sliding Window 설정
```
SELFHEALING_API_RATE_SLIDING_PRECISION=100
SELFHEALING_API_RATE_SLIDING_WINDOW_SIZE=60
```

### Token Bucket 설정
```
SELFHEALING_API_RATE_TOKEN_REFILL_INTERVAL=1.0
SELFHEALING_API_RATE_TOKEN_BUCKET_SIZE=10
```

### Fixed Window 설정
```
SELFHEALING_API_RATE_FIXED_WINDOW_SIZE=60
```

---

## 10. 위험 요소 및 완화 방안

| 위험 | 영향도 | 완화 방안 |
|-----|-------|----------|
| Rate Limit 설정 변경 시 서비스 영향 | 높음 | 단계적 롤아웃, A/B 테스트 |
| 타임아웃 변경 시 요청 실패 | 중간 | 충분한 기본값 유지 |
| Circuit Breaker 민감도 변경 | 높음 | 프로덕션 트래픽 분석 후 조정 |

---

## 11. 모니터링 통합

Rate Limit 및 Middleware 설정은 모니터링과 밀접하므로:

1. 설정 변경 시 메트릭 태그에 현재 설정값 포함
2. 임계값 근접 시 알림 트리거
3. 설정 변경 이력 audit 로그 기록

---

## 12. 검증 체크리스트

- [ ] Rate Limit 기본값으로 기존 동작 유지
- [ ] 환경변수 오버라이드 정상 작동
- [ ] 미들웨어 체이닝 정상 동작
- [ ] 429 응답 시 Retry-After 헤더 정상 출력
- [ ] Circuit Breaker 상태 전이 정상
- [ ] 타임아웃 처리 정상
- [ ] 헬스체크 엔드포인트 정상 동작

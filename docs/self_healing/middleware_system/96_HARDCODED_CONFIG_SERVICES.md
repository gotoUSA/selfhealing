# 96. 서비스 계층 설정 외부화

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94 (개요), 95 (API 뷰)
- **대상 경로**: `services/`, `adapters/`

---

## 1. 대상 파일 목록

| 파일 | 하드코딩 항목 수 | 기존 Settings 유무 | 우선순위 |
|------|----------------|-------------------|----------|
| `services/dlq_models.py` | 6건 | O (dlq.py) | 높음 |
| `services/corruption_shield/config.py` | 5건 | O (corruption_shield.py) | 높음 |
| `services/coordination/recovery_circuit_breaker.py` | 6건 | O (recovery_circuit_breaker.py) | 높음 |
| `services/coordination/redis_key_guard.py` | 6건 | O (redis_key_guard.py) | 중간 |
| `services/security/models.py` | 5건 | O (security.py) | 중간 |
| `services/chaos/blast_radius.py` | 8건 | O (chaos_blast_radius.py) | 중간 |
| `services/chaos/base/models.py` | 4건 | △ (일부만) | 중간 |
| `services/precomputed_cache.py` | 3건 | X | 낮음 |
| `services/coordination/regional_recovery_policy.py` | 5건 | O (regional_recovery_policy.py) | 낮음 |
| `services/canary/cross_cluster.py` | 3건 | X | 낮음 |
| `services/error_budget/constants.py` | 6건 | O (error_budget.py) | 낮음 |
| `adapters/celery/signal_hooks.py` | 2건 | X | 낮음 |

---

## 2. dlq_models.py 상세

### 2.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 25 | `retention_days` | `30` | DLQ 항목 보존 기간 |
| 26 | `max_replay_attempts` | `2` | 최대 리플레이 시도 횟수 |
| 27 | `max_retries` | `3` | 최대 재시도 횟수 |
| 28 | `retry_delay` | `60` | 재시도 지연 시간(초) |
| 29 | `expiry_hours` | `72` | 만료 시간(시간) |
| 30 | `batch_size` | `10` | 배치 크기 |
| 135 | `page_size` | `20` | 페이지 크기 |

### 2.2 현황 분석
- `settings/dlq.py`에 이미 DLQ 관련 Settings 존재
- `dlq_models.py`의 dataclass가 Settings를 참조하지 않고 기본값 직접 정의

### 2.3 구현 순서

1. **Step 1**: `settings/dlq.py` 확장
   - 누락된 설정 항목 추가 (page_size 등)
   
2. **Step 2**: `dlq_models.py` 수정
   - `DLQRuntimeConfig` 클래스에 `from_settings()` 팩토리 메서드 추가
   - 기존 생성 코드에서 Settings 연동

### 2.4 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_DLQ_DEFAULT_PAGE_SIZE` | `20` | 페이지네이션 기본 크기 |

---

## 3. corruption_shield/config.py 상세

### 3.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 32 | `max_string_length` | `1000` | 문자열 최대 길이 |
| 35 | `min_amount` | `100` | 최소 금액 (원) |
| 36 | `max_amount` | `100_000_000` | 최대 금액 (1억 원) |
| 42 | `min_samples_for_anomaly` | `10` | 이상 탐지 최소 샘플 수 |

### 3.2 현황 분석
- `settings/corruption_shield.py`에 대부분의 설정 존재
- `services/corruption_shield/config.py`의 dataclass가 Settings를 참조하지 않음

### 3.3 구현 순서

1. **Step 1**: `settings/corruption_shield.py` 검토 및 필요시 확장
2. **Step 2**: `config.py`에 `from_settings()` 팩토리 메서드 추가
3. **Step 3**: 기존 사용처에서 팩토리 메서드 사용

---

## 4. coordination/recovery_circuit_breaker.py 상세

### 4.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 71 | `sampling_window_seconds` | `60` | 샘플링 윈도우 |
| 75 | `min_samples` | `10` | 최소 샘플 수 |
| 79 | `open_duration_seconds` | `300` | 오픈 상태 유지 시간 |
| 83 | `half_open_max_requests` | `5` | 하프오픈 최대 요청 |
| 87 | `max_consecutive_trips` | `3` | 최대 연속 트립 |

### 4.2 현황 분석
- `settings/recovery_circuit_breaker.py`에 대부분 정의됨
- `RecoveryCircuitBreakerConfig` dataclass가 Settings 연동 안됨

### 4.3 구현 순서

1. **Step 1**: `settings/recovery_circuit_breaker.py` 완전성 검증
2. **Step 2**: `RecoveryCircuitBreakerConfig`에 `from_settings()` 추가
3. **Step 3**: 사용처에서 팩토리 메서드 호출

---

## 5. coordination/redis_key_guard.py 상세

### 5.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 99 | `is_warning` | `80.0` | 경고 임계치 (%) |
| 103 | `is_critical` | `90.0` | 위험 임계치 (%) |
| 218 | 키 정책 | `3600` | 세션 TTL (초) |
| 224 | 키 정책 | `7200` | 캐시 TTL (초) |
| 231 | 키 정책 | `604800` | 분석 TTL (초) |
| 420 | `emergency_cleanup` | `20.0` | 목표 여유 비율 (%) |
| 521 | `batch_cleanup` | `100` | 배치 크기 |

### 5.2 현황 분석
- `settings/redis_key_guard.py`에 대부분 설정 존재
- 서비스 코드에서 Settings를 참조하지 않는 곳 있음

### 5.3 구현 순서

1. **Step 1**: `settings/redis_key_guard.py` 누락 항목 추가
2. **Step 2**: `redis_key_guard.py`에서 Settings 참조로 변경
3. **Step 3**: 함수 파라미터 기본값을 Settings 연동

---

## 6. security/models.py 상세

### 6.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 97 | `rate_limit_window_seconds` | `60` | Rate Limit 윈도우 |
| 98 | `rate_limit_max_requests` | `100` | Rate Limit 최대 요청 |
| 105 | `suspicious_ip_cache_timeout` | `86400` | 의심 IP 캐시 타임아웃 (24시간) |
| 108 | `injection_ban_hours` | `24` | 인젝션 차단 시간 |

### 6.2 현황 분석
- `settings/security.py`에 보안 설정 존재
- `SecurityConfig` dataclass가 Settings 연동 안됨

### 6.3 구현 순서

1. **Step 1**: `settings/security.py`에 누락 항목 추가
2. **Step 2**: `SecurityConfig`에 `from_settings()` 추가
3. **Step 3**: 사용처에서 팩토리 메서드 사용

---

## 7. chaos/blast_radius.py 상세

### 7.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 66 | `instance_max_concurrent` | `5` | 인스턴스 최대 동시 실행 |
| 69 | `service_max_concurrent` | `2` | 서비스 최대 동시 실행 |
| 72 | `region_max_concurrent` | `1` | 리전 최대 동시 실행 |
| 96 | `max_traffic_percent_instance` | `100.0` | 인스턴스 최대 트래픽 % |
| 99 | `max_traffic_percent_service` | `50.0` | 서비스 최대 트래픽 % |
| 102 | `max_traffic_percent_region` | `10.0` | 리전 최대 트래픽 % |
| 294 | `max_traffic_percent` | `100.0` | 기본 최대 트래픽 % |
| 295 | `max_concurrent` | `5` | 기본 최대 동시 실행 |

### 7.2 현황 분석
- `settings/chaos_blast_radius.py`에 설정 존재
- dataclass 필드 기본값이 Settings와 중복

### 7.3 구현 순서

1. **Step 1**: `BlastRadiusLimits`, `TrafficLimits` 등에 `from_settings()` 추가
2. **Step 2**: 기존 기본값 제거하고 Settings 연동

---

## 8. chaos/base/models.py 상세

### 8.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 187 | `p50_latency_max_ms` | `100.0` | P50 레이턴시 최대값 |
| 188 | `p99_latency_max_ms` | `500.0` | P99 레이턴시 최대값 |
| 191 | `error_rate_max_percent` | `0.1` | 에러율 최대값 |
| 194 | `throughput_min_rps` | `100.0` | 최소 처리량 |

### 8.2 구현 순서

1. **Step 1**: `settings/chaos_experiment.py` 또는 신규 Settings에 항목 추가
2. **Step 2**: `SteadyStateThresholds`에 `from_settings()` 추가

---

## 9. precomputed_cache.py 상세

### 9.1 발견된 하드코딩

| 라인 | 변수명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 67 | `L2_TTL_SECONDS` | `15.0` | Redis 캐시 TTL |
| 68 | `REFRESH_INTERVAL` | `10.0` | 백그라운드 갱신 주기 |
| 111 | `maxsize` | `100` | L1 캐시 최대 크기 |

### 9.2 현황 분석
- 전용 Settings 없음
- 신규 Settings 생성 필요

### 9.3 구현 순서

1. **Step 1**: `settings/precomputed_cache.py` 생성
2. **Step 2**: 모듈 상수를 Settings 참조로 변경

### 9.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_PRECOMPUTED_CACHE_L2_TTL_SECONDS` | `15.0` | L2 캐시 TTL |
| `SELFHEALING_PRECOMPUTED_CACHE_REFRESH_INTERVAL` | `10.0` | 갱신 주기 |
| `SELFHEALING_PRECOMPUTED_CACHE_L1_MAXSIZE` | `100` | L1 캐시 크기 |

---

## 10. 기타 서비스 파일

### 10.1 coordination/regional_recovery_policy.py

| 라인 | 필드명 | 하드코딩 값 |
|------|--------|------------|
| 61 | `stability_check_duration_minutes` | `10` |
| 80 | `canary_resume_wait_after_seconds` | `60` |
| 83 | `governance_normal_wait_after_seconds` | `300` |
| 95 | `approval_timeout_minutes` | `60` |

- 기존 Settings: `settings/regional_recovery_policy.py`
- 조치: `from_settings()` 팩토리 추가

### 10.2 canary/cross_cluster.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 388 | `timeout` | `10` |
| 599 | `default_expiry_hours` | `24` |
| 848 | `ttl` | `604800` (7일) |

- 기존 Settings: 없음
- 조치: `settings/canary.py` 확장 또는 신규 생성

### 10.3 error_budget/constants.py

| 라인 | 상수명 | 하드코딩 값 |
|------|--------|------------|
| 34 | `MAX_CRISIS_MULTIPLIER_CAP` | `10.0` |
| 42 | `MAX_DOMAIN_MULTIPLIER` | `24.0` |
| 50 | `MAX_COMBINED_MULTIPLIER` | `10.0` |
| 62 | `DEFAULT_CACHE_TTL_SECONDS` | `30.0` |
| 133 | `DEFAULT_PROPAGATION_MAX_HOPS` | `3` |
| 153 | `REFUND_PROPOSAL_EXPIRY_HOURS` | `24` |

- 기존 Settings: `settings/error_budget.py`, `settings/error_budget_propagation.py`
- 조치: constants.py를 Settings 참조로 변경

### 10.4 adapters/celery/signal_hooks.py

| 라인 | 상수 | 하드코딩 값 |
|------|------|------------|
| 27 | `SELFHEALING_CB_RECOVERY_TIMEOUT` | `60` |
| 647 | `max_items` | `50` |

- 조치: 기존 circuit_breaker Settings 확장

---

## 11. 전체 구현 순서 요약

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1 | `dlq_models.py` - Settings 연동 | 30분 |
| 2 | `corruption_shield/config.py` - Settings 연동 | 30분 |
| 3 | `recovery_circuit_breaker.py` - Settings 연동 | 30분 |
| 4 | `redis_key_guard.py` - Settings 연동 | 30분 |
| 5 | `security/models.py` - Settings 연동 | 20분 |
| 6 | `chaos/blast_radius.py` - Settings 연동 | 30분 |
| 7 | `chaos/base/models.py` - Settings 연동 | 20분 |
| 8 | `settings/precomputed_cache.py` 생성 | 20분 |
| 9 | `precomputed_cache.py` - Settings 연동 | 15분 |
| 10 | `regional_recovery_policy.py` - Settings 연동 | 20분 |
| 11 | `canary/cross_cluster.py` - Settings 연동 | 25분 |
| 12 | `error_budget/constants.py` - Settings 연동 | 25분 |
| 13 | `signal_hooks.py` - Settings 연동 | 15분 |
| 14 | 테스트 실행 및 검증 | 40분 |
| **총계** | | **약 6시간** |

---

## 12. 검증 체크리스트

- [ ] `dlq_models.py` 하드코딩 7건 제거
- [ ] `corruption_shield/config.py` 하드코딩 4건 제거
- [ ] `recovery_circuit_breaker.py` 하드코딩 5건 제거
- [ ] `redis_key_guard.py` 하드코딩 6건 제거
- [ ] `security/models.py` 하드코딩 4건 제거
- [ ] `chaos/blast_radius.py` 하드코딩 8건 제거
- [ ] `chaos/base/models.py` 하드코딩 4건 제거
- [ ] `settings/precomputed_cache.py` 생성됨
- [ ] `precomputed_cache.py` 하드코딩 3건 제거
- [ ] 기타 파일 하드코딩 제거
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

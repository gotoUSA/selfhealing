# 100. 추가 발견 하드코딩 설정 외부화

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94-99
- **대상**: 문서 94-99에서 다루지 않은 추가 발견 하드코딩

---

## 1. 개요

이 문서는 기존 문서(94-99)에서 다루지 않은 추가 발견된 하드코딩 설정을 정리합니다.

---

## 2. core/ 모듈 (12건)

### 2.1 core/backoff.py

| 라인 | 클래스/함수 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 44 | `ExponentialBackoff` | `base_delay=1.0` | 기본 지연 |
| 45 | `ExponentialBackoff` | `max_delay=300.0` | 최대 지연 |
| 46 | `ExponentialBackoff` | `multiplier=2.0` | 승수 |
| 49 | `ExponentialBackoff` | `jitter_factor=0.2` | 지터 팩터 |
| 74 | `LinearBackoff` | `base_delay=1.0` | 기본 지연 |
| 75 | `LinearBackoff` | `increment=1.0` | 증분 |
| 76 | `LinearBackoff` | `max_delay=60.0` | 최대 지연 |
| 80 | `LinearBackoff` | `jitter_factor=0.1` | 지터 팩터 |
| 106 | `ConstantBackoff` | `delay=5.0` | 고정 지연 |
| 109 | `ConstantBackoff` | `jitter_factor=0.1` | 지터 팩터 |

### 2.2 core/connection_health.py

| 라인 | 클래스/함수 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 114 | `DefaultConnectionHealthMonitor` | `failure_threshold=3` | 실패 임계값 |

### 2.3 core/pool_monitor.py

| 라인 | 클래스/함수 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 114 | `ConnectionPoolMonitor` | `warning_threshold=70.0` | 경고 임계값 % |
| 115 | `ConnectionPoolMonitor` | `critical_threshold=90.0` | 임계 임계값 % |
| 116 | `ConnectionPoolMonitor` | `leak_threshold_seconds=300.0` | 누수 임계값 초 |

### 2.4 core/decision_engine.py

| 라인 | 클래스/함수 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 119 | `DecisionEngine` | `MIN_CHANGE_RATIO=0.05` | 최소 변경 비율 |

### 2.5 core/apply_strategy.py

| 라인 | 클래스/함수 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 30 | `ApplyOptions` | `delay_seconds=0` | 지연 초 |
| 31 | `ApplyOptions` | `grace_timeout_seconds=60` | 유예 타임아웃 초 |
| 73-74 | `DefaultApplyConfig` | `delay_seconds=0, grace_timeout_seconds=60` | 기본 적용 설정 |

### 2.6 Settings 신규 필요: `settings/backoff.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_BACKOFF_EXPONENTIAL_BASE_DELAY` | `1.0` | Exponential 기본 지연 |
| `SELFHEALING_BACKOFF_EXPONENTIAL_MAX_DELAY` | `300.0` | Exponential 최대 지연 |
| `SELFHEALING_BACKOFF_EXPONENTIAL_MULTIPLIER` | `2.0` | Exponential 승수 |
| `SELFHEALING_BACKOFF_LINEAR_BASE_DELAY` | `1.0` | Linear 기본 지연 |
| `SELFHEALING_BACKOFF_LINEAR_INCREMENT` | `1.0` | Linear 증분 |
| `SELFHEALING_BACKOFF_LINEAR_MAX_DELAY` | `60.0` | Linear 최대 지연 |
| `SELFHEALING_BACKOFF_CONSTANT_DELAY` | `5.0` | Constant 지연 |

### 2.7 Settings 신규 필요: `settings/pool_monitor.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_POOL_WARNING_THRESHOLD` | `70.0` | 경고 임계값 % |
| `SELFHEALING_POOL_CRITICAL_THRESHOLD` | `90.0` | 임계 임계값 % |
| `SELFHEALING_POOL_LEAK_THRESHOLD_SECONDS` | `300.0` | 누수 감지 초 |

---

## 3. services/error_budget/ 모듈 (9건)

### 3.1 error_budget/constants.py

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 34 | `MAX_CRISIS_MULTIPLIER_CAP` | `10.0` | 위기 승수 캡 |
| 42 | `MAX_DOMAIN_MULTIPLIER` | `24.0` | 도메인 승수 최대값 |
| 50 | `MAX_COMBINED_MULTIPLIER` | `10.0` | 결합 승수 최대값 |
| 62 | `DEFAULT_CACHE_TTL_SECONDS` | `30.0` | 캐시 TTL |
| 126 | `DEFAULT_PROPAGATION_DECAY` | `0.5` | 전파 감쇠율 |
| 133 | `DEFAULT_PROPAGATION_MAX_HOPS` | `3` | 최대 전파 홉 |
| 146 | `DEFAULT_REFUND_RATIO` | `0.5` | 환불 비율 |
| 153 | `REFUND_PROPOSAL_EXPIRY_HOURS` | `24` | 환불 제안 만료 시간 |

### 3.2 error_budget/backfill.py

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 197 | `MAX_BACKFILL_HOURS` | `2` | 최대 소급 시간 |
| 201 | `DEFAULT_LOOKBACK_MINUTES` | `30` | 기본 소급 시간 |

### 3.3 Settings 확장 필요: `settings/error_budget_propagation.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_ERRORBUDGET_MAX_CRISIS_MULTIPLIER_CAP` | `10.0` | 위기 승수 캡 |
| `SELFHEALING_ERRORBUDGET_MAX_DOMAIN_MULTIPLIER` | `24.0` | 도메인 승수 최대값 |
| `SELFHEALING_ERRORBUDGET_MAX_COMBINED_MULTIPLIER` | `10.0` | 결합 승수 최대값 |
| `SELFHEALING_ERRORBUDGET_DEFAULT_CACHE_TTL_SECONDS` | `30.0` | 캐시 TTL |
| `SELFHEALING_ERRORBUDGET_REFUND_RATIO` | `0.5` | 환불 비율 |
| `SELFHEALING_ERRORBUDGET_REFUND_EXPIRY_HOURS` | `24` | 환불 제안 만료 시간 |

---

## 4. services/namespace_emergency/ 모듈 (6건)

### 4.1 cascade_detector.py

| 라인 | 상수/필드 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 44 | `DEFAULT_ESCALATION_THRESHOLD` | `2` | 격상 임계값 |
| 47 | `DEFAULT_CASCADE_WINDOW_MINUTES` | `30` | 연쇄 감지 윈도우 |
| 143 | `self._max_history_size` | `100` | 히스토리 최대 크기 |

### 4.2 tracker.py

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 48 | `DEFAULT_EMERGENCY_EXPIRY_HOURS` | `8` | 비상 만료 시간 |
| 51 | `CACHE_TTL_SECONDS` | `30.0` | 캐시 TTL |

### 4.3 escalation_audit.py

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 203 | `max_buffer_size` | `1000` | 버퍼 최대 크기 |

### 4.4 Settings 신규 필요: `settings/namespace_emergency.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_NAMESPACE_EMERGENCY_ESCALATION_THRESHOLD` | `2` | 격상 임계값 |
| `SELFHEALING_NAMESPACE_EMERGENCY_CASCADE_WINDOW_MINUTES` | `30` | 연쇄 감지 윈도우 |
| `SELFHEALING_NAMESPACE_EMERGENCY_EXPIRY_HOURS` | `8` | 비상 만료 시간 |
| `SELFHEALING_NAMESPACE_EMERGENCY_CACHE_TTL_SECONDS` | `30.0` | 캐시 TTL |

---

## 5. services/canary/ 모듈 (4건)

### 5.1 service.py

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 103 | `ROLLOUT_TTL_DAYS` | `7` | 롤아웃 TTL 일수 |

### 5.2 cross_cluster.py

| 라인 | 파라미터/상수 | 하드코딩 | 설명 |
|------|--------------|----------|------|
| 388 | `timeout` | `10` | 타임아웃 |
| 599 | `default_expiry_hours` | `24` | 기본 만료 시간 |

### 5.3 Settings 확장 필요: `settings/canary.py` (신규)

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CANARY_ROLLOUT_TTL_DAYS` | `7` | 롤아웃 TTL 일수 |
| `SELFHEALING_CANARY_CROSS_CLUSTER_TIMEOUT` | `10` | 타임아웃 |
| `SELFHEALING_CANARY_DEFAULT_EXPIRY_HOURS` | `24` | 기본 만료 시간 |

---

## 6. slo.py (4건)

| 라인 | 필드/파라미터 | 하드코딩 | 설명 |
|------|--------------|----------|------|
| 85 | `window_days` | `30` | SLO 윈도우 일수 |
| 95 | `fast_burn_rate` | `14.4` | 빠른 소진율 |
| 96 | `slow_burn_rate` | `3.0` | 느린 소진율 |
| 232 | `default_window_days` | `30` | 기본 윈도우 일수 |

### Settings 확장 필요: `settings/slo.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_SLO_FAST_BURN_RATE` | `14.4` | 빠른 소진율 |
| `SELFHEALING_SLO_SLOW_BURN_RATE` | `3.0` | 느린 소진율 |

---

## 7. tasks/canary_watchdog.py (4건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 64 | `zombie_threshold_minutes` | `30` | Zombie 임계값 분 |
| 65 | `auto_rollback_after_minutes` | `60` | 자동 롤백 분 |
| 66 | `max_stage_duration_minutes` | `15` | 최대 단계 체류 분 |

### Settings 신규 필요: `settings/canary_watchdog.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CANARY_WATCHDOG_ZOMBIE_THRESHOLD_MINUTES` | `30` | Zombie 임계값 분 |
| `SELFHEALING_CANARY_WATCHDOG_AUTO_ROLLBACK_MINUTES` | `60` | 자동 롤백 분 |
| `SELFHEALING_CANARY_WATCHDOG_MAX_STAGE_DURATION_MINUTES` | `15` | 최대 단계 체류 분 |

---

## 8. services/throttle/adaptive.py (3건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 62 | `smoothing_factor` | `0.5` | 평활 팩터 |
| 63 | `sample_window_seconds` | `10.0` | 샘플 윈도우 초 |
| 64 | `min_samples` | `3` | 최소 샘플 수 |

### Settings 확장 필요: `settings/throttle.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_THROTTLE_SMOOTHING_FACTOR` | `0.5` | 평활 팩터 |
| `SELFHEALING_THROTTLE_SAMPLE_WINDOW_SECONDS` | `10.0` | 샘플 윈도우 초 |
| `SELFHEALING_THROTTLE_MIN_SAMPLES` | `3` | 최소 샘플 수 |

---

## 9. services/chaos/traffic_shaper.py (5건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 73 | `target_rps` | `100.0` | 목표 RPS |
| 80 | `burst_size` | `10` | 버스트 크기 |
| 84 | `max_concurrent` | `50` | 최대 동시 요청 |
| 88 | `target_latency_ms` | `200.0` | 목표 레이턴시 |
| 91 | `adjustment_interval_seconds` | `5.0` | 조정 간격 |

### Settings 확장 필요: `settings/chaos.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CHAOS_TRAFFIC_SHAPER_TARGET_RPS` | `100.0` | 목표 RPS |
| `SELFHEALING_CHAOS_TRAFFIC_SHAPER_BURST_SIZE` | `10` | 버스트 크기 |
| `SELFHEALING_CHAOS_TRAFFIC_SHAPER_MAX_CONCURRENT` | `50` | 최대 동시 요청 |
| `SELFHEALING_CHAOS_TRAFFIC_SHAPER_TARGET_LATENCY_MS` | `200.0` | 목표 레이턴시 |

---

## 10. services/chaos/constants.py (9건)

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 31 | `DISK_IO_MAX_LATENCY_MS` | `2000` | 디스크 IO 최대 지연 |
| 34 | `DISK_IO_MAX_FAILURE_RATE` | `0.30` | 디스크 IO 최대 실패율 |
| 37 | `REPLAY_FLOOD_MAX_ENTRIES` | `5000` | Replay Flood 최대 엔트리 |
| 40 | `REPLAY_FLOOD_MAX_RATE` | `500` | Replay Flood 최대 속도 |
| 43 | `CLOCK_SKEW_MAX_SECONDS` | `86400` | Clock Skew 최대 오차 |
| 46 | `BLACKHOLE_MAX_DURATION_SECONDS` | `300` | Network Blackhole 최대 지속 |
| 52 | `POOL_EXHAUSTION_MAX_DURATION_SECONDS` | `120` | Pool 고갈 최대 지속 |
| 55 | `POOL_EXHAUSTION_MAX_PERCENTAGE` | `0.50` | Pool 고갈 최대 비율 |
| 58 | `TLS_FAILURE_MAX_DURATION_SECONDS` | `180` | TLS 실패 최대 지속 |
| 61 | `TLS_FAILURE_MAX_RATE` | `0.25` | TLS 실패 최대 발생률 |

### Settings 신규 필요: `settings/chaos_safety_caps.py`

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CHAOS_DISK_IO_MAX_LATENCY_MS` | `2000` | 디스크 IO 최대 지연 |
| `SELFHEALING_CHAOS_DISK_IO_MAX_FAILURE_RATE` | `0.30` | 디스크 IO 최대 실패율 |
| `SELFHEALING_CHAOS_REPLAY_FLOOD_MAX_ENTRIES` | `5000` | Replay Flood 최대 엔트리 |
| `SELFHEALING_CHAOS_BLACKHOLE_MAX_DURATION_SECONDS` | `300` | Blackhole 최대 지속 |
| `SELFHEALING_CHAOS_POOL_EXHAUSTION_MAX_SECONDS` | `120` | Pool 고갈 최대 지속 |

---

## 11. audit/reconciler.py (6건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 41 | `check_interval_seconds` | `300.0` | 검증 주기 |
| 44 | `check_window_seconds` | `3600.0` | 검증 범위 |
| 47 | `resend_batch_size` | `50` | 재전송 배치 크기 |
| 50 | `max_resend_attempts` | `3` | 최대 재전송 시도 |
| 53 | `alert_threshold` | `10` | 알림 임계값 |
| 170 | `_confirmed_ids_max_size` | `10000` | 확인 ID 최대 크기 |

**비고**: 이미 `from_env()` 메서드 존재하나 Settings 패턴 미사용

---

## 12. audit/sync_worker.py (8건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 42 | `sync_interval_seconds` | `1.0` | 동기화 주기 |
| 45 | `batch_size` | `100` | 배치 크기 |
| 47 | `max_retries` | `3` | 최대 재시도 |
| 48 | `retry_delay_seconds` | `1.0` | 재시도 지연 |
| 49 | `retry_backoff_multiplier` | `2.0` | 재시도 백오프 승수 |
| 50 | `max_retry_delay_seconds` | `30.0` | 최대 재시도 지연 |
| 53 | `cleanup_after_seconds` | `3600.0` | 정리 기준 시간 |
| 56 | `metrics_interval_seconds` | `60.0` | 메트릭 리포팅 주기 |

**비고**: 이미 `from_env()` 메서드 존재하나 Settings 패턴 미사용

---

## 13. audit/audit_integration.py (3건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 65 | `batch_size` | `5` | 배치 크기 |
| 66 | `flush_interval_seconds` | `2.0` | 플러시 주기 |
| 68 | `max_queue_size` | `5000` | 최대 큐 크기 |

---

## 14. audit/audit_watchdog.py (4건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 77 | `timeout_seconds` | `5.0` | 타임아웃 |
| 86 | `heartbeat_interval_seconds` | `30.0` | Heartbeat 주기 |
| 89 | `missed_threshold` | `3` | 연속 실패 허용 |
| 418 | `max_age_seconds` | `60.0` | 최대 나이 |

**비고**: 이미 `from_env()` 메서드 존재하나 Settings 패턴 미사용

---

## 15. api/django/permissions.py (1건)

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 223 | `EMERGENCY_EXPIRY_HOURS` | `4` | 긴급 모드 자동 만료 시간 |

---

## 16. services/control_api_service.py (2건)

| 라인 | 코드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 330 | `timedelta(minutes=90)` | `90` | OPS 기본 TTL 분 |
| 705 | `timedelta(minutes=5)` | `5` | 캐시 조회 윈도우 분 |

---

## 17. 전체 구현 순서

| 순서 | 작업 | 파일 | 예상 소요 |
|------|------|------|----------|
| 1 | `settings/backoff.py` 생성 | 신규 | 20분 |
| 2 | `core/backoff.py` Settings 연동 | 수정 | 25분 |
| 3 | `settings/pool_monitor.py` 생성 | 신규 | 15분 |
| 4 | `core/pool_monitor.py` Settings 연동 | 수정 | 20분 |
| 5 | `core/connection_health.py` Settings 연동 | 수정 | 15분 |
| 6 | `settings/error_budget_propagation.py` 확장 | 수정 | 20분 |
| 7 | `services/error_budget/constants.py` Settings 연동 | 수정 | 25분 |
| 8 | `services/error_budget/backfill.py` Settings 연동 | 수정 | 15분 |
| 9 | `settings/namespace_emergency.py` 생성 | 신규 | 15분 |
| 10 | `services/namespace_emergency/*.py` Settings 연동 | 수정 | 30분 |
| 11 | `settings/canary.py` 생성 | 신규 | 15분 |
| 12 | `services/canary/*.py` Settings 연동 | 수정 | 20분 |
| 13 | `settings/slo.py` 확장 | 수정 | 15분 |
| 14 | `slo.py` Settings 연동 | 수정 | 20분 |
| 15 | `settings/canary_watchdog.py` 생성 | 신규 | 15분 |
| 16 | `tasks/canary_watchdog.py` Settings 연동 | 수정 | 20분 |
| 17 | `settings/throttle.py` 확장 | 수정 | 10분 |
| 18 | `services/throttle/adaptive.py` Settings 연동 | 수정 | 15분 |
| 19 | `settings/chaos.py` 확장 | 수정 | 15분 |
| 20 | `services/chaos/traffic_shaper.py` Settings 연동 | 수정 | 20분 |
| 21 | `settings/chaos_safety_caps.py` 생성 | 신규 | 20분 |
| 22 | `services/chaos/constants.py` Settings 연동 | 수정 | 20분 |
| 23 | `settings/audit_reconciler.py` 생성 | 신규 | 15분 |
| 24 | `audit/reconciler.py` Settings 연동 | 수정 | 20분 |
| 25 | `audit/sync_worker.py` Settings 연동 | 수정 | 20분 |
| 26 | `audit/audit_integration.py` Settings 연동 | 수정 | 15분 |
| 27 | `audit/audit_watchdog.py` Settings 연동 | 수정 | 15분 |
| 28 | `api/django/permissions.py` Settings 연동 | 수정 | 10분 |
| 29 | `services/control_api_service.py` Settings 연동 | 수정 | 15분 |
| 30 | 테스트 실행 및 검증 | - | 40분 |
| **총계** | | | **약 8시간** |

---

## 18. 검증 체크리스트

- [ ] `core/backoff.py` 하드코딩 10건 제거됨
- [ ] `core/pool_monitor.py` 하드코딩 3건 제거됨
- [ ] `core/connection_health.py` 하드코딩 1건 제거됨
- [ ] `core/decision_engine.py` 하드코딩 1건 제거됨
- [ ] `core/apply_strategy.py` 하드코딩 4건 제거됨
- [ ] `services/error_budget/constants.py` 하드코딩 8건 제거됨
- [ ] `services/error_budget/backfill.py` 하드코딩 2건 제거됨
- [ ] `services/namespace_emergency/*.py` 하드코딩 6건 제거됨
- [ ] `services/canary/*.py` 하드코딩 3건 제거됨
- [ ] `slo.py` 하드코딩 4건 제거됨
- [ ] `tasks/canary_watchdog.py` 하드코딩 3건 제거됨
- [ ] `services/throttle/adaptive.py` 하드코딩 3건 제거됨
- [ ] `services/chaos/traffic_shaper.py` 하드코딩 5건 제거됨
- [ ] `services/chaos/constants.py` 하드코딩 9건 제거됨
- [ ] `audit/reconciler.py` 하드코딩 6건 제거됨
- [ ] `audit/sync_worker.py` 하드코딩 8건 제거됨
- [ ] `audit/audit_integration.py` 하드코딩 3건 제거됨
- [ ] `audit/audit_watchdog.py` 하드코딩 4건 제거됨
- [ ] `api/django/permissions.py` 하드코딩 1건 제거됨
- [ ] `services/control_api_service.py` 하드코딩 2건 제거됨
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

---

## 19. 추가 발견 항목 (2차)

### 19.1 utils/jitter.py (8건)

| 라인 | 함수/파라미터 | 하드코딩 | 설명 |
|------|-------------|----------|------|
| 27 | `with_jitter()` | `max_delay_seconds=60.0` | 최대 지연 |
| 28 | `with_jitter()` | `min_delay_seconds=0.0` | 최소 지연 |
| 75 | `calculate_jitter()` | `max_delay_seconds=60.0` | 최대 지연 |
| 76 | `calculate_jitter()` | `min_delay_seconds=0.0` | 최소 지연 |
| 99 | `sleep_with_jitter()` | `max_delay_seconds=60.0` | 최대 지연 |
| 100 | `sleep_with_jitter()` | `min_delay_seconds=0.0` | 최소 지연 |
| 122 | `async_sleep_with_jitter()` | `max_delay_seconds=60.0` | 최대 지연 |
| 123 | `async_sleep_with_jitter()` | `min_delay_seconds=0.0` | 최소 지연 |

### 19.2 services/error_budget_gate/fault_detector.py (2건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 46 | `GateFaultDetector.__init__()` | `failure_threshold=5` | 실패 임계값 |
| 46 | `GateFaultDetector.__init__()` | `recovery_timeout=30` | 복구 타임아웃 |

### 19.3 services/coordination/critical_worker.py (다수 건)

| 라인 | 클래스/필드 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 87 | `WorkerQueueConfig` | `worker_count=1` | 기본 Worker 수 |
| 90 | `WorkerQueueConfig` | `concurrency=2` | 동시성 |
| 93 | `WorkerQueueConfig` | `prefetch_multiplier=1` | 프리페치 배수 |
| 226 | `queue_configs["critical"]` | `worker_count=2, concurrency=2, prefetch_multiplier=1` | Critical 큐 설정 |
| 234 | `queue_configs["high"]` | `worker_count=4, concurrency=4, prefetch_multiplier=2` | High 큐 설정 |
| 242 | `queue_configs["recovery"]` | `worker_count=4, concurrency=4, prefetch_multiplier=2` | Recovery 큐 설정 |
| 250 | `queue_configs["notifications"]` | `worker_count=2, concurrency=4, prefetch_multiplier=4` | Notifications 큐 설정 |
| 258 | `queue_configs["default"]` | `worker_count=8, concurrency=8, prefetch_multiplier=4` | Default 큐 설정 |

### 19.4 services/isolation/regional_gate.py (1건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 139 | `isolate_region()` | `duration_seconds=300` | 기본 격리 시간 |

### 19.5 services/idempotency_service.py (4건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 774 | `AntiFlappingWindow.__init__()` | `window_seconds=60` | 윈도우 크기 |
| 775 | `AntiFlappingWindow.__init__()` | `similarity_threshold=0.01` | 유사도 임계값 |
| 776 | `AntiFlappingWindow.__init__()` | `max_similar_changes=3` | 최대 유사 변경 횟수 |

### 19.6 services/forensic_audit_bridge.py (4건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 59 | `ForensicRateLimiter.__init__()` | `exception_limit=10` | 분당 예외 최대값 |
| 60 | `ForensicRateLimiter.__init__()` | `snapshot_limit=1` | 분당 스냅샷 최대값 |
| 61 | `ForensicRateLimiter.__init__()` | `anomaly_limit=5` | 분당 이상 최대값 |
| 64 | `ForensicRateLimiter.__init__()` | `window_seconds=60.0` | 윈도우 크기 |

### 19.7 services/finops/service.py (1건)

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 382 | `MAX_CHAOS_WEIGHT_MULTIPLIER` | `10.0` | Chaos 가중치 최대값 |

### 19.8 2차 추가 발견 Settings 필요 목록

| 파일명 | 항목 수 | 우선순위 |
|--------|---------|----------|
| `settings/jitter.py` | 2개 | 중간 |
| `settings/gate_fault.py` | 2개 | 중간 |
| `settings/critical_worker.py` 확장 | 5개 | 낮음 |
| `settings/regional_gate.py` | 1개 | 낮음 |
| `settings/anti_flapping.py` 확장 | 3개 | 중간 |
| `settings/forensic.py` | 4개 | 낮음 |
| `settings/finops.py` | 1개 | 낮음 |

---

## 20. 신규 Settings 파일 목록

| 파일명 | 항목 수 | 우선순위 |
|--------|---------|----------|
| `settings/backoff.py` | 7개 | 높음 |
| `settings/pool_monitor.py` | 3개 | 높음 |
| `settings/namespace_emergency.py` | 4개 | 중간 |
| `settings/canary.py` | 3개 | 중간 |
| `settings/canary_watchdog.py` | 3개 | 중간 |
| `settings/chaos_safety_caps.py` | 10개 | 낮음 |
| `settings/audit_reconciler.py` | 6개 | 낮음 |
| `settings/jitter.py` | 2개 | 중간 |
| `settings/gate_fault.py` | 2개 | 중간 |
| `settings/regional_gate.py` | 1개 | 낮음 |
| `settings/forensic.py` | 4개 | 낮음 |
| `settings/finops.py` | 1개 | 낮음 |

---

## 21. 확장 필요 기존 Settings 목록

| 파일명 | 추가 항목 수 |
|--------|-------------|
| `settings/error_budget_propagation.py` | 6개 |
| `settings/slo.py` | 2개 |
| `settings/throttle.py` | 3개 |
| `settings/chaos.py` | 5개 |
| `settings/critical_worker.py` | 5개 |
| `settings/anti_flapping.py` | 3개 |

---

## 22. 추가 발견 항목 - 3차

### 22.1 audit/ring_buffer.py (2건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 67 | `RingBuffer.__init__()` | `capacity=10000` | 버퍼 용량 |
| 60 | `get_batch()` | `max_size=100` | 배치 최대 크기 |

### 22.2 audit/performance/sampling.py (4건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 20 | `SamplingConfig.sample_rate` | `0.1` | 샘플링 비율 (10%) |
| 21 | `SamplingConfig.min_samples` | `10` | 최소 샘플 수 |
| 22 | `SamplingConfig.max_samples` | `1000` | 최대 샘플 수 |
| 23 | `SamplingConfig.full_verify_on_failure` | `True` | 실패 시 전체 검증 |

### 22.3 services/retry_handler.py (4건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 76 | `RetryConfig.max_attempts` | `3` | 최대 재시도 횟수 |
| 77 | `RetryConfig.backoff_base` | `4` | 백오프 기본값 |
| 78 | `RetryConfig.backoff_max` | `180` | 최대 백오프 |
| 79 | `RetryConfig.jitter_percent` | `25` | 지터 비율 |

### 22.4 services/chaos/blast_radius.py (8건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 86 | `allowed_hours_start` | `2` | 허용 시작 시간 (UTC) |
| 90 | `allowed_hours_end` | `6` | 허용 종료 시간 (UTC) |
| 96 | `max_traffic_percent_instance` | `100.0` | Instance 최대 트래픽 % |
| 99 | `max_traffic_percent_service` | `50.0` | Service 최대 트래픽 % |
| 102 | `max_traffic_percent_region` | `10.0` | Region 최대 트래픽 % |
| 180 | `CHAOS_MAX_FAILURE_PERCENT` | `5.0` | Chaos 최대 실패율 % |
| 294 | `max_traffic_percent` | `100.0` | 기본 트래픽 최대값 |

### 22.5 services/chaos/base/models.py (4건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 35 | `injection_rate` | `0.001` | 기본 주입 비율 (0.1%) |
| 186 | `p50_latency_max_ms` | `100.0` | P50 지연 최대값 |
| 187 | `p99_latency_max_ms` | `500.0` | P99 지연 최대값 |
| 191 | `error_rate_max_percent` | `0.1` | 에러율 최대값 % |
| 194 | `throughput_min_rps` | `100.0` | 최소 처리량 RPS |

### 22.6 services/governance_checks.py (4건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 340 | `is_emergency_blocking()` | `min_level=2` | 최소 비상 레벨 |
| 403 | `check_all_governance()` | `emergency_min_level=2` | 최소 비상 레벨 |
| 535 | `require_not_emergency()` | `min_level=2` | 최소 비상 레벨 |
| 621 | `check_all_governance()` 내부 | `emergency_min_level=2` | 최소 비상 레벨 |

### 22.7 audit/cascade_load_shedding.py (1건)

| 라인 | 필드 | 하드코딩 | 설명 |
|------|------|----------|------|
| 135 | `_rate_window_seconds` | `1.0` | Rate 윈도우 초 |

### 22.8 audit/self_audit.py (1건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 256 | `is_healthy()` | `max_failure_rate=0.1` | 최대 실패율 |

### 22.9 tasks/traffic_aware_replay.py (1건)

| 라인 | 파라미터 | 하드코딩 | 설명 |
|------|----------|----------|------|
| 121 | `check_all_governance()` | `emergency_min_level=2` | 최소 비상 레벨 |

### 22.10 3차 추가 발견 Settings 필요 목록

| 파일명 | 항목 수 | 우선순위 |
|--------|---------|----------|
| `settings/ring_buffer.py` | 2개 | 낮음 |
| `settings/sampling.py` | 4개 | 낮음 |
| `settings/retry.py` 확장 | 4개 | 중간 |
| `settings/chaos_blast_radius.py` 확장 | 6개 | 낮음 |
| `settings/steady_state.py` | 5개 | 낮음 |
| `settings/governance.py` | 1개 | 중간 |
| `settings/self_audit.py` | 1개 | 낮음 |

---

## 23. 전체 구현 순서 (통합)

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1-30 | 1차 항목 (문서 100 - 섹션 2-17) | 약 8시간 |
| 31-48 | 2차 항목 (섹션 19) | 약 3시간 |
| 49-68 | 3차 항목 (섹션 22) | 약 3시간 |
| 69-70 | 테스트 및 검증 | 약 1시간 |
| **총계** | | **약 15시간** |

---

## 24. 검증 체크리스트 (추가)

- [ ] `audit/ring_buffer.py` 하드코딩 2건 제거됨
- [ ] `audit/performance/sampling.py` 하드코딩 4건 제거됨
- [ ] `services/retry_handler.py` 하드코딩 4건 제거됨
- [ ] `services/chaos/blast_radius.py` 하드코딩 8건 제거됨
- [ ] `services/chaos/base/models.py` 하드코딩 5건 제거됨
- [ ] `services/governance_checks.py` 하드코딩 4건 제거됨
- [ ] `audit/cascade_load_shedding.py` 하드코딩 1건 제거됨
- [ ] `audit/self_audit.py` 하드코딩 1건 제거됨
- [ ] `tasks/traffic_aware_replay.py` 하드코딩 1건 제거됨

---

## 25. 추가 발견 항목 - 4차

### 25.1 audit/graceful_degradation/enums.py (9건)

| 라인 | 클래스/필드 | 하드코딩 | 설명 |
|------|------------|----------|------|
| 45 | `FallbackConfig.redis_timeout_seconds` | `5.0` | Redis 타임아웃 |
| 46 | `FallbackConfig.replica_timeout_seconds` | `3.0` | Replica 타임아웃 |
| 48 | `FallbackConfig.memory_max_entries` | `10000` | 메모리 최대 엔트리 |
| 49 | `FallbackConfig.key_prefix` | `"selfhealing:"` | 키 접두사 |
| 55 | `CircuitBreakerConfig.failure_threshold` | `5` | 실패 임계값 |
| 56 | `CircuitBreakerConfig.recovery_timeout_seconds` | `30.0` | 복구 타임아웃 |
| 57 | `CircuitBreakerConfig.half_open_requests` | `3` | 반개방 요청 수 |
| 58 | `CircuitBreakerConfig.success_threshold` | `2` | 성공 임계값 |

### 25.2 tasks/cleanup_tasks.py (4건)

| 라인 | 함수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 28 | `archive_old_dlq_entries()` | `older_than_days=30` | DLQ 아카이브 기준일 |
| 60 | `cleanup_expired_config()` | `older_than_hours=24` | 만료 설정 정리 기준 |
| 91 | `expire_approval_requests()` | `older_than_hours=72` | 승인 요청 만료 기준 |
| 123 | `purge_archived_dlq_entries()` | `older_than_days=90` | 영구 삭제 기준일 |

### 25.3 services/coordination/recovery_tasks.py (2건)

| 라인 | 태스크/파라미터 | 하드코딩 | 설명 |
|------|---------------|----------|------|
| 523 | `check_stale_pending_recoveries()` | `stale_threshold_minutes=30` | 방치 임계 분 |
| 605 | `cleanup_old_recovery_sessions_task()` | `max_age_hours=168` | 보관 기간 (7일) |

### 25.4 4차 추가 발견 Settings 필요 목록

| 파일명 | 항목 수 | 우선순위 |
|--------|---------|----------|
| `settings/graceful_degradation.py` (신규) | 9개 | 낮음 |
| `settings/cleanup.py` 확장 | 4개 | 중간 |
| `settings/recovery_tasks.py` 확장 | 2개 | 낮음 |

---

## 26. 전체 통계 요약

| 발견 차수 | 파일 수 | 항목 수 |
|----------|---------|---------|
| 1차 (문서 95-99) | 43개 | ~77건 |
| 2차 (문서 100 - 1차) | 23개 | ~87건 |
| 3차 (문서 100 - 2차) | 7개 | ~28건 |
| 4차 (문서 100 - 3차) | 9개 | ~30건 |
| 5차 (문서 100 - 4차) | 3개 | ~15건 |
| **총계** | **85개 파일** | **약 237건** |

---

## 27. 검증 체크리스트 (4차)

- [ ] `audit/graceful_degradation/enums.py` 하드코딩 9건 제거됨
- [ ] `tasks/cleanup_tasks.py` 하드코딩 4건 제거됨
- [ ] `services/coordination/recovery_tasks.py` 하드코딩 2건 제거됨

---

## 28. 추가 발견 항목 - 5차 (timedelta 패턴)

### 28.1 services/canary/locking.py (1건)

| 라인 | 상수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 81 | `DEFAULT_LOCK_TIMEOUT` | `timedelta(minutes=30)` | 락 타임아웃 |

### 28.2 tasks/drift_detection.py (1건)

| 라인 | 변수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 118 | `analysis_window` | `timedelta(hours=24)` | 분석 윈도우 |

### 28.3 adapters/celery/tasks/monitoring.py (1건)

| 라인 | 변수 | 하드코딩 | 설명 |
|------|------|----------|------|
| 123 | `sla_threshold` | `timedelta(hours=4)` | SLA 임계값 |

### 28.4 interfaces/cache_provider.py 및 adapters/cache/*.py (6건)

| 파일 | 라인 | 파라미터 | 하드코딩 |
|------|------|----------|----------|
| `interfaces/cache_provider.py` | 358 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/redis_adapter.py` | 52 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/redis_adapter.py` | 418 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/memory_adapter.py` | 82 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/memory_adapter.py` | 421 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/memcached_adapter.py` | 39 | `timeout` | `timedelta(seconds=10)` |
| `adapters/cache/memcached_adapter.py` | 399 | `timeout` | `timedelta(seconds=10)` |

### 28.5 5차 추가 발견 Settings 필요 목록

| 파일명 | 항목 수 | 우선순위 |
|--------|---------|----------|
| `settings/canary_locking.py` (신규) | 1개 | 낮음 |
| `settings/drift_detection.py` (신규) | 1개 | 낮음 |
| `settings/cache_adapter.py` (신규) | 1개 (공통) | 낮음 |

---

## 29. 전체 최종 통계

| 발견 차수 | 파일 수 | 항목 수 |
|----------|---------|---------|
| 1차 (문서 95-99) | 43개 | ~77건 |
| 2차 (문서 100 - 1차) | 23개 | ~87건 |
| 3차 (문서 100 - 2차) | 7개 | ~28건 |
| 4차 (문서 100 - 3차) | 9개 | ~30건 |
| 5차 (문서 100 - 4차) | 3개 | ~15건 |
| 6차 (문서 100 - 5차) | 9개 | ~9건 |
| **총계** | **94개 파일** | **약 246건** |

---

## 30. 참고: 설정 수 분석

### 현재 시스템 구조
| 항목 | 수량 |
|------|------|
| 전체 Python 파일 | 660개 |
| Settings 파일 | 49개 |
| 이미 정의된 Settings Field | 475개 |
| `from_settings` 패턴 사용 | 42개 파일 |
| 추가 발견된 하드코딩 | ~246건 |

### 분석
- **기존 설정 (800+ 수정 완료)** + **새로 발견 (~246건)** = **약 1,050건 이상**
- 660개 파일 중 **49개 Settings 파일**이 이미 475개 필드 정의
- 이는 **복잡한 Self-Healing 시스템**으로서 자연스러운 수준
- 대부분은 **기본값이 적절**하며, 변경이 거의 필요 없음

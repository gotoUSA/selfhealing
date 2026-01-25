# 97. 태스크 설정 외부화

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94-96
- **대상 경로**: `tasks/`, `services/coordination/recovery_tasks.py`

---

## 1. 대상 파일 목록

| 파일 | 하드코딩 항목 수 | 기존 Settings 유무 | 우선순위 |
|------|----------------|-------------------|----------|
| `tasks/cleanup_tasks.py` | 8건 | △ (일부) | 높음 |
| `services/coordination/recovery_tasks.py` | 10건 | △ (일부) | 높음 |
| `tasks/intelligence_tasks.py` | 8건 | X | 중간 |
| `tasks/notification_policy.py` | 5건 | △ (일부) | 중간 |
| `tasks/config_apply.py` | 4건 | X | 중간 |
| `tasks/chaos_scheduler.py` | 4건 | O (chaos.py) | 낮음 |
| `tasks/canary_watchdog.py` | 3건 | X | 낮음 |
| `tasks/daily_report.py` | 2건 | X | 낮음 |
| `tasks/governance.py` | 2건 | O (governance.py) | 낮음 |
| `tasks/traffic_aware_replay.py` | 1건 | X | 낮음 |

---

## 2. cleanup_tasks.py 상세

### 2.1 발견된 하드코딩

| 라인 | 함수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 28 | `archive_old_dlq_entries` | `30` | older_than_days 기본값 |
| 60 | `cleanup_expired_config` | `24` | older_than_hours 기본값 |
| 91 | `expire_approval_requests` | `72` | older_than_hours 기본값 |
| 123 | `purge_old_audit_logs` | `90` | older_than_days 기본값 |
| 173-174 | 태스크 데코레이터 | `2`, `300` | max_retries, default_retry_delay |
| 183-184 | 태스크 데코레이터 | `2`, `300` | max_retries, default_retry_delay |
| 193-194 | 태스크 데코레이터 | `2`, `300` | max_retries, default_retry_delay |
| 203-204 | 태스크 데코레이터 | `1`, `600` | max_retries, default_retry_delay |

### 2.2 현황 분석
- `settings/celery_task.py`에 일반 Celery 설정 존재
- 클린업 전용 설정 없음
- 각 함수별 기본값이 개별 정의됨

### 2.3 구현 순서

1. **Step 1**: `settings/cleanup.py` 생성
   - 환경 변수 접두사: `SELFHEALING_CLEANUP_`
   - 각 클린업 유형별 기본값 정의

2. **Step 2**: `cleanup_tasks.py` 수정
   - Settings import 추가
   - 함수 파라미터 기본값을 Settings 참조로 변경
   - 태스크 데코레이터 값도 Settings에서 로드

### 2.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CLEANUP_DLQ_OLDER_THAN_DAYS` | `30` | DLQ 아카이브 기준 일수 |
| `SELFHEALING_CLEANUP_CONFIG_OLDER_THAN_HOURS` | `24` | 설정 만료 기준 시간 |
| `SELFHEALING_CLEANUP_APPROVAL_OLDER_THAN_HOURS` | `72` | 승인 요청 만료 기준 시간 |
| `SELFHEALING_CLEANUP_AUDIT_OLDER_THAN_DAYS` | `90` | 감사 로그 보존 일수 |
| `SELFHEALING_CLEANUP_TASK_MAX_RETRIES` | `2` | 클린업 태스크 최대 재시도 |
| `SELFHEALING_CLEANUP_TASK_RETRY_DELAY` | `300` | 클린업 태스크 재시도 지연(초) |

---

## 3. services/coordination/recovery_tasks.py 상세

### 3.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 82 | `DEFAULT_TRIGGER_CHECK_INTERVAL` | `60` | 트리거 체크 주기(초) |
| 83 | `DEFAULT_HEALTH_MONITOR_INTERVAL` | `30` | 헬스 모니터 주기(초) |
| 84 | `DEFAULT_STALE_CHECK_INTERVAL` | `10` | Stale 체크 주기(초) |
| 94-95 | 태스크 데코레이터 | `3`, `60` | max_retries, default_retry_delay |
| 257-258 | 태스크 데코레이터 | `3`, `30` | max_retries, default_retry_delay |
| 411-412 | 태스크 데코레이터 | `2`, `15` | max_retries, default_retry_delay |
| 419 | `total_requests` | `100` | 기본 총 요청 수 |
| 518 | 태스크 데코레이터 | `1` | max_retries |
| 523 | `stale_threshold_minutes` | `30` | Stale 임계치(분) |
| 605 | `max_age_hours` | `168` | 최대 보존 시간 (7일) |

### 3.2 현황 분석
- 모듈 상수로 주기 정의됨
- 태스크별 재시도 설정이 개별 정의됨
- 일부 값은 `settings/recovery_circuit_breaker.py`와 관련

### 3.3 구현 순서

1. **Step 1**: `settings/recovery_task.py` 생성 또는 기존 확장
2. **Step 2**: 모듈 상수를 Settings 참조로 변경
3. **Step 3**: 태스크 데코레이터에서 Settings 사용

### 3.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_RECOVERY_TASK_TRIGGER_CHECK_INTERVAL` | `60` | 트리거 체크 주기(초) |
| `SELFHEALING_RECOVERY_TASK_HEALTH_MONITOR_INTERVAL` | `30` | 헬스 모니터 주기(초) |
| `SELFHEALING_RECOVERY_TASK_STALE_CHECK_INTERVAL` | `10` | Stale 체크 주기(초) |
| `SELFHEALING_RECOVERY_TASK_STALE_THRESHOLD_MINUTES` | `30` | Stale 임계치(분) |
| `SELFHEALING_RECOVERY_TASK_MAX_AGE_HOURS` | `168` | 세션 최대 보존 시간 |

---

## 4. tasks/intelligence_tasks.py 상세

### 4.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 57 | `cooldown_seconds` | `3600` | 쿨다운 시간 (1시간) |
| 151 | `threshold` | `10` | 실행 임계치 |
| 154 | `cooldown_seconds` | `3600` | 쿨다운 시간 (1시간) |
| 157 | `threshold_minutes` | `60` | 분석 임계치(분) |
| 168 | `batch_size` | `100` | 배치 크기 |
| 258-260 | 조건문 | `50`, `10` | 심각도 판단 임계치 |
| 440 | `cooldown_seconds` | `120` | 쿨다운 시간 (2분) |
| 558 | `timedelta` | `30` (분) | 컷오프 시간 |

### 4.2 현황 분석
- 전용 Settings 없음
- 태스크 데코레이터와 로직 내부에 하드코딩 분산

### 4.3 구현 순서

1. **Step 1**: `settings/intelligence_task.py` 생성
2. **Step 2**: 모든 하드코딩을 Settings 참조로 변경

### 4.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_INTELLIGENCE_DEFAULT_COOLDOWN_SECONDS` | `3600` | 기본 쿨다운 |
| `SELFHEALING_INTELLIGENCE_EXECUTION_THRESHOLD` | `10` | 실행 임계치 |
| `SELFHEALING_INTELLIGENCE_ANALYSIS_THRESHOLD_MINUTES` | `60` | 분석 임계치(분) |
| `SELFHEALING_INTELLIGENCE_BATCH_SIZE` | `100` | 배치 크기 |
| `SELFHEALING_INTELLIGENCE_SEVERITY_HIGH_THRESHOLD` | `50` | 높은 심각도 임계치 |
| `SELFHEALING_INTELLIGENCE_SEVERITY_MEDIUM_THRESHOLD` | `10` | 중간 심각도 임계치 |

---

## 5. tasks/notification_policy.py 상세

### 5.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 23 | `threshold` | `10` | 알림 임계치 |
| 57 | `warning` | `20.0` | 경고 레벨 임계치 |
| 58 | `critical` | `50.0` | 위험 레벨 임계치 |
| 100 | `cooldown_seconds` | `300` | 쿨다운 시간 (5분) |

### 5.2 현황 분석
- `settings/notification_channel.py`에 알림 채널 설정 존재
- 정책 관련 설정은 별도로 정의 필요

### 5.3 구현 순서

1. **Step 1**: `settings/notification_channel.py` 확장 또는 별도 Settings 생성
2. **Step 2**: 정책 임계치를 Settings 참조로 변경

### 5.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_NOTIFICATION_POLICY_THRESHOLD` | `10` | 알림 트리거 임계치 |
| `SELFHEALING_NOTIFICATION_POLICY_WARNING_LEVEL` | `20.0` | 경고 레벨 |
| `SELFHEALING_NOTIFICATION_POLICY_CRITICAL_LEVEL` | `50.0` | 위험 레벨 |
| `SELFHEALING_NOTIFICATION_POLICY_COOLDOWN_SECONDS` | `300` | 정책 쿨다운 |

---

## 6. tasks/config_apply.py 상세

### 6.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 28 | `default_retry_delay` | `10` | 재시도 지연(초) |
| 99 | `max_retries` | `10` | 최대 재시도 횟수 |
| 102 | `max_wait_seconds` | `60` | 최대 대기 시간 |
| 130 | `countdown` | `30` | 재시도 대기(초) |
| 219 | `max_age_hours` | `24` | 만료 설정 최대 시간 |

### 6.2 구현 순서

1. **Step 1**: `settings/config_apply.py` 생성 또는 기존 확장
2. **Step 2**: 태스크 데코레이터와 함수 파라미터 변경

### 6.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CONFIG_APPLY_RETRY_DELAY` | `10` | 재시도 지연(초) |
| `SELFHEALING_CONFIG_APPLY_MAX_RETRIES` | `10` | 최대 재시도 |
| `SELFHEALING_CONFIG_APPLY_MAX_WAIT_SECONDS` | `60` | 최대 대기 시간 |
| `SELFHEALING_CONFIG_APPLY_COUNTDOWN` | `30` | 재시도 대기(초) |
| `SELFHEALING_CONFIG_APPLY_EXPIRE_MAX_AGE_HOURS` | `24` | 만료 기준 시간 |

---

## 7. tasks/chaos_scheduler.py 상세

### 7.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 233 | `soft_time_limit` | `300` | 소프트 타임 리밋 (5분) |
| 234 | `time_limit` | `360` | 하드 타임 리밋 (6분) |
| 244 | `default_retry_delay` | `300` | 재시도 지연 (5분) |
| 435 | `ttl_seconds` | `120` | 락 TTL (2분) |

### 7.2 현황 분석
- `settings/chaos.py`에 카오스 설정 존재
- 스케줄러 전용 설정 추가 필요

### 7.3 구현 순서

1. **Step 1**: `settings/chaos.py` 확장
2. **Step 2**: 스케줄러 관련 하드코딩 변경

---

## 8. tasks/canary_watchdog.py 상세

### 8.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 64 | `zombie_threshold_minutes` | `30` | 좀비 임계치(분) |
| 65 | `auto_rollback_after_minutes` | `60` | 자동 롤백 시간(분) |
| 66 | `max_stage_duration_minutes` | `15` | 최대 스테이지 지속 시간(분) |

### 8.2 구현 순서

1. **Step 1**: Canary 관련 Settings 확장
2. **Step 2**: Watchdog 설정 추가

---

## 9. 기타 태스크 파일

### 9.1 tasks/daily_report.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 59 | `max_retries` | `2` |
| 60 | `default_retry_delay` | `300` |

### 9.2 tasks/governance.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 144 | `default_retry_delay` | `60` |

### 9.3 tasks/traffic_aware_replay.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 186 | `cooldown_seconds` | `300` |

---

## 10. 전체 구현 순서 요약

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1 | `settings/cleanup.py` 생성 | 25분 |
| 2 | `cleanup_tasks.py` 리팩토링 | 35분 |
| 3 | `settings/recovery_task.py` 생성 | 25분 |
| 4 | `recovery_tasks.py` 리팩토링 | 40분 |
| 5 | `settings/intelligence_task.py` 생성 | 25분 |
| 6 | `intelligence_tasks.py` 리팩토링 | 35분 |
| 7 | Notification Settings 확장 | 15분 |
| 8 | `notification_policy.py` 리팩토링 | 20분 |
| 9 | `settings/config_apply.py` 생성 | 20분 |
| 10 | `config_apply.py` 리팩토링 | 25분 |
| 11 | Chaos Settings 확장 | 15분 |
| 12 | `chaos_scheduler.py` 리팩토링 | 20분 |
| 13 | Canary Settings 확장 | 15분 |
| 14 | `canary_watchdog.py` 리팩토링 | 15분 |
| 15 | 기타 태스크 리팩토링 | 25분 |
| 16 | 테스트 실행 및 검증 | 40분 |
| **총계** | | **약 6.5시간** |

---

## 11. 검증 체크리스트

- [ ] `settings/cleanup.py` 생성됨
- [ ] `cleanup_tasks.py` 하드코딩 8건 제거됨
- [ ] `settings/recovery_task.py` 생성됨
- [ ] `recovery_tasks.py` 하드코딩 10건 제거됨
- [ ] `settings/intelligence_task.py` 생성됨
- [ ] `intelligence_tasks.py` 하드코딩 8건 제거됨
- [ ] `notification_policy.py` 하드코딩 5건 제거됨
- [ ] `config_apply.py` 하드코딩 4건 제거됨
- [ ] `chaos_scheduler.py` 하드코딩 4건 제거됨
- [ ] `canary_watchdog.py` 하드코딩 3건 제거됨
- [ ] 기타 태스크 하드코딩 제거됨
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

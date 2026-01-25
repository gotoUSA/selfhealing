# 104. Coordination 서비스 하드코딩된 설정값 리팩토링 계획

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: Step 1 완료
- **관련 문서**: 102_HARDCODED_CONFIG_FINAL_AUDIT.md
- **대상 디렉토리**: `packages/selfhealing-python/src/selfhealing/services/coordination/`

---

## 1. 개요

Coordination 서비스에서 발견된 하드코딩된 설정값들을 Pydantic Settings 체계로 마이그레이션하는 상세 계획.

---

## 2. 대상 파일 및 설정값

### 2.1 recovery_tasks.py

**위치**: L82-95, L257-258, L411-412, L518  
**현재 구조**: 모듈 레벨 상수 및 Celery 데코레이터 파라미터

#### 모듈 레벨 상수
| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_TRIGGER_CHECK_INTERVAL` | 60 | 트리거 체크 주기 | `SELFHEALING_RECOVERY_TRIGGER_CHECK_INTERVAL` |
| `DEFAULT_HEALTH_MONITOR_INTERVAL` | 30 | 헬스 모니터 주기 | `SELFHEALING_RECOVERY_HEALTH_MONITOR_INTERVAL` |
| `DEFAULT_STALE_CHECK_INTERVAL` | 10 | 스테일 체크 주기 | `SELFHEALING_RECOVERY_STALE_CHECK_INTERVAL` |

#### Celery 태스크 데코레이터 (L94-95, L257-258, L411-412, L518)
| 태스크 | max_retries | default_retry_delay |
|-------|-------------|---------------------|
| check_recovery_triggers | 3 | 60 |
| monitor_active_recovery | 3 | 30 |
| cleanup_stale_sessions | 2 | 15 |
| run_health_checks | 1 | - |

**구현 방안**:
- 기존 `settings/recovery_circuit_breaker.py` 확장 또는 `settings/recovery_tasks.py` 신규 생성
- Celery 태스크 설정은 동적으로 적용하거나 기본값 유지

---

### 2.2 critical_worker.py

**위치**: L52-70 (Enum), L226-260 (Worker Pool 설정)  
**현재 구조**: Enum 값 및 딕셔너리 기반 Pool 설정

#### Worker Pool 설정 (L226-260)
| 환경 | worker_count | concurrency | prefetch_multiplier |
|-----|-------------|-------------|---------------------|
| MINIMAL | 2 | 2 | 1 |
| STANDARD | 4 | 4 | 2 |
| HIGH_AVAILABILITY | 4 | 4 | 2 |
| BURST | 2 | 4 | 4 |
| ENTERPRISE | 8 | 8 | 4 |

**환경변수 제안**:
```
SELFHEALING_CRITICAL_WORKER_{ENV}_WORKER_COUNT
SELFHEALING_CRITICAL_WORKER_{ENV}_CONCURRENCY
SELFHEALING_CRITICAL_WORKER_{ENV}_PREFETCH_MULTIPLIER
```

**구현 방안**:
- 기존 `settings/critical_worker.py` 확장
- 환경별 설정을 중첩 Pydantic 모델로 정의

---

### 2.3 regional_recovery_policy.py

**위치**: L195-230, L254, L366, L372  
**현재 구조**: 정적 정책 정의

#### 중요도별 정책 기본값
| 중요도 | stability_check_duration_minutes | error_rate_threshold | success_rate_threshold | approval_timeout_minutes | priority |
|-------|----------------------------------|---------------------|------------------------|-------------------------|----------|
| CRITICAL | 10 | 0.05 | 0.98 | 30 | 100 |
| HIGH | 7 | 0.10 | 0.95 | - | 50 |
| MEDIUM | 5 | 0.15 | 0.90 | - | 10 |
| LOW | 10 | 0.10 | 0.95 | - | 0 |

**환경변수 제안**:
```
SELFHEALING_REGIONAL_POLICY_{LEVEL}_STABILITY_DURATION_MIN
SELFHEALING_REGIONAL_POLICY_{LEVEL}_ERROR_RATE_THRESHOLD
SELFHEALING_REGIONAL_POLICY_{LEVEL}_SUCCESS_RATE_THRESHOLD
```

**구현 방안**:
- `settings/regional_recovery_policy.py` 확장
- 중첩 Pydantic 모델로 정책별 설정 그룹 정의

---

### 2.4 redis_key_guard.py

**위치**: L218-231, L347-348, L438  
**현재 구조**: 키 타입별 기본 TTL

| 키 타입 | default_ttl_seconds | 설명 |
|--------|---------------------|------|
| SHORT_LIVED | 3600 | 1시간 |
| NORMAL | 7200 | 2시간 |
| LONG_LIVED | 604800 | 7일 |

**환경변수 제안**:
```
SELFHEALING_REDIS_GUARD_TTL_SHORT_LIVED
SELFHEALING_REDIS_GUARD_TTL_NORMAL
SELFHEALING_REDIS_GUARD_TTL_LONG_LIVED
```

**구현 방안**:
- `settings/redis_key_guard.py` 신규 생성

---

### 2.5 recovery_shutdown.py

**위치**: L145  
**현재 구조**: 기본 파라미터

| 설정 | 현재 값 | 용도 |
|-----|--------|------|
| `drain_timeout` | 30.0 | 드레인 타임아웃 (초) |

**구현 방안**:
- `settings/recovery_shutdown.py` 신규 생성 또는 기존 설정에 통합

---

### 2.6 recovery_coordinator.py

**위치**: L103-164, L437, L696-697  
**현재 구조**: RecoveryStep 객체 및 안정성 검사 기본값

| 설정 그룹 | 설정 | 값 |
|----------|-----|---|
| RecoveryStep | wait_after_seconds | 0, 60, 300 등 |
| StabilityCheck | duration_minutes | 10 |
| StabilityCheck | error_rate_threshold | 0.1 |

**구현 방안**:
- `settings/recovery_coordinator.py` 신규 생성
- 복구 단계별 대기 시간을 환경변수로 설정 가능하게 함

---

## 3. Namespace Emergency 서브디렉토리

### 3.1 tracker.py

**위치**: L68, L71  
**현재 구조**: 모듈 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_EMERGENCY_EXPIRY_HOURS` | 8 | 긴급 상태 만료 | `SELFHEALING_NAMESPACE_EMERGENCY_EXPIRY_HOURS` |
| `CACHE_TTL_SECONDS` | 30.0 | 캐시 TTL | `SELFHEALING_NAMESPACE_CACHE_TTL` |

---

### 3.2 cascade_detector.py

**위치**: L44, L47  
**현재 구조**: 모듈 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_ESCALATION_THRESHOLD` | 2 | 에스컬레이션 임계값 | `SELFHEALING_NAMESPACE_ESCALATION_THRESHOLD` |
| `DEFAULT_CASCADE_WINDOW_MINUTES` | 30 | 캐스케이드 윈도우 | `SELFHEALING_NAMESPACE_CASCADE_WINDOW_MINUTES` |

---

## 4. 신규/확장 Settings 모듈 구조

```
settings/
├── recovery_tasks.py           # NEW
├── redis_key_guard.py          # NEW
├── recovery_shutdown.py        # NEW (또는 기존에 통합)
├── recovery_coordinator.py     # NEW
├── critical_worker.py          # EXTEND
├── regional_recovery_policy.py # EXTEND
├── namespace_emergency.py      # EXTEND
└── ... (기존 파일들)
```

---

## 5. 구현 순서

### Step 1: Settings 모듈 생성/확장 (1-2일)
1. `settings/recovery_tasks.py` 생성
2. `settings/redis_key_guard.py` 생성
3. `settings/recovery_coordinator.py` 생성
4. `settings/critical_worker.py` 확장 (Worker Pool 설정 추가)
5. `settings/regional_recovery_policy.py` 확장 (정책별 설정 추가)
6. `settings/namespace_emergency.py` 확장 (tracker, cascade_detector 설정 추가)

### Step 2: Coordination 서비스 리팩토링 (2-3일)
7. `services/coordination/recovery_tasks.py` - settings 연동
8. `services/coordination/critical_worker.py` - settings 연동
9. `services/coordination/regional_recovery_policy.py` - settings 연동
10. `services/coordination/redis_key_guard.py` - settings 연동
11. `services/coordination/recovery_shutdown.py` - settings 연동
12. `services/coordination/recovery_coordinator.py` - settings 연동

### Step 3: Namespace Emergency 리팩토링 (1일)
13. `services/namespace_emergency/tracker.py` - settings 연동
14. `services/namespace_emergency/cascade_detector.py` - settings 연동

### Step 4: 테스트 업데이트 (1일)
15. 단위 테스트 환경변수 모킹 추가
16. 통합 테스트 검증

### Step 5: 문서화 (0.5일)
17. 환경변수 문서 업데이트
18. 마이그레이션 가이드 작성

---

## 6. 예상 소요 시간

| 단계 | 예상 소요 |
|-----|----------|
| Settings 모듈 생성/확장 | 1-2일 |
| Coordination 서비스 리팩토링 | 2-3일 |
| Namespace Emergency 리팩토링 | 1일 |
| 테스트 업데이트 | 1일 |
| 문서화 | 0.5일 |
| **총계** | **5.5-7.5일** |

---

## 7. Celery 태스크 데코레이터 처리 전략

### 7.1 현재 상태
```python
@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def check_recovery_triggers(self):
    ...
```

### 7.2 권장 전략: 기본값 유지 + 런타임 오버라이드

Celery 데코레이터의 `max_retries`, `default_retry_delay`는 태스크 정의 시점에 결정되므로, 
동적 변경이 어려움. 다음 두 가지 전략 중 선택:

**전략 A: 기본값 유지**
- 데코레이터 파라미터는 현재 값 유지
- 운영 환경에서 Celery 설정으로 글로벌 오버라이드

**전략 B: 래퍼 함수 사용**
- settings에서 값을 읽어 `self.retry()` 호출 시 동적 적용
- 데코레이터에서는 기본값만 설정

**권장**: 전략 A (단순성 우선)

---

## 8. 위험 요소 및 완화 방안

| 위험 | 영향도 | 완화 방안 |
|-----|-------|----------|
| Celery 태스크 재등록 필요 | 높음 | 데코레이터 값은 유지, 런타임 오버라이드 |
| Worker Pool 설정 변경 시 재시작 필요 | 중간 | 변경 시 graceful restart 절차 마련 |
| 정책 설정 오류 시 복구 실패 | 높음 | 값 검증 로직 추가, fallback 기본값 적용 |

---

## 9. 검증 체크리스트

- [x] 모든 신규 settings 모듈이 Pydantic v1/v2 호환
- [x] 환경변수 없이 기본값으로 정상 동작
- [ ] Recovery 태스크 정상 실행
- [ ] Worker Pool 설정 적용 확인
- [ ] Regional Policy 정책별 동작 검증
- [ ] Namespace Emergency 설정 적용 확인
- [x] 기존 단위 테스트 100% 통과

---

## 10. Step 1 완료 내역 (2026-01-25)

### 10.1 생성된 Settings 모듈

| 파일 | 설명 | 환경변수 접두어 |
|-----|------|----------------|
| `settings/recovery_tasks.py` | Celery 복구 태스크별 재시도 설정 | `SELFHEALING_RECOVERY_TASKS_` |
| `settings/recovery_coordinator.py` | RecoveryStep 기본값 (LEVEL별) | `SELFHEALING_RECOVERY_COORD_` |

### 10.2 확장된 Settings 모듈

| 파일 | 추가 내용 |
|-----|---------|
| `settings/critical_worker.py` | DeploymentEnvironment Enum, 환경별 Worker Pool 설정 (MINIMAL/STANDARD/HIGH_AVAILABILITY/BURST/ENTERPRISE) |

### 10.3 테스트

- `tests/unit/settings/test_coordination_settings.py` (29 tests, 100% passed)
  - RecoveryTasksSettings: 기본값, 환경변수 오버라이드, 유효성 검증
  - RecoveryCoordinatorSettings: LEVEL별 기본값, 안정성 검사 설정
  - CriticalWorkerSettings Worker Pool: 환경별 설정, get_pool_config_for_env()
  - Settings Module Exports: 정상 export 확인

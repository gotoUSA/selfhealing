# 146. Postmortem 인시던트 생명주기 통합

**문서 버전:** 1.2
**작성일:** 2026-01-28
**수정일:** 2026-02-01
**선행 문서:** [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md), [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md)
**관련 코드:** `services/audit/base.py`, `adapters/cache/redis_adapter.py`, `settings/distributed_lock.py`, `services/postmortem_store.py`, `services/postmortem/incident_group.py`
**상태:** 구현 완료

---

## 1. 목적

현재 Postmortem 자동 생성은 CB CLOSED 이벤트에만 반응합니다. 그러나 시스템에는 두 가지 복구 경로가 존재하며, 둘 다 인시던트 완료 시점으로 Postmortem을 생성해야 합니다:

1. **CB 기반 복구:** 개별 서비스 장애 → CB CLOSED
2. **Emergency 기반 복구:** 리전/글로벌 장애 → RecoveryCoordinator COMPLETED

---

## 2. 현재 상태 분석

### 2.1 Postmortem 트리거 구조

| 트리거 | 파일 | 함수 | 구현 상태 |
|--------|------|------|----------|
| CB CLOSED | `event_bus.py` | `_on_circuit_breaker_closed_postmortem()` | ✅ 구현됨 |
| Recovery COMPLETED | - | - | ❌ 미구현 |

### 2.2 RecoveryCoordinator 완료 지점

`services/coordination/recovery_coordinator.py`의 `_complete_session()` 메서드:

| 단계 | 동작 |
|------|------|
| 1 | 세션 상태를 `COMPLETED`로 변경 |
| 2 | `_record_recovery_completed()` 호출 → 감사 기록 |
| 3 | 세션 저장 및 락 해제 |
| 4 | **Postmortem 생성 ❌ 없음** |

### 2.3 복구 완료 후 감사 기록

`_record_recovery_completed()` 메서드에서 `RecoveryAuditEventType.RECOVERY_COMPLETED` 이벤트 기록:

| 기록 내용 | 설명 |
|----------|------|
| `session_id` | 복구 세션 ID |
| `namespace` | 네임스페이스 |
| `trigger_level` | 트리거된 Emergency 레벨 |
| `steps_count` | 실행된 단계 수 |
| `duration_seconds` | 복구 소요 시간 |

---

## 3. EventType 확장

### 3.1 기존 EventType 분석

`services/event_bus.py`의 `EventType` Enum:

| 카테고리 | 이벤트 |
|---------|--------|
| Emergency | `EMERGENCY_RECOVERY_STARTED`, `EMERGENCY_RECOVERY_COMPLETED` |
| CB | `CIRCUIT_BREAKER_OPENED`, `CIRCUIT_BREAKER_CLOSED` |

**발견:** `EMERGENCY_RECOVERY_COMPLETED`가 이미 정의되어 있으나, 발행하는 코드가 없음

### 3.2 이벤트 발행 위치

| 위치 | 메서드 | 변경 필요 |
|------|--------|----------|
| `recovery_coordinator.py` | `_complete_session()` | 이벤트 발행 추가 |
| `recovery_coordinator.py` | `approve_recovery()` | 이벤트 발행 추가 |

---

## 4. 구현 설계

### 4.1 새 핸들러 추가

**파일:** `services/event_bus.py`

**함수명:** `_on_emergency_recovery_completed_postmortem()`

**동작:**
1. `EMERGENCY_RECOVERY_COMPLETED` 이벤트 수신
2. Settings에서 `auto_postmortem_enabled` 확인
3. 활성화 시 Emergency 레벨 Postmortem 생성
4. `add_healing_incident()` 호출

### 4.2 이벤트 발행 추가

**파일:** `services/coordination/recovery_coordinator.py`

**위치:** `_complete_session()` 메서드 끝

**발행 내용:**

| 필드 | 값 |
|------|-----|
| `event_type` | `EventType.EMERGENCY_RECOVERY_COMPLETED` |
| `namespace` | 세션 네임스페이스 |
| `trigger_level` | 트리거 레벨 |
| `session_id` | 세션 ID |
| `started_at` | 복구 시작 시각 |
| `completed_at` | 복구 완료 시각 |
| `duration_seconds` | 총 소요 시간 |
| `steps_executed` | 실행된 단계 수 |

### 4.3 핸들러 등록

**파일:** `services/event_bus.py` → `register_default_handlers()`

| 이벤트 | 핸들러 | 우선순위 |
|--------|--------|----------|
| `EMERGENCY_RECOVERY_COMPLETED` | `_on_emergency_recovery_completed_postmortem` | `LOW` |

---

## 5. Emergency Postmortem 데이터 구조

### 5.1 CB vs Emergency Postmortem 차이

| 항목 | CB Postmortem | Emergency Postmortem |
|------|---------------|---------------------|
| 범위 | 단일 서비스 | 리전/글로벌 |
| incident_id 접두사 | `AUTO-{service}` | `EMERGENCY-{namespace}` |
| 영향 서비스 | CB 상태에서 추출 | 복구 세션에서 추출 |
| 타임라인 | EventBus 히스토리 | 복구 단계 + EventBus |

### 5.2 Emergency Postmortem 전용 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `recovery_type` | `str` | `"emergency"` |
| `namespace` | `str` | 복구 네임스페이스 |
| `trigger_level` | `str` | 트리거 Emergency 레벨 |
| `recovery_session_id` | `str` | RecoverySession ID |
| `recovery_steps` | `list` | 실행된 복구 단계 목록 |
| `requires_approval` | `bool` | 수동 승인 필요 여부 |
| `approved_by` | `str` | 승인자 (해당 시) |

---

## 6. 타임라인 통합

### 6.1 Emergency 타임라인 구성

| 소스 | 내용 |
|------|------|
| RecoverySession.steps | 복구 단계별 시작/완료 시각 |
| EventBus 히스토리 | Emergency 관련 이벤트 |
| RecoveryAudit 로그 | 감사 기록 |

### 6.2 타임라인 병합 순서

```
1. EMERGENCY_ACTIVATED 이벤트
2. Recovery 단계별 이벤트 (BUDGET_RESET, HEALTH_CHECK 등)
3. CB 상태 변경 이벤트 (있는 경우)
4. EMERGENCY_RECOVERY_COMPLETED 이벤트
```

---

## 7. 헬퍼 함수 설계

### 7.1 새 함수

**파일:** `api/django/views/xtest/observability.py` (또는 분리 후 `services/postmortem_store.py`)

**함수명:** `_generate_emergency_postmortem_data()`

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `session` | `RecoverySession` | 완료된 복구 세션 |
| `event_bus_history` | `list` | EventBus 히스토리 |
| `snapshot` | `dict` | 시스템 스냅샷 |

**출력:** `dict` - Emergency Postmortem 데이터

### 7.2 Duration 계산

Emergency 복구의 경우:

| 시작 | 종료 | 계산 |
|------|------|------|
| `session.started_at` | `session.completed_at` | ISO 파싱 후 차이 계산 |

---

## 8. Settings 확장

### 8.1 기존 설정 재사용

| 설정 | 용도 |
|------|------|
| `xtest_auto_postmortem_enabled` | 자동 생성 활성화 (CB + Emergency 공용) |
| `xtest_auto_postmortem_min_duration` | 최소 duration (CB + Emergency 공용) |

### 8.2 새 설정 (선택적)

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `emergency_postmortem_enabled` | `bool` | `True` | Emergency 복구 시 Postmortem 생성 |

---

## 9. 구현 체크리스트

### 9.1 이벤트 발행

- [x] `recovery_coordinator.py` → `_complete_session()`에 이벤트 발행 추가
- [x] `recovery_coordinator.py` → `approve_recovery()`에 이벤트 발행 추가
- [x] EventBus import 추가

### 9.2 핸들러 구현

- [x] `_on_emergency_recovery_completed_postmortem()` 함수 생성
- [x] RecoverySession 데이터 수집
- [x] Emergency Postmortem 데이터 생성
- [x] `add_healing_incident()` 호출

### 9.3 핸들러 등록

- [x] `register_default_handlers()`에 새 핸들러 등록
- [x] Priority LOW로 설정

### 9.4 헬퍼 함수

- [x] `_generate_emergency_postmortem_data()` 구현
- [x] 타임라인 병합 로직 구현
- [x] 동적 Action Items 생성 (복구 단계 기반)

### 9.5 테스트

- [x] Recovery COMPLETED 시 Postmortem 생성 확인
- [x] 수동 승인 후 Postmortem 생성 확인
- [x] Emergency Postmortem 고유 필드 확인

---

## 10. 의존성 분석

### 10.1 필요 import

`event_bus.py`에서:

| 모듈 | 대상 |
|------|------|
| `recovery_coordinator` | `get_recovery_coordinator` |
| `recovery_state` | `RecoverySession` |

`recovery_coordinator.py`에서:

| 모듈 | 대상 |
|------|------|
| `event_bus` | `get_event_bus`, `EventType` |

### 10.2 순환 import 방지

이벤트 발행은 `_complete_session()` 내부에서 수행하므로 지연 import 사용:

```
# 메서드 내부에서
from selfhealing.services.event_bus import get_event_bus, EventType
```

---

## 11. 로그

### 11.1 예상 로그

| 상황 | 레벨 | 메시지 |
|------|------|--------|
| 이벤트 발행 | INFO | `"[Recovery] Published EMERGENCY_RECOVERY_COMPLETED: {session_id}"` |
| Postmortem 생성 | INFO | `"[EventHandler] Emergency postmortem generated: {incident_id}"` |
| 생성 스킵 | DEBUG | `"[EventHandler] Emergency postmortem disabled, skipping"` |

---

---

## 12. 데이터 안전성 강화

### 12.1 WAL Fallback 연동

**참조:** `services/audit/base.py` → `_write_to_wal()` 함수

| 항목 | 설명 |
|------|------|
| 용도 | Postmortem 생성 실패 시 WAL에 임시 저장 |
| 싱글톤 | `_get_wal()` 함수로 WriteAheadLog 인스턴스 획득 |
| 그룹 커밋 | `group_commit_enabled` 설정으로 I/O 최적화 |
| 버퍼 | `InMemoryAuditBuffer`로 이중 보호 |

**호출 시점:**

| 시점 | 동작 |
|------|------|
| Postmortem 생성 시도 | 먼저 WAL에 기록 |
| 중앙 저장소 저장 실패 | WAL에서 재시도 대기 |
| 저장 성공 | WAL 마킹 (synced=True) |

**환경 변수:**

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUDIT_WAL_DIR` | `/var/log/audit/wal` | WAL 디렉토리 |
| `AUDIT_WAL_MAX_FILE_SIZE_MB` | `100` | 최대 파일 크기 |
| `AUDIT_WAL_SYNC_ON_WRITE` | `true` | 쓰기 시 동기화 |

### 12.2 분산 락 연동

**참조:** `adapters/cache/redis_adapter.py` → `RedisDistributedLock` 클래스

| 항목 | 설명 |
|------|------|
| 용도 | Postmortem ID 생성 시 중복 방지 |
| 구현 | Redis SET NX PX 기반 원자적 락 |
| 소유권 | owner_id로 락 소유자 식별 |
| 해제 | Lua 스크립트로 원자적 체크 후 삭제 |

**락 키 패턴:**

| 키 | TTL | 용도 |
|----|-----|------|
| `lock:postmortem:generate:{incident_id}` | 30초 | 생성 중복 방지 |
| `lock:postmortem:group:{group_id}` | 60초 | 그룹 종료 중복 방지 |

**설정:** `settings/distributed_lock.py` → `DistributedLockSettings`

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `timeout_minutes` | 30 | 락 최대 유지 시간 |
| `retry_interval_seconds` | 0.1 | 재시도 간격 |
| `max_retry_attempts` | 100 | 최대 재시도 횟수 |

---

## 13. 구현 체크리스트 (추가)

### 13.1 WAL 연동

- [x] Postmortem 생성 전 WAL 기록 추가
- [x] 저장 실패 시 WAL fallback 로직
- [x] synced 플래그 업데이트

### 13.2 분산 락

- [x] `RedisDistributedLock` import
- [x] Postmortem 생성 시 락 획득/해제
- [x] 그룹 종료 시 락 획득/해제

---

## 14. 관련 문서

- [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) - RecoveryCoordinator 설계
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - CB Postmortem 자동 트리거
- [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md) - 인시던트 병합
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - WAL 기반 감사 설계

# 147. Postmortem 인시던트 병합 (IncidentGroup)

**문서 버전:** 1.1
**작성일:** 2026-01-28
**수정일:** 2026-01-28
**선행 문서:** [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md)
**관련 코드:** `audit/integrity/local_manager.py`, `idempotency_service.py`
**상태:** 설계 완료

---

## 1. 목적

짧은 시간 내 발생하는 연쇄 CB 이벤트를 하나의 IncidentGroup으로 병합하여 중복 Postmortem 생성을 방지하고, 연관 장애를 통합 분석할 수 있도록 함

---

## 2. 문제 정의

### 2.1 현재 동작

`_on_circuit_breaker_closed_postmortem()` 함수는 각 CB CLOSED 이벤트마다 독립적으로 호출됨:

| 시나리오 | CB CLOSED 이벤트 수 | 생성되는 Postmortem 수 |
|---------|-------------------|----------------------|
| 단일 서비스 장애 | 1 | 1 |
| 3개 서비스 동시 장애 | 3 | 3 |
| Cascading Failure (10개) | 10 | 10 |

### 2.2 문제점

| 문제 | 영향 |
|------|------|
| Postmortem 폭증 | 저장소 공간 낭비, 조회 복잡도 증가 |
| 연관성 파악 불가 | 공통 원인 분석 어려움 |
| 알림 폭주 | 알림 피로도 증가 |

---

## 3. 저장소 방식 비교

### 3.1 Redis ZSET 방식

**구조:** `ZSET` (Sorted Set) + 타임스탬프 score

| 장점 | 단점 |
|------|------|
| 다중 워커 환경에서 데이터 일관성 보장 | Redis 의존성 필수 |
| TTL 자동 만료 지원 | 네트워크 지연 발생 가능 |
| 원자적 연산 (ZADD, ZRANGEBYSCORE) | 추가 인프라 비용 |
| 수평 확장 용이 | Redis 장애 시 기능 불가 |

**참조 구현:** `idempotency_service.py` → `AntiFlappingWindow._check_and_record_redis()`

| 동작 | Redis 명령 |
|------|-----------|
| 오래된 엔트리 제거 | `ZREMRANGEBYSCORE` |
| 윈도우 내 엔트리 조회 | `ZRANGEBYSCORE` |
| 새 엔트리 추가 | `ZADD` |
| TTL 설정 | `EXPIRE` |

### 3.2 In-Memory 슬라이딩 윈도우 방식

**구조:** `dict[str, list[tuple[float, dict]]]` + Thread Lock

| 장점 | 단점 |
|------|------|
| 외부 의존성 없음 | 단일 프로세스 내에서만 유효 |
| 네트워크 지연 없음 (빠름) | 다중 워커 시 데이터 불일치 |
| 구현 단순 | 서버 재시작 시 데이터 손실 |
| Fallback으로 사용 가능 | 메모리 사용량 관리 필요 |

**참조 구현:** `idempotency_service.py` → `AntiFlappingWindow._check_and_record_memory()`

---

## 4. 방식 선택: Hybrid (Redis 우선 + In-Memory Fallback)

### 4.1 선택 이유

| 고려사항 | 결론 |
|---------|------|
| 다중 워커 환경 | Gunicorn 다중 워커 사용 → Redis 필수 |
| 장애 복원력 | Redis 장애 시에도 In-Memory로 동작 필요 |
| 기존 패턴 | AntiFlappingWindow가 동일 패턴 사용 중 → 검증됨 |
| 인프라 현황 | Redis StateBackend 이미 사용 중 → 추가 비용 없음 |

### 4.2 Hybrid 전략

| 상황 | 사용 저장소 |
|------|------------|
| Redis 정상 | Redis ZSET |
| Redis 연결 실패 | In-Memory Fallback |
| Redis 명령 실패 | In-Memory Fallback |

---

## 5. IncidentGroup 설계

### 5.1 개념

| 용어 | 정의 |
|------|------|
| `IncidentGroup` | 지정 시간 윈도우 내 발생한 연관 CB 이벤트 묶음 |
| `group_window_seconds` | 그룹핑 윈도우 크기 (기본 600초 = 10분) |
| `group_id` | 그룹 고유 ID (`INCGRP-{timestamp}`) |
| `primary_incident` | 그룹 내 첫 번째 (트리거) 인시던트 |

### 5.2 그룹핑 조건

| 조건 | 설명 |
|------|------|
| 시간 근접성 | 첫 번째 CB CLOSED 후 10분 내 발생 |
| 활성 그룹 존재 | 이미 열린 그룹이 있으면 합류 |

### 5.3 그룹 상태 전이

```
[OPEN] → (10분 경과 또는 모든 CB CLOSED) → [CLOSED] → Postmortem 생성
```

| 상태 | 설명 |
|------|------|
| `OPEN` | 이벤트 수집 중 |
| `CLOSED` | 수집 완료, Postmortem 생성 대기 |
| `COMPLETED` | Postmortem 생성됨 |

---

## 6. 구현 설계

### 6.1 새 모듈

**파일:** `services/postmortem/incident_group.py`

**클래스:** `IncidentGroupManager`

### 6.2 데이터 구조

#### IncidentGroupEntry

| 필드 | 타입 | 설명 |
|------|------|------|
| `service_name` | `str` | CB CLOSED된 서비스 |
| `closed_at` | `str` | CLOSED 시각 (ISO) |
| `opened_at` | `str` | OPEN 시각 (ISO) |
| `duration_seconds` | `float` | 개별 장애 지속 시간 |
| `event_data` | `dict` | 원본 이벤트 데이터 |

#### IncidentGroup

| 필드 | 타입 | 설명 |
|------|------|------|
| `group_id` | `str` | 그룹 ID |
| `status` | `str` | `OPEN`, `CLOSED`, `COMPLETED` |
| `created_at` | `str` | 그룹 생성 시각 |
| `closed_at` | `str` | 그룹 종료 시각 |
| `entries` | `list[IncidentGroupEntry]` | 포함된 인시던트 목록 |
| `primary_service` | `str` | 첫 번째 장애 서비스 |

### 6.3 Redis Key 설계

| 키 패턴 | 타입 | TTL | 용도 |
|--------|------|-----|------|
| `selfhealing:incgroup:active:{namespace}` | `STRING` | 15분 | 활성 그룹 ID |
| `selfhealing:incgroup:data:{group_id}` | `HASH` | 1시간 | 그룹 메타데이터 |
| `selfhealing:incgroup:entries:{group_id}` | `ZSET` | 1시간 | 그룹 내 이벤트 (score=timestamp) |

---

## 7. IncidentGroupManager API

### 7.1 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `add_incident()` | CB CLOSED 이벤트를 그룹에 추가 |
| `get_active_group()` | 현재 활성 그룹 조회 |
| `close_group()` | 그룹 종료 및 Postmortem 생성 트리거 |
| `should_close_group()` | 그룹 종료 조건 확인 |

### 7.2 add_incident 상세

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `service_name` | `str` | 서비스명 |
| `event` | `SelfHealingEvent` | CB CLOSED 이벤트 |
| `namespace` | `str` | 네임스페이스 (기본: `"default"`) |

**동작:**

| 단계 | 설명 |
|------|------|
| 1 | 활성 그룹 조회 |
| 2 | 그룹 없으면 새 그룹 생성 |
| 3 | 그룹에 인시던트 추가 |
| 4 | 그룹 종료 조건 확인 |
| 5 | 조건 충족 시 종료 스케줄링 |

**반환:** `tuple[str, bool]` - (group_id, is_new_group)

### 7.3 그룹 종료 조건

| 조건 | 설명 |
|------|------|
| 타임아웃 | 첫 이벤트 후 10분 경과 |
| 수동 종료 | API를 통한 강제 종료 |
| 비활성 | 마지막 이벤트 후 2분간 추가 이벤트 없음 |

---

## 8. 핸들러 수정

### 8.1 기존 핸들러 변경

**파일:** `services/event_bus.py`

**함수:** `_on_circuit_breaker_closed_postmortem()`

**변경 사항:**

| 현재 동작 | 변경 후 동작 |
|----------|-------------|
| 즉시 Postmortem 생성 | IncidentGroupManager에 위임 |

### 8.2 변경 로직

| 단계 | 설명 |
|------|------|
| 1 | `IncidentGroupManager.add_incident()` 호출 |
| 2 | 새 그룹 생성 시 타이머 시작 |
| 3 | 기존 그룹에 추가 시 로그만 기록 |
| 4 | 그룹 종료 시 통합 Postmortem 생성 |

---

## 9. 통합 Postmortem 생성

### 9.1 그룹 Postmortem 특성

| 항목 | 개별 Postmortem | 그룹 Postmortem |
|------|----------------|-----------------|
| incident_id | `AUTO-{service}` | `INCGRP-{group_id}` |
| affected_services | 1개 | N개 |
| 타임라인 | 단일 서비스 | 모든 서비스 통합 |
| 분석 | 개별 원인 | 공통 원인 추론 |

### 9.2 그룹 Postmortem 추가 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `is_group` | `bool` | `True` |
| `group_id` | `str` | 그룹 ID |
| `incident_count` | `int` | 포함된 인시던트 수 |
| `services_timeline` | `list` | 서비스별 OPEN/CLOSED 시각 |
| `cascading_pattern` | `str` | 감지된 연쇄 패턴 |

### 9.3 연쇄 패턴 분석

| 패턴 | 조건 | 설명 |
|------|------|------|
| `simultaneous` | 모든 OPEN이 30초 내 | 동시 다발 (공통 원인 가능) |
| `cascading` | 순차적 OPEN (간격 < 60초) | 연쇄 장애 |
| `independent` | OPEN 간격 > 60초 | 독립 장애 우연 중복 |

---

## 10. Settings

### 10.1 새 설정

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `incident_group_enabled` | `bool` | `True` | 그룹핑 활성화 |
| `incident_group_window_seconds` | `int` | `600` | 그룹핑 윈도우 (10분) |
| `incident_group_inactivity_seconds` | `int` | `120` | 비활성 종료 시간 (2분) |
| `incident_group_min_count` | `int` | `2` | 그룹화 최소 인시던트 수 |

### 10.2 환경 변수

```
SELFHEALING_POSTMORTEM_INCIDENT_GROUP_ENABLED=true
SELFHEALING_POSTMORTEM_INCIDENT_GROUP_WINDOW_SECONDS=600
SELFHEALING_POSTMORTEM_INCIDENT_GROUP_INACTIVITY_SECONDS=120
SELFHEALING_POSTMORTEM_INCIDENT_GROUP_MIN_COUNT=2
```

---

## 11. 그룹 종료 스케줄링

### 11.1 Celery Task

**파일:** `adapters/celery/tasks.py`

**Task:** `close_incident_group`

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `group_id` | `str` | 그룹 ID |
| `namespace` | `str` | 네임스페이스 |

**동작:**
1. 그룹 상태 확인
2. 아직 OPEN이면 종료 처리
3. 통합 Postmortem 생성
4. 그룹 상태를 COMPLETED로 변경

### 11.2 스케줄링 시점

| 시점 | 지연 |
|------|------|
| 그룹 생성 시 | `group_window_seconds` 후 |
| 마지막 이벤트 추가 시 | `inactivity_seconds` 후 (재스케줄) |

---

## 12. 구현 체크리스트

### 12.1 IncidentGroupManager

- [ ] `services/postmortem/incident_group.py` 생성
- [ ] `IncidentGroupManager` 클래스 구현
- [ ] Redis ZSET 기반 그룹 관리
- [ ] In-Memory Fallback 구현

### 12.2 핸들러 수정

- [ ] `_on_circuit_breaker_closed_postmortem()` 수정
- [ ] `IncidentGroupManager.add_incident()` 호출
- [ ] 그룹 종료 시 Postmortem 생성

### 12.3 Celery Task

- [ ] `close_incident_group` 태스크 구현
- [ ] 그룹 생성 시 태스크 스케줄링
- [ ] 비활성 감지 스케줄링

### 12.4 Settings

- [ ] 그룹핑 관련 설정 추가
- [ ] 환경 변수 매핑

### 12.5 테스트

- [ ] 단일 인시던트 → 개별 Postmortem 생성
- [ ] 다중 인시던트 → 그룹 Postmortem 생성
- [ ] 타임아웃 후 그룹 종료 확인
- [ ] 비활성 종료 확인
- [ ] Redis Fallback 테스트

---

## 13. 하위 호환성

### 13.1 그룹핑 비활성화 시

`incident_group_enabled=False`인 경우:
- 기존 동작 유지 (즉시 개별 Postmortem 생성)
- 마이그레이션 없이 롤백 가능

### 13.2 단일 인시던트 그룹

그룹 내 인시던트가 1개만 있는 경우:
- 그룹 Postmortem 대신 개별 Postmortem 생성
- `incident_group_min_count` 설정으로 제어

---

## 14. 데이터 무결성 보장

### 14.1 HashChain 연동

**참조:** `audit/integrity/local_manager.py` → `HashChainManager` 클래스

| 항목 | 설명 |
|------|------|
| 용도 | Postmortem 생성 후 불변성 봉인 |
| 메서드 | `add_integrity()` |
| 출력 | sequence, previous_hash, current_hash |
| 저장 | Postmortem 레코드에 함께 보관 |

**봉인 시점:**

| 시점 | 동작 |
|------|------|
| 그룹 Postmortem 생성 | `add_integrity()` 호출 |
| Emergency Postmortem 생성 | `add_integrity()` 호출 |
| 개별 Postmortem 생성 | `add_integrity()` 호출 |

**무결성 필드 추가:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `integrity_sequence` | `int` | 체인 시퀀스 번호 |
| `integrity_prev_hash` | `str` | 이전 레코드 해시 |
| `integrity_hash` | `str` | 현재 레코드 해시 |
| `sealed_at` | `str` | 봉인 시각 (ISO) |

### 14.2 봉인 프로세스

| 단계 | 동작 |
|------|------|
| 1 | Postmortem 데이터 생성 |
| 2 | JSON 직렬화 |
| 3 | `HashChainManager.add_integrity()` 호출 |
| 4 | 반환된 무결성 필드 추가 |
| 5 | 저장소에 저장 |

### 14.3 검증 용도

| 상황 | 검증 방법 |
|------|----------|
| 감사 시 | 연속된 Postmortem 체인 재계산 |
| 위변 감지 | previous_hash 일치 확인 |
| 법적 증명 | 봉인 시각 및 해시 체인 제시 |

---

## 15. 구현 체크리스트 (추가)

### 15.1 무결성 연동

- [ ] `HashChainManager` import
- [ ] Postmortem 생성 후 `add_integrity()` 호출
- [ ] 무결성 필드 Postmortem 스키마에 추가
- [ ] 검증 API 엔드포인트 구현

---

## 16. 알림 집계 (Notification Aggregation)

### 16.1 필요성

IncidentGroup은 Postmortem 병합은 해결하지만, 알림 폭주(Alert Storm)는 별도 처리 필요:

| 문제 | 영향 |
|------|------|
| CB CLOSED 알림 N건 | 알림 피로도 증가 |
| 동시 Postmortem 알림 | 동일 장애에 대해 중복 알림 |
| 운영자 혼란 | 개별 대응 vs 통합 대응 판단 어려움 |

### 16.2 기존 패턴 분석

**AntiFlappingWindow (idempotency_service.py):**

| 특성 | 설명 |
|------|------|
| 저장소 | Redis ZSET |
| 윈도우 | 슬라이딩 타임 윈도우 |
| 용도 | Emergency 레벨 전환 플래핑 방지 |

**record_transition() (anti_flapping.py):**

| 특성 | 설명 |
|------|------|
| 용도 | 상태 전환 히스토리 기록 |
| 패턴 | Postmortem 알림 집계에 재사용 가능 |

### 16.3 NotificationAggregator 설계

| 항목 | 값 |
|------|-----|
| 파일 | `services/postmortem/notification_aggregator.py` |
| 저장소 | Redis ZSET (AntiFlappingWindow 패턴) |
| Fallback | In-Memory (단일 프로세스) |

### 16.4 Redis Key 설계

| 키 패턴 | 타입 | TTL | 용도 |
|--------|------|-----|------|
| `selfhealing:notif_agg:pending:{namespace}` | `ZSET` | 15분 | 대기 중인 알림 |
| `selfhealing:notif_agg:sent:{hash}` | `STRING` | 1시간 | 발송 완료 중복 방지 |

### 16.5 집계 동작

| 단계 | 동작 |
|------|------|
| 1 | Postmortem 생성 시 알림 요청 등록 |
| 2 | 집계 윈도우 동안 대기 (기본 60초) |
| 3 | 윈도우 종료 시 단일 요약 알림 발송 |
| 4 | 개별 알림 대신 통합 알림 1건 |

### 16.6 IncidentSummaryNotification

| 필드 | 타입 | 설명 |
|------|------|------|
| `total_incidents` | `int` | 집계된 인시던트 수 |
| `affected_services` | `list[str]` | 영향받은 서비스 목록 |
| `total_downtime_seconds` | `float` | 총 다운타임 |
| `group_id` | `str` | IncidentGroup ID (있는 경우) |
| `postmortem_links` | `list[str]` | 개별 Postmortem 링크 |
| `primary_incident_id` | `str` | 대표 인시던트 ID |

### 16.7 Settings

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `notification_aggregation_enabled` | `bool` | `True` | 알림 집계 활성화 |
| `notification_aggregation_window_seconds` | `int` | `60` | 집계 윈도우 크기 |
| `notification_aggregation_max_wait_seconds` | `int` | `300` | 최대 대기 시간 |

### 16.8 구현 체크리스트 (추가)

- [ ] `NotificationAggregator` 클래스 구현
- [ ] Redis ZSET 기반 대기 큐
- [ ] 집계 윈도우 타이머 (Celery Task)
- [ ] `IncidentSummaryNotification` 데이터클래스
- [ ] 기존 `PostmortemNotifier`와 통합

---

## 17. 관련 문서

- [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md) - 생명주기 통합
- [148_POSTMORTEM_TIMELINE_SNAPSHOT.md](148_POSTMORTEM_TIMELINE_SNAPSHOT.md) - 타임라인 스냅샷
- [151_POSTMORTEM_DEEP_LINKS.md](151_POSTMORTEM_DEEP_LINKS.md) - 알림 연동
- [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md) - AntiFlappingGuard 참조
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - 무결성 체인 설계

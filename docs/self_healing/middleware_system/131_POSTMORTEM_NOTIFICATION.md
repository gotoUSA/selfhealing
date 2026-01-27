# 131. Postmortem 알림 연동

**문서 버전:** 1.0  
**작성일:** 2026-01-27  
**선행 문서:** [130_POSTMORTEM_AUDIT_INTEGRATION.md](130_POSTMORTEM_AUDIT_INTEGRATION.md)  
**상태:** 분석 완료

---

## 1. 현재 알림 시스템 분석

### 1.1 기존 CB 알림 구현

**파일:** `services/event_bus.py` → `_on_circuit_breaker_opened_notify()`

**동작:**
1. CB OPEN 이벤트 수신
2. `UnifiedNotificationManager` 호출
3. Slack/Email 등으로 알림 발송

**알림 내용:**
- 제목: `🔴 Circuit Breaker OPEN: {service_name}`
- 우선순위: `HIGH`
- 카테고리: `CIRCUIT_BREAKER`
- 메타데이터: service_name, trace_id, actionable URLs

### 1.2 Post-mortem 알림 현황

| 경로 | 알림 발송 |
|------|----------|
| 수동 API (`PostmortemGeneratorView`) | ❌ 없음 |
| 자동 트리거 (`_on_circuit_breaker_closed_postmortem`) | ❌ 없음 |

---

## 2. 알림 시스템 구조

### 2.1 Unified Notification Manager

**파일:** `services/unified_notification.py`

**지원 우선순위:**

| Priority | 설명 | 채널 |
|----------|------|------|
| `CRITICAL` | 즉시 전송 | 모든 채널 |
| `HIGH` | 긴급 | Slack + Email |
| `MEDIUM` | 일반 | Slack only |
| `LOW` | 배치 가능 | 설정에 따름 |
| `INFO` | 로그만 | 로그 |

**지원 카테고리:**

| Category | 용도 |
|----------|------|
| `SECURITY` | 보안 인시던트 |
| `OPERATIONS` | Self-healing 작업 |
| `SLA` | SLA 위반 |
| `CIRCUIT_BREAKER` | CB 상태 변경 |
| `GOVERNANCE` | 거버넌스 체크 |
| `REPORT` | 일일 리포트 |

### 2.2 NotificationPayload 구조

**필수 필드:**
- `title`: 알림 제목
- `message`: 알림 본문
- `priority`: 우선순위
- `category`: 카테고리
- `source`: 발송 출처

**선택 필드:**
- `metadata`: 추가 정보 (dict)
- `dedup_key`: 중복 제거 키
- `channels`: 명시적 채널 지정

---

## 3. Post-mortem 알림 설계

### 3.1 알림 시점

| 시점 | 알림 필요 여부 | 이유 |
|------|---------------|------|
| Post-mortem 생성 완료 | ✅ 필요 | 인시던트 분석 완료 알림 |
| 자동 트리거로 생성 | ✅ 필요 | 장애 복구 후 분석 완료 |
| 수동 API로 생성 | ⚪ 선택적 | 이미 테스트 중이므로 불필요할 수 있음 |

### 3.2 알림 우선순위 결정

**기준: 인시던트 심각도**

| 조건 | Priority |
|------|----------|
| `duration_seconds >= 300` (5분 이상) | `HIGH` |
| `affected_services >= 3` (3개 이상 영향) | `HIGH` |
| 그 외 | `MEDIUM` |

### 3.3 알림 내용

**제목:** `📋 Post-mortem 생성: {incident_id}`

**본문:**
- 인시던트 시작/종료 시각
- 지속 시간
- 영향받은 서비스 목록
- 권장 조치 사항 요약

**메타데이터:**
- `incident_id`
- `duration_seconds`
- `affected_services`
- `resolved_at`
- `postmortem_url` (조회 API URL)

### 3.4 카테고리 선택

| 선택지 | 장단점 |
|--------|--------|
| `OPERATIONS` | Self-healing 작업으로 분류 |
| `REPORT` | 리포트로 분류 |

**권장:** `OPERATIONS` - Post-mortem은 Self-healing 운영의 일부

---

## 4. 구현 위치

### 4.1 자동 트리거 알림

**파일:** `services/event_bus.py` → `_on_circuit_breaker_closed_postmortem()`

**추가 위치:** Post-mortem 저장 (`add_healing_incident()`) 직후

### 4.2 수동 API 알림 (선택적)

**파일:** `api/django/views/xtest/observability.py` → `PostmortemGeneratorView.post()`

**추가 위치:** Response 반환 직전

**고려사항:**
- 수동 API는 X-Test-Mode 전용
- 테스트 중 알림이 필요한지 검토 필요
- Settings로 제어 가능하게 구현 권장

---

## 5. 중복 알림 방지

### 5.1 Dedup Key 설계

**형식:** `postmortem:{incident_id}`

**효과:** 동일 인시던트에 대해 중복 알림 방지

### 5.2 쿨다운

UnifiedNotificationManager의 기존 쿨다운 메커니즘 활용

---

## 6. Settings 확장

### 6.1 새 설정 항목

| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `postmortem_notification_enabled` | `bool` | `True` | Post-mortem 알림 활성화 |
| `postmortem_notification_min_duration` | `int` | `60` | 알림 발송 최소 duration (초) |

### 6.2 환경 변수

```
SELFHEALING_API_VIEW_POSTMORTEM_NOTIFICATION_ENABLED=true
SELFHEALING_API_VIEW_POSTMORTEM_NOTIFICATION_MIN_DURATION=60
```

---

## 7. 구현 체크리스트

### 7.1 자동 트리거

- [ ] `_on_circuit_breaker_closed_postmortem()`에 알림 로직 추가
- [ ] `NotificationPayload` 구성
- [ ] `UnifiedNotificationManager.notify()` 호출
- [ ] Settings에서 활성화 여부 확인

### 7.2 Settings

- [ ] `postmortem_notification_enabled` 설정 추가
- [ ] `postmortem_notification_min_duration` 설정 추가

### 7.3 테스트

- [ ] 알림 비활성화 시 미발송 확인
- [ ] 알림 활성화 시 정상 발송 확인
- [ ] 중복 알림 방지 확인

---

## 8. 관련 문서

- [08_NOTIFICATION_ARCHITECTURE.md](08_NOTIFICATION_ARCHITECTURE.md) - 알림 아키텍처
- [23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md](23_CIRCUIT_BREAKER_NOTIFICATION_DESIGN.md) - CB 알림 설계
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - 자동 트리거 구현

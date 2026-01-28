# 129. Postmortem Root Cause 필드 추가 (선택적)

**문서 버전:** 1.0
**작성일:** 2026-01-27
**선행 문서:** [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md)
**상태:** ✅ 구현 완료 (2026-01-28)

---

## 1. 목적

Google SRE 표준에 맞춰 `root_cause` 필드를 Post-mortem에 추가하여 장애 원인 분석 지원

---

## 2. Google SRE 표준

### 2.1 Postmortem 필수 구성요소

**참조:** Google SRE Book - Example Postmortem

| 항목 | 설명 | 현재 구현 |
|------|------|----------|
| Summary | 장애 요약 | ✅ `summary` |
| Impact | 영향 범위 | ✅ `affected_services` |
| **Root Causes** | 근본 원인 | ❌ 미구현 |
| **Trigger** | 장애 트리거 | ❌ 미구현 |
| **Resolution** | 해결 방법 | ❌ 미구현 |
| **Detection** | 감지 방법 | ❌ 미구현 |
| Timeline | 타임라인 | ✅ `timeline` |
| Action Items | 조치 항목 | ✅ (127번 문서에서 개선) |

---

## 3. 구현 범위

### 3.1 자동 추론 가능한 필드

| 필드 | 데이터 소스 | 자동화 가능성 |
|------|------------|--------------|
| Trigger | 첫 번째 CB OPEN 이벤트 | ✅ 높음 |
| Detection | CB OPEN 이벤트 시각 | ✅ 높음 |
| Resolution | CB CLOSED 이벤트 | ✅ 높음 |
| Root Cause | 에러 컨텍스트 | ⚠️ 부분적 |

### 3.2 Root Cause 추론

**제한사항:** 완전한 근본 원인 분석은 불가능 (인간 판단 필요)

**가능한 정보:**
- CB OPEN 시 기록된 에러 컨텍스트
- 첫 번째 실패 이벤트의 에러 메시지
- 연속 실패 패턴

---

## 4. 데이터 소스

### 4.1 CB 상태 저장소

**참조:** `services/circuit_breaker_service.py`

CB 상태에 포함된 정보:
- `service_name`: 서비스명
- `state`: 현재 상태
- `failure_count`: 실패 횟수
- `opened_at`: OPEN 시각

### 4.2 EventBus 히스토리

**참조:** `services/event_bus.py` → `bus.get_history()`

이벤트에 포함된 정보:
- `event_type`: 이벤트 타입
- `data`: 이벤트 데이터 (에러 컨텍스트 포함 가능)
- `timestamp`: 발생 시각

### 4.3 DLQ 엔트리

**참조:** `services/dlq_service.py`

DLQ 엔트리에 포함된 정보:
- `original_error`: 원본 에러 메시지
- `error_type`: 에러 타입
- `domain`: 도메인

---

## 5. 새 필드 설계

### 5.1 추가 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `trigger` | `dict` | 장애 트리거 정보 |
| `detection` | `dict` | 감지 정보 |
| `resolution` | `dict` | 해결 정보 |
| `root_cause_hypothesis` | `str` | 근본 원인 가설 (자동 추론) |

### 5.2 trigger 구조

```json
{
  "event_type": "circuit_breaker_opened",
  "service": "database",
  "timestamp": "2026-01-27T14:01:23+09:00",
  "error_context": {
    "error_type": "ConnectionError",
    "message": "Database connection timeout"
  }
}
```

### 5.3 detection 구조

```json
{
  "method": "circuit_breaker_threshold",
  "detected_at": "2026-01-27T14:01:24+09:00",
  "detector": "CircuitBreakerService",
  "threshold_exceeded": {
    "failure_count": 5,
    "threshold": 5
  }
}
```

### 5.4 resolution 구조

```json
{
  "method": "automatic_recovery",
  "resolved_at": "2026-01-27T14:02:25+09:00",
  "recovery_path": "OPEN → HALF_OPEN → CLOSED",
  "manual_intervention": false
}
```

---

## 6. 구현 위치

### 6.1 헬퍼 함수

**파일:** `observability.py`

새 헬퍼 함수들:

| 함수명 | 용도 |
|--------|------|
| `_extract_trigger_info()` | 트리거 정보 추출 |
| `_extract_detection_info()` | 감지 정보 추출 |
| `_extract_resolution_info()` | 해결 정보 추출 |
| `_generate_root_cause_hypothesis()` | 근본 원인 가설 생성 |

### 6.2 _generate_postmortem_data 확장

반환 딕셔너리에 새 필드 추가

---

## 7. 구현 체크리스트

### 7.1 헬퍼 함수

- [x] `_extract_trigger_info()` 구현
- [x] `_extract_detection_info()` 구현
- [x] `_extract_resolution_info()` 구현
- [x] `_generate_root_cause_hypothesis()` 구현

### 7.2 데이터 구조

- [x] `trigger` 필드 추가
- [x] `detection` 필드 추가
- [x] `resolution` 필드 추가
- [x] `root_cause_hypothesis` 필드 추가

### 7.3 테스트

- [x] 각 필드 데이터 추출 확인
- [x] 데이터 없을 때 기본값 처리
- [x] 기존 테스트 호환성 확인

---

## 8. Root Cause 가설 생성 로직

### 8.1 패턴 기반 분류

| 패턴 | 가설 |
|------|------|
| 단일 서비스 OPEN | "단일 서비스 장애: {service}" |
| 다중 서비스 OPEN | "인프라 전체 장애 가능성 - 공통 원인 분석 필요" |
| 연속 빠른 실패 | "급격한 부하 증가 또는 외부 의존성 장애" |
| DB 관련 에러 | "데이터베이스 연결 문제" |
| Timeout 에러 | "네트워크 지연 또는 서비스 과부하" |

### 8.2 참조 패턴

**파일:** `services/audit/chaos_audit.py` (L107)

```
"root_cause_hypothesis": "인프라 전체 붕괴 감지 - 개별 서비스 장애 아님"
```

---

## 9. 예상 결과

### 9.1 확장된 Post-mortem 구조

```json
{
  "incident_id": "HEAL-2026-0127-001",
  "summary": {...},
  "trigger": {
    "event_type": "circuit_breaker_opened",
    "service": "database",
    "timestamp": "2026-01-27T14:01:23+09:00"
  },
  "detection": {
    "method": "circuit_breaker_threshold",
    "detected_at": "2026-01-27T14:01:24+09:00"
  },
  "resolution": {
    "method": "automatic_recovery",
    "resolved_at": "2026-01-27T14:02:25+09:00"
  },
  "root_cause_hypothesis": "단일 서비스 장애: database - ConnectionError 감지",
  "timeline": [...],
  "auto_actions": [...],
  "recommendations": [...]
}
```

---

## 10. 주의사항

### 10.1 가설의 한계

- 자동 생성된 `root_cause_hypothesis`는 **가설**일 뿐임
- 실제 근본 원인 분석은 운영자가 수행해야 함
- 필드명에 `_hypothesis` 접미사로 명확히 표시

### 10.2 데이터 없음 처리

모든 새 필드는 데이터 없을 때 `null` 또는 빈 객체 반환

---

## 11. 우선순위

이 문서의 기능은 **선택적**입니다.

| 필드 | 우선순위 | 이유 |
|------|----------|------|
| `trigger` | 높음 | 자동 추출 가능 |
| `detection` | 높음 | 자동 추출 가능 |
| `resolution` | 높음 | 자동 추출 가능 |
| `root_cause_hypothesis` | 중간 | 가설만 가능 |

---

## 12. CB OPEN 스냅샷 저장 권장사항

### 12.1 업계 표준 분석

CB 상태 변경 시 스냅샷 저장에 대한 업계 관행을 조사한 결과입니다.

#### 참조 문서

| 출처 | 문서 |
|------|------|
| Microsoft Azure | [Circuit Breaker pattern](https://docs.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker) |
| Resilience4j | [CircuitBreaker Documentation](https://resilience4j.readme.io/docs/circuitbreaker) |
| Google SRE | [Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/) |
| Google SRE | [Tracking Outages (Outalator)](https://sre.google/sre-book/tracking-outages/) |

### 12.2 데이터 저장 방식 비교

| 출처 | 저장 방식 | 상세 내용 |
|------|----------|-----------|
| **Microsoft Azure** | 이벤트 기반 알림 | "If the circuit breaker raises an event each time it changes state, this information can help **monitor the health** of the protected system" |
| **Resilience4j** | 메트릭 + 이벤트 소비자 | `onStateTransition(event -> logger.info(...))` - 이벤트 발행 지원, **영구 저장은 사용자 책임** |
| **Google SRE** | Outalator (알림 저장) | "Outalator **stores a copy of the original notification** and allows annotating incidents" |

### 12.3 핵심 인사이트

#### Resilience4j
- CB 상태 변경을 **이벤트로 발행**하지만, 영구 저장은 하지 않음
- `CircularEventConsumer`로 **메모리 버퍼에 저장** 가능 (고정 용량)
- 영구 저장은 사용자가 **외부 시스템 연동**해야 함

#### Google SRE Outalator
- 알림(notification) 원본을 **저장**함
- 시스템 스냅샷 (CPU, memory 등)은 **별도 메트릭 시스템** 영역

#### Google SRE Monitoring 권고
> "In Google's experience, basic **collection and aggregation of metrics**, paired with alerting and dashboards, has worked well as a **relatively standalone system**."

### 12.4 데이터 유형별 저장 필요성

| 데이터 유형 | 저장 필요성 | 근거 |
|------------|------------|------|
| **CB 상태 변경** (CLOSED→OPEN) | ✅ **필수** | Google Outalator: "stores a copy of the original notification" |
| **실패 횟수/임계값** | ✅ **권장** | Azure: "track the number of recent failures" |
| **시스템 메트릭** (CPU, memory) | ⚠️ **선택** | 별도 메트릭 시스템에서 관리 (Prometheus 등) |
| **상세 에러 컨텍스트** | ⚠️ **선택** | 포스트모템 목적에만 필요 |

### 12.5 현재 코드 분석

#### 스냅샷 캡처 지점

| 위치 | 함수 | 수집 데이터 | 저장 방식 |
|------|------|------------|----------|
| `services/circuit_breaker/service.py` | `_collect_failure_snapshot()` | failure_count, error_context, latency, CPU, memory | `logger.info()` (휘발) |
| `services/circuit_breaker/service.py` | `_log_circuit_open_audit()` | reason 문자열만 | WAL + Audit 버퍼 |
| `views/xtest/base.py` | `collect_system_snapshot()` | CPU, memory, DB connections | 없음 (직접 반환) |

#### 현재 문제점
- `_collect_failure_snapshot()`의 상세 정보가 `logger.info()`로만 출력되어 **휘발**됨
- Audit 로그에는 reason 문자열만 저장 (상세 snapshot 누락)

### 12.6 권장사항

#### 12.6.1 추가 저장 **불필요** 항목

| 항목 | 이유 |
|------|------|
| **시스템 메트릭 스냅샷** | 기존 Prometheus/Grafana에서 이미 수집 중 |
| **CB 상태 상세 저장** | 메트릭 시스템에서 조회 가능 |

#### 12.6.2 현재 구현 유지 권장

| 현재 코드 | 권장 조치 | 이유 |
|----------|----------|------|
| `_collect_failure_snapshot()` | ✅ 유지 | 로깅 목적으로 충분 |
| `log_cb_state_change_audit()` | ✅ 유지 | 상태 변경 이벤트 저장됨 |
| `logger.info(snapshot)` | ✅ 유지 | 디버깅 목적 |

#### 12.6.3 개선 가능 항목 (선택적)

현재 구현으로 충분하지만, 필요시 다음 개선 가능:

| 개선 항목 | 방법 | 우선순위 |
|----------|------|----------|
| Audit reason 확장 | `reason`에 failure_count, threshold 포함 | 낮음 |
| 메트릭 시스템 연동 | Post-mortem 생성 시 Prometheus 쿼리 | 낮음 |

### 12.7 업계 표준 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    업계 표준 분리 모델                        │
├─────────────────────────────────────────────────────────────┤
│  Circuit Breaker        │  Metrics System   │  Outalator    │
│  ─────────────────      │  ─────────────    │  ───────────  │
│  • 상태 변경 이벤트 발행  │  • CPU/Memory 저장│  • 알림 저장  │
│  • 실패 카운트           │  • Latency 저장  │  • 태깅      │
│  • 이벤트 콜백 제공       │  • 대시보드      │  • 주석 추가  │
│  (영구 저장 X)          │  (영구 저장 O)   │  (영구 저장 O)│
└─────────────────────────────────────────────────────────────┘
```

### 12.8 결론

| 질문 | 답변 | 이유 |
|------|------|------|
| CB OPEN 스냅샷 저장? | **부분적 O** | 상태 변경 + 핵심 수치만 저장, 상세 메트릭은 별도 시스템 |
| 추가 영구 저장 필요? | **불필요** | 기존 Prometheus + Audit 조합으로 충분 |
| 코드 수정 필요? | **불필요** | 현재 구현이 업계 표준과 부합 |

**업계에서는 CB와 메트릭 시스템을 분리**하고, CB는 **이벤트 발행**에 집중합니다.
상세 스냅샷 별도 저장은 **오버엔지니어링**으로 판단됩니다.

---

## 13. 관련 문서

- [124_POSTMORTEM_ENHANCEMENT_OVERVIEW.md](124_POSTMORTEM_ENHANCEMENT_OVERVIEW.md) - 전체 개요
- [127_POSTMORTEM_ACTION_ITEMS.md](127_POSTMORTEM_ACTION_ITEMS.md) - Action Items 구현
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - 자동 트리거

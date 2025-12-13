# RESPONSIBILITY_AND_DECISION_BOUNDARY.md

> **문서 목적**: 법적·운영 책임 경계 명시 (분쟁 시 참조용)
> **대상 독자**: 법무, 보안, 감사 담당자
> **최종 수정일**: 2025-12-14
> **버전**: 1.3
> **변경 사항**: 법적 모호성 최종 제거, 표현 통일
> ※ 본 문서는 내부 설계 및 책임 경계 정의를 위한 문서이며,
법적 계약이나 약관을 대체하지 않습니다.

---

## 1️⃣ Design Principle (설계 원칙)

> **핵심 선언**: 본 시스템은 판단(judgment), 선택(selection), 추론(inference) 기능을 **포함하지 않으며 기술적으로 수행할 수 없습니다**. 오직 정책 작성자가 사전 정의한 수치 조건의 충족 여부를 비교(comparison)하고, 충족 시 미리 정의된 **유일한** 동작을 집행(execution)합니다. 복수의 대안 중 선택하는 기능은 기술적으로 불가능합니다.

이 시스템은 다음을 **기술적으로 수행할 수 없습니다** (해당 로직 부재):

1. **비즈니스 규칙 해석 또는 생성** — 해당 로직 부재. 모든 임계값, 정책, 조건은 정책 작성자가 사전 정의.
2. **거래 승인/거절 판단** — 해당 로직 부재. 외부 PG사 또는 운영자의 지시만 집행.
3. **보안 위반 복구** — 해당 로직 부재. 차단 후 사람에게 에스컬레이션만 수행.
4. **사용자 데이터 분석을 통한 의사결정** — 해당 로직 부재. 조건 비교 후 집행만 수행.
5. **상태 전이 조건 생성** — 해당 로직 부재. 정책 작성자가 사전 정의한 수치 조건 또는 운영자 명령에 의해서만 상태 전이 발생.

---

## 2️⃣ Decision Ownership Table (결정 주체 테이블)

| Action (행동) | 결정권자 | 시스템의 역할 | 코드 위치 |
|---|---|---|---|
| **재시도 최대 횟수** | 정책 작성자 | 설정값 적용 | [config.py#L227-L253](shopping/services/self_healing/config.py#L227-L253) `RetrySettings.max_attempts` |
| **SLA 임계시간 (도메인별)** | 정책 작성자 | 설정값 적용 | [config.py#L30-L83](shopping/services/self_healing/config.py#L30-L83) `SLAThresholds` |
| **Circuit Breaker OPEN 전환** | 정책 작성자 (임계값 사전 정의) 또는 운영자 (force_open 명령) | `failure_count >= failure_threshold` 수치 비교만 수행, 조건 충족 시 정책에 정의된 상태값으로 기록 갱신 (판단 없음) | [service.py#L198-L238](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py#L198-L238) `record_failure()` |
| **Circuit Breaker CLOSE 전환** | 운영자 (force_close) | 운영자 명령 집행 | [manual_control.py#L41-L95](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py#L41-L95) `force_open()`, `force_close()` |
| **DLQ 항목 Replay 실행** | 운영자 (항목 선택 및 명령) | 운영자가 선택한 항목에 대해 Replay 요청 전달만 수행 | [replay_service.py#L77-L100](shopping/services/self_healing/replay_service.py#L77-L100) `ReplayHandler.replay()` |
| **DLQ 항목 Resolved/Rejected 처리** | 운영자 | 운영자 지시 기록 | [dlq_admin.py#L78-L83](shopping/admin/dlq_admin.py#L78-L83) Admin actions |
| **보안 위반 유형 매칭 시 차단** | 정책 작성자 (ViolationType enum 정의) | enum에 정의된 값과 일치 여부만 확인, 일치 시 차단 집행 | [security_violation_service.py#L108-L148](shopping/services/self_healing/security_violation_service.py#L108-L148) `SecurityConfig` |
| **보안 사건 조사/해결** | 보안팀 (사람) | 사건 기록 및 에스컬레이션만 수행 | [security_incident.py#L48-L54](shopping/models/security_incident.py#L48-L54) `Status.INVESTIGATING` |
| **Rate Limit 수치 비교** | 정책 작성자 (임계값 정의) | request_count >= rate_limit_max 수치 비교만 수행 | [config.py#L144-L150](shopping/services/self_healing/config.py#L144-L150) `SecurityThresholds.rate_limit_max_requests` |
| **DLQ 보관 기간** | 정책 작성자 | 설정값 적용 | [config.py#L319-L341](shopping/services/self_healing/config.py#L319-L341) `DLQSettings.retention_days` |
| **Backoff 지연 값 산출** | 정책 작성자 (공식 정의: 4^n + jitter) | 정의된 공식에 수치 대입 후 결과값 반환만 수행 (판단 없음) | [backoff_calculator.py](shopping/services/self_healing/backoff_calculator.py) `BackoffCalculator` |
| **알림 채널 라우팅** | 정책 작성자 | 설정된 채널로 메시지 전송 | [config.py#L379-L401](shopping/services/self_healing/config.py#L379-L401) `SlackChannels` |
| **Control API 명령 실행** | 운영자 (API 호출자) | 운영자 명령 집행 및 감사 로그 기록 | [control_api_service.py#L135-L148](shopping/services/self_healing/control_api_service.py#L135-L148) `ControlRequest` |
| **Risk Level 조회** | 정책 작성자 (매트릭스 사전 정의) | (action, environment) 키로 매트릭스에서 값 조회만 수행 (판단 없음) | [control_api_service.py#L93-L125](shopping/services/self_healing/control_api_service.py#L93-L125) `get_risk_level()` |

---

## 3️⃣ Explicit Non-Responsibilities (명시적 비책임 영역)

이 시스템의 범위는 다음을 **포함하지 않으며, 기술적으로 수행 불가능합니다**:

### 3.1 보안 위반

| 항목 | 설명 | 코드 증거 |
|---|---|---|
| Webhook 서명 위조 | 차단만 수행, 복구 로직 부재 | [security_violation_service.py#L51](shopping/services/self_healing/security_violation_service.py#L51) `WEBHOOK_SIGNATURE_INVALID` |
| 결제 금액 변조 | 차단만 수행, 복구 로직 부재 | [security_violation_service.py#L52](shopping/services/self_healing/security_violation_service.py#L52) `PAYMENT_AMOUNT_TAMPERED` |
| 토큰 위조 | 차단만 수행, 복구 로직 부재 | [security_violation_service.py#L53](shopping/services/self_healing/security_violation_service.py#L53) `TOKEN_FORGED` |
| Replay 공격 | 차단만 수행, 복구 로직 부재 | [security_violation_service.py#L57](shopping/services/self_healing/security_violation_service.py#L57) `REPLAY_ATTACK` |

**코드 검증**: 보안 사건은 `SecurityIncident` 모델에 별도 저장되며, `FailedOperation` (DLQ)과 분리되어 Replay 기능에서 접근 불가능함
→ [security_incident.py#L20-L25](shopping/models/security_incident.py#L20-L25)

### 3.2 비즈니스 로직

| 항목 | 설명 |
|---|---|
| 주문 유효성 | 해당 로직 부재 — 상위 서비스 책임 |
| 재고 가용성 | 해당 로직 부재 — 상위 서비스 책임 |
| 가격 정책 | 해당 로직 부재 — 상위 서비스 책임 |
| 사용자 자격 | 해당 로직 부재 — 상위 서비스 책임 |

### 3.3 외부 시스템 상태

| 항목 | 설명 |
|---|---|
| PG사 복구 시점 추정 | 해당 로직 부재 — 운영자가 확인 후 force_close 명령 필요 |
| 외부 API 장애 원인 | 해당 로직 부재 — 오류 코드 기록만 수행 |
| 네트워크 상태 | 해당 로직 부재 — 타임아웃 발생 사실만 기록 |

---

## 4️⃣ Evidence & Audit Boundary (증거 및 감사 경계)

### 4.1 저장되는 데이터 (RECORDED DATA)

| 데이터 유형 | 저장 위치 | 보관 기간 | 코드 위치 |
|---|---|---|---|
| 실패 시점 타임스탬프 | `FailedOperation.created_at` | 정책 정의 (기본 30일) | [failed_operation.py](shopping/models/failed_operation.py) |
| 에러 코드/메시지 | `FailedOperation.error_code`, `error_message` | 정책 정의 | [failed_operation.py#L138-L147](shopping/models/failed_operation.py#L138-L147) |
| 요청/응답 데이터 스냅샷 | `FailedOperation.request_data`, `response_data` | 정책 정의 | [failed_operation.py](shopping/models/failed_operation.py) |
| 재시도 이력 | `ForensicContext.retry_history` | 정책 정의 | [forensic_context.py#L37-L44](shopping/services/self_healing/forensic_context.py#L37-L44) `RetryAttempt` |
| 운영자 행동 | `resolved_by`, `resolution_note`, `resolution_type` | 정책 정의 | [failed_operation.py](shopping/models/failed_operation.py) |
| Circuit Breaker 상태값 갱신 | `CircuitBreakerState` + 로그 | 영구 | [failed_payment.py](shopping/models/failed_payment.py) |
| Control API 요청 | `ControlRequest` + correlation_id | 로그 레벨에 따라 | [control_api_service.py#L135-L165](shopping/services/self_healing/control_api_service.py#L135-L165) |
| 보안 사건 | `SecurityIncident` | 별도 정책 (보안팀 정의) | [security_incident.py](shopping/models/security_incident.py) |

### 4.2 저장되지 않는 데이터 (NOT RECORDED)

| 데이터 유형 | 이유 |
|---|---|
| 평문 비밀번호 | 보안 정책 위반 |
| 전체 신용카드 번호 | PCI-DSS 준수 |
| 결제 인증 토큰 원문 | 보안 정책 위반 |
| 개인 식별 정보 (주민번호 등) | 개인정보보호법 준수 |
| 외부 시스템의 내부 오류 상세 | 제공되지 않음 (외부 시스템 책임) |

### 4.3 데이터 절삭 (Truncation) 정책

| 필드 | 최대 길이 | 코드 위치 |
|---|---|---|
| `error_message` | 500자 | [config.py#L357](shopping/services/self_healing/config.py#L357) `ForensicSettings.error_message_max_length` |
| `response_body` | 5000자 | [config.py#L358](shopping/services/self_healing/config.py#L358) `ForensicSettings.response_body_max_length` |
| `user_agent` | 500자 | [config.py#L359](shopping/services/self_healing/config.py#L359) `ForensicSettings.user_agent_max_length` |

---

## 5️⃣ Naming & Language Rules (명명 및 언어 규칙)

### 5.1 사용 금지 단어 (FORBIDDEN TERMS)

| 금지 단어 | 이유 | 대체 표현 |
|---|---|---|
| "AI가 판단" | 의사결정 주체 모호 | "정책에 정의된 조건 충족 시" |
| "자동으로 결정" | 의사결정 주체 모호 | "정책 작성자가 정의한 규칙에 따라 집행" |
| "시스템이 판단" | 의사결정 주체 모호 | "설정된 임계값 초과 시" |
| "시스템이 선택" | 자율적 선택 암시 | "정책에 정의된 경로에 따라" |
| "스스로 복구" | 자율성 암시 | "운영자 명령에 따라 재시도" |
| "지능적으로" | 추론 능력 암시 | 대안 없음 (사용 금지) |
| "학습하여" | ML 기능 암시 | 대안 없음 (사용 금지) |
| "예측하여" | 추론 능력 암시 | 대안 없음 (사용 금지) |
| "분석하여" | 추론 능력 암시 | "기록하여" |
| "최적화하여" | 자율 개선 암시 | "정책에 정의된 방식으로" |

### 5.2 사용 권장 단어 (RECOMMENDED TERMS)

| 권장 단어 | 의미 |
|---|---|
| "집행한다 (execute)" | 정의된 규칙을 적용함 |
| "기록한다 (record)" | 정보를 저장함 |
| "전달한다 (forward)" | 다른 주체에게 전송함 |
| "차단한다 (block)" | 요청을 거부함 |
| "에스컬레이션한다 (escalate)" | 사람에게 알림 전달 |
| "정책에 따라 (per policy)" | 사전 정의된 규칙에 의해 |
| "운영자 지시에 따라 (per operator command)" | 사람의 명시적 명령에 의해 |
| "임계값 초과 시 (when threshold exceeded)" | 정량적 조건 충족 |

### 5.3 "Self-Healing" 용어의 법적 정의

본 시스템에서 "Self-Healing"은 **의사결정 시스템이 아닙니다**.

| 포함되는 기능 | 포함되지 않는 기능 (로직 부재) |
|---|---|
| 정책 정의 조건의 수치 비교 | 장애 원인 분석/추론 |
| 충족 시 재시도 요청 전달 | 복구 전략 선택/생성 |
| 운영자 명령 집행 | 비즈니스 규칙 해석/적용 |
| DLQ 레코드 저장 | 외부 시스템 상태 추정/예측 |

> **법적 반박 근거**: "시스템 판단 오류"라는 주장은 기술적으로 성립 불가능합니다.
> - **근거 1**: 판단 기능이 코드에 존재하지 않음 (코드 검토로 확인 가능)
> - **근거 2**: 모든 동작은 정책 작성자가 정의한 조건의 수치 비교 결과임
> - **근거 3**: 비교 결과에 대응하는 동작은 정책에 의해 사전 고정되어 있음 (런타임 변경 불가)

---

## 6️⃣ Technical Scope Exclusions (기술적 범위 외 사항)

다음 기능은 시스템의 기술적 범위에 **포함되지 않으며, 해당 로직이 코드에 존재하지 않습니다**:

| 범위 외 기능 | 기술적 상태 | 대안 책임자 |
|---|---|---|
| 복구 전략 선택/생성 | 해당 로직 부재 | 정책 작성자 |
| 장애 원인 분석/추론 | 해당 로직 부재 | 운영자 |
| 외부 시스템 복구 확인 | 해당 로직 부재 | 운영자 |
| 보안 사건 복구/해결 | 해당 로직 부재 | 보안팀 |
| 재시도 전략 런타임 변경 | 해당 로직 부재 | 정책 작성자 (재배포 필요) |
| 결제 금액 불일치 조정 | 해당 로직 부재 | 운영자 |
| 사용자 보상 처리 | 해당 로직 부재 | 운영자 |
| 운영자 명령 없는 CB CLOSED 전환 | 해당 로직 부재 | 운영자 (force_close 필수) |

---

## 7️⃣ Execution Flow (집행 흐름 — 판단 로직 부재 검증)

### 7.1 재시도 집행 흐름 (판단 없음)

```
[실패 발생]
    ↓
[retry_count < max_attempts?]  ← 수치 비교만 수행 (max_attempts는 정책 작성자가 정의)
    │
    ├─ YES → [Backoff 값 산출]  ← 정책 작성자가 정의한 공식(4^n + jitter)에 수치 대입만 수행 (판단 없음)
    │           ↓
    │        [재시도 요청 전달]  ← 큐에 작업 추가만 수행 (성공 여부 판단 안 함)
    │
    └─ NO → [DLQ 저장]  ← 레코드 삽입만 수행 (복구 필요 여부 판단 안 함)
              ↓
           [운영자 검토 대기]  ← 사람이 Replay/Reject 결정
```

**관련 코드**: [retry_handler.py#L61-L100](shopping/services/self_healing/retry_handler.py#L61-L100) `RetryConfig`

### 7.2 Circuit Breaker 상태값 갱신 흐름 (판단 없음)

```
[CLOSED] ────────────────────────────────────────────────────────────┐
    │                                                                │
    │ failure_count >= failure_threshold (정책 정의)                  │
    ↓                                                                │
[OPEN] ──────────────────────────────────────────────────────────────┤
    │                                                                │
    │ recovery_timeout 경과 (정책 정의)                               │
    ↓                                                                │
[HALF_OPEN]                                                          │
    │                                                                │
    │ success_count >= success_threshold (정책 정의)                  │
    └────────────────────────────────────────────────────────────────┘

[수동 제어 (운영자)]
    │
    ├── force_open()  → 모든 상태에서 OPEN으로 전환
    └── force_close() → 모든 상태에서 CLOSED로 전환
```

**관련 코드**:
- 정책 조건 비교: [service.py#L198-L287](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py#L198-L287) — 수치 비교만 수행 (판단/선택 없음)
- 운영자 명령 처리: [manual_control.py#L41-L207](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py#L41-L207) — 명령 집행만 수행

### 7.3 보안 위반 처리 흐름 (판단 없음)

```
[보안 위반 유형 매칭]  ← ViolationType enum 값과 일치 여부만 확인
    ↓
[요청 거부]  ← HTTP 403 응답 반환 (정책: "enum에 정의된 유형은 모두 차단")
    ↓
[SecurityIncident 레코드 삽입]  ← DB INSERT만 수행
    ↓
[Slack 채널에 메시지 전송]  ← 정책 작성자가 지정한 채널로 전달
    ↓
[보안팀 조사]  ← 사람이 원인 분석
    ↓
[보안팀 해결/기각 결정]  ← 사람이 최종 판정
```

**관련 코드**: [security_violation_service.py#L153-L200](shopping/services/self_healing/security_violation_service.py#L153-L200)

---

## 8️⃣ Configuration Authority (설정 권한)

모든 정책 값은 `settings.SELF_HEALING` 딕셔너리에서 로드됩니다.

| 설정 그룹 | 설정 항목 | 기본값 | 정의 책임자 |
|---|---|---|---|
| `SLA` | `PAYMENT_HOURS` | 1 | 정책 작성자 |
| `SLA` | `POINT_HOURS` | 4 | 정책 작성자 |
| `RETRY` | `MAX_ATTEMPTS` | 3 | 정책 작성자 |
| `RETRY` | `BACKOFF_BASE` | 4 | 정책 작성자 |
| `CIRCUIT_BREAKER` | `FAILURE_THRESHOLD` | 5 | 정책 작성자 |
| `CIRCUIT_BREAKER` | `RECOVERY_TIMEOUT` | 60 | 정책 작성자 |
| `DLQ` | `RETENTION_DAYS` | 30 | 정책 작성자 |
| `SECURITY` | `RATE_LIMIT_MAX` | 100 | 정책 작성자 |

**관련 코드**: [config.py#L409-L449](shopping/services/self_healing/config.py#L409-L449) `SelfHealingConfig.load()`

---

## 9️⃣ Appendix: Record Creation Conditions (레코드 생성 조건)

| 모델 | 생성 조건 (판단 없음) | 수정 주체 | 삭제 조건/주체 |
|---|---|---|---|
| `FailedOperation` | 정책 정의 조건 `retry_count >= max_retries` 충족 시 레코드 삽입 (조건 비교만 수행) | 운영자 (상태 변경 명령) | 정책 정의 `retention_days` 경과 시 배치 삭제 집행 |
| `SecurityIncident` | 정책 정의 `ViolationType` enum 값 일치 시 레코드 삽입 (문자열 비교만 수행) | 보안팀 (조사 상태 변경 명령) | 보안팀 명령 |
| `CircuitBreakerState` | 첫 요청 수신 시 초기 레코드 삽입 | 운영자 명령 또는 정책 정의 수치 조건 충족 시 갱신 | N/A |
| `FailedPayment` | PG 응답 코드가 정책 정의 실패 코드 목록에 포함 시 레코드 삽입 (목록 포함 여부 비교만 수행) | 운영자 명령 | 정책 정의 `expires_at` 경과 시 배치 삭제 집행 |

---

## 10️⃣ Document Control

| 항목 | 값 |
|---|---|
| 문서 소유자 | 시스템 설계팀 (기술 내용) / 법무팀 (법적 표현) |
| 검토 주기 | 분기별 |
| 승인 필요 | 법무팀, 보안팀, 운영팀 |
| 변경 시 통보 대상 | 전 이해관계자 |

---

## 11️⃣ Legal Disclaimer (법적 면책 조항)

### 11.1 보증 범위 외 사항

본 시스템은 다음을 **보증하지 않으며, 기술적으로 보증할 수 없습니다**:

| 항목 | 사유 |
|---|---|
| 외부 시스템의 가용성/응답 정확성 | 외부 시스템 책임 |
| 재시도 성공 여부 | 요청 전달만 수행, 성공은 외부 시스템에 의존 |
| 복구 완료 | 운영자 명령 및 외부 시스템 응답에 의존 |
| 비즈니스 로직 정확성 | 상위 서비스 책임 |
| 정책 정의의 적절성 | 정책 작성자 책임 |

### 11.2 "시스템 판단 오류" 주장에 대한 법적 반박

해당 주장은 **기술적으로 성립 불가능**합니다:

| 반박 근거 | 증명 방법 |
|---|---|
| 판단 기능 코드 부재 | 코드 검토로 확인 가능 |
| 수치 비교만 수행 | 모든 동작은 `count >= threshold` 형태의 비교 결과 |
| 동작 사전 고정 | 비교 결과에 대응하는 동작은 정책에 의해 고정, 런타임 변경 불가 |
| 선택 기능 부재 | 복수 대안 중 선택하는 로직 없음 |
| 감사 증거 보존 | 모든 비교/집행 내역은 타임스탬프와 함께 기록됨 |

### 11.3 책임 귀속 요약

| 책임 영역 | 책임자 | 시스템 책임 |
|---|---|---|
| 정책 값 정의 (임계값, 조건, 공식) | 정책 작성자 | 없음 |
| force_open/force_close 명령 | 운영자 | 명령 집행만 |
| Replay/Reject 명령 | 운영자 | 명령 집행만 |
| 보안 사건 조사/해결 | 보안팀 | 없음 |
| 외부 시스템 응답 | 외부 시스템 | 없음 |
| 비즈니스 로직 | 상위 서비스 | 없음 |
| 수치 비교 정확성 | N/A | 코드에 의해 결정적 |

---

**END OF DOCUMENT**

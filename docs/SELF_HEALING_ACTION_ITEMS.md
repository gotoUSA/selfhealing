# Self-Healing System Action Items

> **생성일**: 2025-12-14  
> **목적**: 대화에서 도출된 모든 개선사항, 결정사항, 리뷰 피드백 정리  
> **상태**: Draft → 검토 → 확정 순으로 진행

---

## 📋 목차

1. [P0: 출시 전 필수](#-p0-출시-전-필수)
2. [P1: v1.0 ~ v1.5](#-p1-v10--v15)
3. [P2: 향후 개선](#-p2-향후-개선)
4. [유지해야 할 것 (변경 금지)](#-유지해야-할-것-변경-금지)
5. [제거해야 할 것](#-제거해야-할-것)
6. [이름 변경해야 할 것](#-이름-변경해야-할-것)
7. [외부화해야 할 것](#-외부화해야-할-것)
8. [법적/책임 방어 포인트](#-법적책임-방어-포인트)
9. [리뷰어 피드백 로그](#-리뷰어-피드백-로그)
10. [결정 변경 로그](#-결정-변경-로그)
11. [현재 시스템 상태 요약](#-현재-시스템-상태-요약)
12. [코드 위치 참조](#-코드-위치-참조)

---

## 🔴 P0: 출시 전 필수

### 보호 메커니즘

| # | 항목 | 현재 상태 | 필요 조치 | 관련 코드 |
|---|------|----------|----------|----------|
| P0-1 | **Tenant Isolation (논리적 테넌트 경계)** | ❌ 없음 | 모든 상태/정책/큐를 tenant_id 단위로 분리 | ControlRequest에 tenant_id 추가 필요 |
| P0-2 | **DRY_RUN / ACTIVE / SAFE_MODE 모드** | ❌ 없음 (management command에만 dry_run 존재) | Control API에 실행 모드 추가 | control_api_service.py |
| P0-3 | **Execution Authority (권한 검사)** | ⚠️ actor/actor_role 필드만 존재, 검사 로직 없음 | 역할별 액션 허용 매트릭스 구현 | control_api_service.py:143-144 |
| P0-4 | **Global/Tenant Safe Mode** | ❌ 없음 | 긴급 정지 스위치 구현 (전역 + 테넌트 단위) | 신규 구현 필요 |
| P0-5 | **Execution Attempt Proof (EAP)** | ❌ 없음 | 실행 시도 증거 기록 (별도 테이블) | 신규 구현 필요 |
| P0-6 | **Control API Rate Limit** | ❌ 없음 | Django throttling 또는 별도 구현 | views 레벨 |

### 프로덕션 빌드에서 제거

| # | 항목 | 현재 상태 | 필요 조치 | 관련 코드 |
|---|------|----------|----------|----------|
| P0-7 | **INJECT_FAILURE / INJECT_SUCCESS 엔드포인트** | Risk Matrix에서 OPS=FORBIDDEN이지만 코드 존재 | 프로덕션 빌드/라우팅에서 완전 제거 | control_api_service.py:414-510 |
| P0-8 | **memory_test_views.py 전체** | 1200줄+ 테스트 코드, 환경변수로 활성화 가능 | 프로덕션 빌드에서 제외 | shopping/views/memory_test_views.py |
| P0-9 | **미완성 Audit Log API** | "TODO: Implement actual audit log retrieval" | 비활성화 또는 완성 | views에서 TODO 존재 |

---

## 🟡 P1: v1.0 ~ v1.5

### 기능 개선

| # | 항목 | 현재 상태 | 필요 조치 | 우선순위 |
|---|------|----------|----------|----------|
| P1-1 | **Policy Versioning** | ❌ 없음 (policy_id, policy_version, effective_at 없음) | 정책 버전 관리 + 감사 추적 | 높음 |
| P1-2 | **비실행 시 이유 로깅 강화** | ⚠️ 실행된 경우만 로깅 | rejected/validation_error도 상세 로깅 | 높음 |
| P1-3 | **Audit Log 영구 저장** | ⚠️ logger.info()만, DB 저장 없음 | 별도 AuditLog 테이블 구현 | 높음 |
| P1-4 | **Grace Window (정책 옵션)** | ❌ 없음 | `grace_window_seconds: Optional[int] = None` | 중간 |
| P1-5 | **Semantic Validation** | ❌ 없음 | "이 서비스에 이 액션이 의미있는가?" 검증 | 중간 |
| P1-6 | **Hard Freeze Mode** | ❌ 없음 | Safe Mode와 별개로 "상태 변경 불가" 모드 | 중간 |
| P1-7 | **Rollback to Previous State** | ⚠️ RESET은 기본값 복원만 | "직전 상태로" 복원 기능 | 낮음 |
| P1-8 | **Input Validation 강화** | ⚠️ 기본 검증만 존재 | service_name 길이, reason 필수 등 강화 | 중간 |
| P1-9 | **Kill Switch per Service 개선** | ⚠️ CB OPEN 가능, "영구 비활성화" 없음 | 서비스별 영구 차단 기능 | 낮음 |

### 이름 변경 (법적 모호성 제거)

| # | 현재 | 변경 후 | 이유 |
|---|------|--------|------|
| P1-10 | `ReasonClassification` | `ReasonTag` | "분류"가 판단 암시 |
| P1-11 | `assess_risk_level()` | `lookup_risk_level()` 또는 `get_risk_level()` | "평가"가 판단 암시 |
| P1-12 | `classify_reason()` | `tag_reason()` | "분류"가 판단 암시 |
| P1-13 | `"AI/system assigned"` 주석 | 삭제 | AI 언급 완전 제거 |

---

## 🔵 P2: 향후 개선

### 복잡성 제거 (장애 복구에 영향 없음)

| # | 항목 | 현재 상태 | 필요 조치 |
|---|------|----------|----------|
| P2-1 | **미사용 Backoff 전략들** | LinearBackoff, ConstantBackoff, DecorrelatedJitterBackoff 존재 | ExponentialBackoff만 남기고 제거 |
| P2-2 | **_gather_evidence() 메서드** | 수집만 하고 실제 사용 안 됨 | 제거 또는 실제 활용 구현 |
| P2-3 | **ReasonClassification 전체** | 키워드 매칭, 장애 복구에 0% 기여 | 제거 또는 단순화 |

### 선택적 개선

| # | 항목 | 현재 상태 | 필요 조치 |
|---|------|----------|----------|
| P2-4 | **Emergency Contact 시스템 등록** | ❌ 없음 | 운영 문서로 대체 가능 |
| P2-5 | **Disclaimer 문구 (API 응답)** | ❌ 없음 | SaaS 제품화 시 고려 |
| P2-6 | **EXTERNAL 문서 영문 버전** | ❌ 없음 | 글로벌 파트너용 |

---

## ✅ 유지해야 할 것 (변경 금지)

현재 잘 구현되어 있으며 유지해야 할 것들:

| # | 항목 | 코드 위치 | 이유 |
|---|------|----------|------|
| K-1 | **Risk Assessment Matrix** | control_api_service.py:104-123 | (action x environment) → risk_level 매핑 완비 |
| K-2 | **Environment 분리 (TEST/CHAOS/OPS)** | constants.py | 환경별 동작 차별화 |
| K-3 | **TTL 강제 (OPS OVERRIDE 60분 제한)** | control_api_service.py:550-568 | 운영 안전장치 |
| K-4 | **INJECT_FAILURE OPS 금지 (FORBIDDEN)** | control_api_service.py:118 | Risk Matrix에서 차단 |
| K-5 | **SecurityViolationService** | security_violation_service.py | IP ban, injection 탐지 |
| K-6 | **Memory Throttling** | memory_test_views.py (THROTTLE_* 환경변수) | 백프레셔 구현 |
| K-7 | **Sanitization** | security_violation_service.py:463-498 | _sanitize_request_data() |
| K-8 | **INJECTION_ATTEMPT 탐지** | security_violation_service.py:51 | ViolationType에 정의 |
| K-9 | **Rate Limit Tracker** | circuit_breaker/rate_limit_tracker.py | 429 폭풍 감지 |

---

## 🗑️ 제거해야 할 것

### 프로덕션 빌드에서 완전 제거

| # | 대상 | 이유 | 조치 방법 |
|---|------|------|----------|
| R-1 | `INJECT_FAILURE` action | 공격 표면, 프로덕션에 존재 자체가 위험 | 라우팅에서 제거 또는 빌드 제외 |
| R-2 | `INJECT_SUCCESS` action | 위와 동일 | 라우팅에서 제거 또는 빌드 제외 |
| R-3 | `memory_test_views.py` 전체 | OOM 유발 가능, 환경변수로 활성화 위험 | urls.py에서 조건부 제외 |

### 코드에서 제거 (P2)

| # | 대상 | 이유 |
|---|------|------|
| R-4 | `LinearBackoff` 클래스 | 미사용 |
| R-5 | `ConstantBackoff` 클래스 | 미사용 |
| R-6 | `DecorrelatedJitterBackoff` 클래스 | 미사용 |
| R-7 | `"AI/system assigned"` 주석 | AI 언급 제거 필요 |

---

## ✏️ 이름 변경해야 할 것

| # | 현재 | 변경 후 | 위치 | 이유 |
|---|------|--------|------|------|
| N-1 | `class ReasonClassification` | `class ReasonTag` | control_api_service.py:38 | 판단 암시 제거 |
| N-2 | `def classify_reason()` | `def tag_reason()` | control_api_service.py:52 | 판단 암시 제거 |
| N-3 | `def assess_risk_level()` | `def lookup_risk_level()` | control_api_service.py:88 | 판단 암시 제거 |
| N-4 | docstring `"AI/system assigned"` | 삭제 | control_api_service.py:39 | AI 언급 제거 |

---

## 🔄 외부화해야 할 것

"시스템의 자율 판단"으로 보이는 로직을 이벤트 발행 방식으로 변경:

| # | 현재 로직 | 위치 | 외부화 방법 |
|---|----------|------|------------|
| E-1 | **자동 CB OPEN** (failure_count >= threshold) | service.py:225 | 이벤트 발행 + 외부 시스템/운영자가 결정 |
| E-2 | **자동 CB CLOSE** (success_count >= threshold) | service.py:262 | 이벤트 발행 + 외부 시스템/운영자가 결정 |
| E-3 | **Half-Open → Closed 전환** | service.py:260-287 | 이벤트 발행 후 운영자 확인 권장 |

### 외부화 예시 패턴

```python
# Before (자동 판단)
def record_failure(self, service_name: str) -> None:
    if failure_count >= threshold:
        self.open_circuit(...)  # 시스템이 결정

# After (이벤트 발행만)
def record_failure(self, service_name: str) -> FailureEvent:
    event = FailureEvent(
        service_name=service_name,
        failure_count=new_count,
        threshold=self.config.failure_threshold,
        recommendation="OPEN" if new_count >= threshold else "NONE"
    )
    self.event_bus.publish(event)  # 결정은 외부에서
    return event
```

---

## ⚖️ 법적/책임 방어 포인트

### 현재 반박 불가능한 항목

| # | 고객 주장 | 반박 가능 여부 | 이유 | 해결책 |
|---|----------|---------------|------|--------|
| L-1 | "시스템이 잘못 판단해서 사고났다" | ❌ 불가능 | Audit Log 휘발성 (logger.info만) | EAP + DB 저장 필요 |
| L-2 | "왜 그때 차단했는지 모르겠다" | ❌ 불가능 | 실행 전/후 상태 스냅샷 없음 | before_state/after_state 기록 |
| L-3 | "어떤 failure들이 있었는지 보여줘" | ⚠️ 부분 가능 | failure_count만 있고 개별 failure 기록 없음 | 개별 failure 이력 저장 |

### "시스템 판단" 책임 귀속 지점 (코드)

| # | 지점 | 코드 위치 | 책임 위험도 |
|---|------|----------|------------|
| J-1 | 자동 CB OPEN | service.py:225 | 🔴 높음 |
| J-2 | 자동 CB CLOSE | service.py:262 | 🔴 높음 |
| J-3 | Reason Classification | control_api_service.py:64-82 | 🟡 중간 |

### 법적 방어 강화 방안

1. **EAP (Execution Attempt Proof)** 구현 → 실행 시도 증거
2. **before_state / after_state** 기록 → 변경 전후 증거
3. **decision_basis** 필드 추가 → "왜 이 결정이 내려졌는지"
4. **Audit Log DB 저장** → 영구 보관

---

## 📝 리뷰어 피드백 로그

### 피드백 #1: 책임 범위 재정의
- **날짜**: 2025-12-13
- **내용**: Security, Cloud, Hardware, Distributed Consensus는 Self-Healing 책임 아님
- **조치**: Application Layer Self-Healing으로 범위 한정
- **상태**: ✅ 반영됨

### 피드백 #2: Pattern Coverage vs Product Completeness
- **날짜**: 2025-12-13
- **내용**: "10/10 pattern coverage"가 오해 소지. Library와 Control Plane 구분 필요
- **조치**: Boundaries, Guardrails, Invariants 관점으로 재분석
- **상태**: ✅ 반영됨

### 피드백 #3: Grace Window는 정책 옵션
- **날짜**: 2025-12-13
- **내용**: "항상 30초" 같은 고정값은 위험. 보안/차단은 즉시 실행이 맞을 수도 있음
- **조치**: Grace Window를 필수 → 정책 옵션으로 변경
- **상태**: ✅ 반영됨

### 피드백 #4: Tenant Isolation 필수
- **날짜**: 2025-12-13
- **내용**: "테넌트 간 영향 0" 요구사항. SaaS 관점에서 논리적 테넌트 경계 필수
- **조치**: Tenant Isolation을 불필요 → 필수로 변경
- **상태**: ✅ 반영됨

### 피드백 #5: Tenant Isolation ≠ 멀티테넌트 SaaS 인프라
- **날짜**: 2025-12-13
- **내용**: 필요한 것은 논리적 테넌트 경계 (정책, 상태, 큐, 실행 컨텍스트, Safe Mode 분리)
- **조치**: tenant_id 단위 분리 설계 필요
- **상태**: 🔄 설계 필요

---

## 🔄 결정 변경 로그

| 날짜 | 항목 | 이전 | 이후 | 이유 |
|------|------|------|------|------|
| 2025-12-13 | Grace Window | 필수 | 정책 옵션 | 시나리오별로 다름 (보안은 즉시, 결제는 유예) |
| 2025-12-13 | Tenant Isolation | 불필요 | **필수** | 테넌트 간 영향 0 요구사항, SaaS 가격 전략 |
| 2025-12-13 | Global Safe Mode | 필수 | **Tenant-level Safe Mode** | 테넌트 단위 분리 필요 |
| 2025-12-13 | 자동 CB OPEN/CLOSE | 유지 | **외부화 권장** | 법적 책임 최소화 |
| 2025-12-13 | Action Selection Logic 가치 | 핵심 | **5~10%** | 핵심은 수동 제어, 자동은 부가 기능 |

---

## 📊 현재 시스템 상태 요약

### Pipeline Coverage
- **현재**: 94% (8개 스테이지)
- **GAP 해결률**: 100% (8/8 GAP 해결)

### Must Have 구현 현황

| 항목 | 상태 |
|------|------|
| DRY_RUN/ACTIVE/SAFE_MODE | ❌ 없음 |
| Tenant Isolation | ❌ 없음 |
| Execution Authority (권한 검사) | ❌ 없음 (필드만 존재) |
| Global/Tenant Safe Mode | ❌ 없음 |
| Execution Attempt Proof (EAP) | ❌ 없음 |
| Control API Rate Limit | ❌ 없음 |

**결론**: 6개 필수 항목 중 **0개 완전 구현**

### 잘 되어 있는 것

| 항목 | 상태 |
|------|------|
| Risk Assessment Matrix | ✅ 완비 |
| Environment 분리 | ✅ 완비 |
| TTL 강제 | ✅ 완비 |
| INJECT_* OPS 금지 | ✅ Risk Matrix에서 FORBIDDEN |
| SecurityViolationService | ✅ 완비 |
| Sanitization | ✅ 완비 |

---

## 📍 코드 위치 참조

### Control API Service
- **파일**: `packages/selfhealing-python/src/selfhealing/services/control_api_service.py`
- **주요 라인**:
  - ReasonClassification: L38-82
  - Risk Matrix: L104-123
  - ControlRequest: L131-144
  - execute(): L235-289
  - INJECT_FAILURE: L414-489
  - TTL 검증: L550-568
  - _record_audit(): L587-607

### Circuit Breaker Service
- **파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py`
- **주요 라인**:
  - record_failure(): L201-235 (자동 OPEN 포함)
  - record_success(): L236-287 (자동 CLOSE 포함)
  - should_allow(): L135-163

### Manual Control
- **파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py`
- **주요 라인**:
  - force_open(): L41-95
  - force_close(): L97-160

### Security Violation Service
- **파일**: `packages/selfhealing-python/src/selfhealing/services/security_violation_service.py`
- **주요 라인**:
  - ViolationType: L47-57
  - _sanitize_request_data(): L463-498

### Memory Test Views
- **파일**: `shopping/views/memory_test_views.py`
- **상태**: 프로덕션 제거 대상

### Backoff Strategies
- **파일**: `packages/selfhealing-python/src/selfhealing/core/backoff.py`
- **주요 라인**:
  - ExponentialBackoff: L37-62 (유지)
  - LinearBackoff: L65-91 (제거 대상)
  - ConstantBackoff: L94-115 (제거 대상)
  - DecorrelatedJitterBackoff: L118-148 (제거 대상)

---

## ✅ 체크리스트

### 1차 체크 (2025-12-14)
- [x] P0 항목 누락 없음
- [x] P1 항목 누락 없음
- [x] P2 항목 누락 없음
- [x] 유지 항목 누락 없음
- [x] 제거 항목 누락 없음
- [x] 이름 변경 항목 누락 없음
- [x] 외부화 항목 누락 없음
- [x] 법적 방어 포인트 누락 없음
- [x] 리뷰어 피드백 5개 모두 기록
- [x] 결정 변경 로그 5개 모두 기록
- [x] 코드 위치 참조 완료

### 2차 체크 (2025-12-14)
- [x] Tenant Isolation 필수로 분류됨
- [x] Grace Window 정책 옵션으로 분류됨
- [x] INJECT_FAILURE 제거 대상으로 분류됨
- [x] memory_test_views.py 제거 대상으로 분류됨
- [x] 자동 CB OPEN/CLOSE 외부화 대상으로 분류됨
- [x] ReasonClassification → ReasonTag 변경 기록됨
- [x] assess_risk_level → lookup_risk_level 변경 기록됨
- [x] "AI/system assigned" 주석 삭제 기록됨
- [x] EAP 필수로 분류됨
- [x] Audit Log DB 저장 필요 기록됨

### 3차 체크 (2025-12-14)
- [x] 6가지 핵심 질문 답변 내용 모두 반영됨
  - Q1: 시스템 판단 지점 3개 (service.py:225, 262, control_api:64-82)
  - Q2: Action Selection Logic 제거 시 5-10% 훼손
  - Q3: 고객 주장 반박 불가능 (증거 부족)
  - Q4: 제거 가능 복잡성 3개 (ReasonClassification, Backoff들, _gather_evidence)
  - Q5: 의사결정 암시 3개 (AI 주석, assess 함수명, 자동 전환)
  - Q6: 외부화/제거 항목 모두 기록
- [x] 빼야 할 TOP 3 모두 반영됨 (INJECT, memory_test, Audit)
- [x] 리뷰어 피드백 중 "테넌트 간 영향 0" 요구사항 반영됨
- [x] 현재 시스템 상태 (Must Have 0/6 구현) 기록됨
- [x] 코드 위치 모두 정확함 확인

---

**END OF DOCUMENT**

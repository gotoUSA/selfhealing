# 개선 권장 사항 구현 가이드 종합

**문서 버전**: 1.0.0  
**작성일**: 2026-01-07  
**근거**: 실제 소스 코드 분석 기반 (추측 없음)

---

## 1. 문서 개요

이 문서는 Self-Healing 시스템의 4대 핵심 기능(Forensic, Governance, Security, Idempotency)에 대한 통합 갭 분석 및 개선 구현 가이드입니다.

### 1.1 관련 문서 목록

| 문서 | 설명 | 예상 공수 |
|------|------|----------|
| [26_IMPROVEMENT_PART1_GOVERNANCE_INTEGRATION.md](26_IMPROVEMENT_PART1_GOVERNANCE_INTEGRATION.md) | Governance 통합 개선 | 8시간 |
| [27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md](27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md) | Audit 통합 개선 | 11시간 |
| [28_IMPROVEMENT_PART3_ENUM_EXTENSION.md](28_IMPROVEMENT_PART3_ENUM_EXTENSION.md) | ViolationType & IdempotencyDomain 확장 | 8.5시간 |

**총 예상 공수**: 27.5시간

---

## 2. 발견된 Gap 요약

### 2.1 Governance 미연결 서비스

| 서비스 | 위치 | 문제점 | 우선순위 |
|--------|------|--------|----------|
| **ChaosSchedulerService** | [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) | `check_all_governance()` 호출 없음, Kill Switch/Emergency Mode 체크 없음 | 🔴 Critical |
| **AutoTuningService** | [service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py) | `check_all_governance()` 호출 없음, 조정 전 안전 체크 없음 | 🟡 High |

#### 근거 코드

```bash
# grep 검색 결과
$ grep -r "governance|is_system_enabled|kill_switch" scheduler.py
→ No matches found

$ grep -r "governance|is_system_enabled" auto_tuning/service.py
→ No matches found
```

### 2.2 Audit 미연결 서비스

| 서비스 | 위치 | 문제점 | 우선순위 |
|--------|------|--------|----------|
| **CorruptionShield** | [shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py#L205-240) | SecurityViolationService만 호출, RequestAuditBuffer 미사용 | 🔴 Critical |
| **ShadowLogger** | [shadow_logger.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py#L85-110) | logger.warning만 호출, Audit 시스템 미연결 | 🟡 High |
| **WriteAheadLog** | [wal.py](../../../packages/selfhealing-python/src/selfhealing/audit/wal.py) | WAL 이벤트(corruption, recovery) Audit 미기록 | 🟡 High |

#### 근거 코드

```python
# shield.py L225-230 - SecurityViolationService만 호출
service.record_violation(
    violation_type=f"corruption_{violation.code}",  # 동적 문자열 (비표준)
    details={...},
)
# ⚠️ RequestAuditBuffer.add() 호출 없음
```

### 2.3 누락된 AuditEventType

[event_buffer.py](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py#L46-120) 분석 결과:

| 필요한 이벤트 | 발생 시점 | 현재 상태 |
|--------------|----------|----------|
| `CORRUPTION_DETECTED` | L1/L2/L3 위반 발견 | ❌ 없음 |
| `CORRUPTION_BLOCKED` | 위반으로 요청 차단 | ❌ 없음 |
| `SHADOW_LOG_SYNC_FAILED` | L2 동기화 실패 | ❌ 없음 |
| `SHADOW_LOG_RECOVERED` | L2 복구 완료 | ❌ 없음 |
| `WAL_CORRUPTION_DETECTED` | CRC32 불일치 | ❌ 없음 |
| `WAL_RECOVERED` | WAL 복구 완료 | ❌ 없음 |
| `FORENSIC_CAPTURE_COMPLETED` | Forensic 캡처 완료 | ❌ 없음 |

### 2.4 누락된 ViolationType

[security_violation_service.py](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py#L38-50) 분석 결과:

| 필요한 타입 | 용도 | Severity |
|------------|------|----------|
| `ANOMALY_STATISTICAL` | L3 통계적 이상 감지 | HIGH |
| `ANOMALY_BEHAVIORAL` | 행위 이상 감지 | HIGH |
| `AUDIT_TAMPERING` | Audit 로그 조작 시도 | CRITICAL |
| `HASH_CHAIN_BROKEN` | 해시 체인 무결성 위반 | CRITICAL |
| `WAL_CORRUPTION` | WAL CRC32 불일치 | CRITICAL |
| `UNAUTHORIZED_OVERRIDE` | 권한 없는 설정 변경 | HIGH |
| `GOVERNANCE_BYPASS_ATTEMPT` | Governance 우회 시도 | CRITICAL |

### 2.5 누락된 IdempotencyDomain

[idempotency_service.py](../../../packages/selfhealing-python/src/selfhealing/services/idempotency_service.py#L42-50) 분석 결과:

| 필요한 도메인 | 용도 | 적용 대상 |
|--------------|------|----------|
| `CHAOS_EXPERIMENT` | 실험 중복 실행 방지 | ChaosScheduler |
| `CONFIG_CHANGE` | 설정 변경 중복 방지 | RuntimeConfigManager |
| `L2_SYNC` | 동기화 중복 방지 | ShadowLogger |
| `WAL_RECOVERY` | 복구 중복 방지 | WriteAheadLog |
| `AUTO_ADJUSTMENT` | 조정 중복 방지 | AutoTuningService |

---

## 3. 구현 우선순위 종합

### 3.1 Phase 1: Critical (주 1)

| 순위 | 작업 | 문서 | 공수 |
|------|------|------|------|
| 1 | ChaosScheduler Governance 통합 | Part 1 | 2시간 |
| 2 | 신규 AuditEventType 추가 | Part 2 | 1시간 |
| 3 | 신규 ViolationType 추가 | Part 3 | 1.5시간 |
| 4 | CorruptionShield Audit 통합 | Part 2 | 2시간 |

**Phase 1 합계**: 6.5시간

### 3.2 Phase 2: High (주 2)

| 순위 | 작업 | 문서 | 공수 |
|------|------|------|------|
| 5 | AutoTuningService Governance 통합 | Part 1 | 2시간 |
| 6 | execute_due_schedules 체크 추가 | Part 1 | 1시간 |
| 7 | ShadowLogger Audit 통합 | Part 2 | 1.5시간 |
| 8 | WAL Audit 통합 | Part 2 | 1.5시간 |
| 9 | 신규 IdempotencyDomain 추가 | Part 3 | 1시간 |

**Phase 2 합계**: 7시간

### 3.3 Phase 3: Medium (주 3)

| 순위 | 작업 | 문서 | 공수 |
|------|------|------|------|
| 10 | ForensicAuditBridge 구현 | Part 2 | 2시간 |
| 11 | CorruptionShield ViolationType 매핑 | Part 3 | 1시간 |
| 12 | ChaosScheduler Idempotency 적용 | Part 3 | 1.5시간 |
| 13 | IdempotencyKey 팩토리 메서드 추가 | Part 3 | 1.5시간 |

**Phase 3 합계**: 6시간

### 3.4 Phase 4: Testing (주 4)

| 순위 | 작업 | 문서 | 공수 |
|------|------|------|------|
| 14 | Governance 통합 테스트 | Part 1 | 3시간 |
| 15 | Audit 통합 테스트 | Part 2 | 3시간 |
| 16 | Enum 확장 테스트 | Part 3 | 2시간 |

**Phase 4 합계**: 8시간

---

## 4. 의존성 다이어그램

```
┌────────────────────────────────────────────────────────────────────┐
│                         Phase 1 (Critical)                         │
├────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐    ┌─────────────────────┐                │
│  │ 1. ChaosScheduler   │    │ 2. AuditEventType   │                │
│  │    Governance       │    │    신규 추가         │                │
│  └─────────────────────┘    └─────────────────────┘                │
│            │                          │                             │
│            │                          ▼                             │
│            │                ┌─────────────────────┐                │
│            │                │ 4. CorruptionShield │                │
│            │                │    Audit 통합       │                │
│            │                └─────────────────────┘                │
│            │                                                        │
│            │    ┌─────────────────────┐                            │
│            │    │ 3. ViolationType    │                            │
│            │    │    신규 추가         │                            │
│            │    └─────────────────────┘                            │
└────────────│────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────────────────┐
│                          Phase 2 (High)                            │
├────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐    ┌─────────────────────┐                │
│  │ 5. AutoTuning       │    │ 7. ShadowLogger     │                │
│  │    Governance       │    │    Audit            │                │
│  └─────────────────────┘    └─────────────────────┘                │
│                                       │                             │
│  ┌─────────────────────┐              │                            │
│  │ 6. Beat Task        │              ▼                            │
│  │    Governance       │    ┌─────────────────────┐                │
│  └─────────────────────┘    │ 8. WAL Audit        │                │
│                              └─────────────────────┘                │
│  ┌─────────────────────┐                                           │
│  │ 9. IdempotencyDomain│                                           │
│  │    신규 추가         │                                           │
│  └─────────────────────┘                                           │
└────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────────────────┐
│                         Phase 3 (Medium)                           │
├────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐    ┌─────────────────────┐                │
│  │ 10. ForensicAudit   │    │ 11. ViolationType   │                │
│  │     Bridge          │    │     매핑             │                │
│  └─────────────────────┘    └─────────────────────┘                │
│                                       │                             │
│  ┌─────────────────────┐              │                            │
│  │ 12. ChaosScheduler  │◄─────────────┘                            │
│  │     Idempotency     │                                           │
│  └─────────────────────┘                                           │
│            │                                                        │
│            ▼                                                        │
│  ┌─────────────────────┐                                           │
│  │ 13. IdempotencyKey  │                                           │
│  │     팩토리 메서드    │                                           │
│  └─────────────────────┘                                           │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. 검증 체크리스트

### 5.1 Governance 통합 검증

- [ ] ChaosScheduler.execute_now()에서 `check_all_governance()` 호출 확인
- [ ] AutoTuningService.start()에서 `check_all_governance()` 호출 확인
- [ ] execute_due_schedules()에서 전역 Governance 체크 확인
- [ ] Kill Switch 활성화 시 실험 차단 확인
- [ ] Emergency Mode LEVEL_2+ 에서 조정 차단 확인

### 5.2 Audit 통합 검증

- [ ] CorruptionShield에서 `RequestAuditBuffer.add()` 호출 확인
- [ ] ShadowLogger에서 Audit 이벤트 기록 확인
- [ ] WAL에서 corruption/recovery 이벤트 기록 확인
- [ ] 신규 AuditEventType이 올바르게 기록되는지 확인

### 5.3 Enum 확장 검증

- [ ] 신규 ViolationType이 SecurityViolationService에서 사용 가능
- [ ] 신규 ViolationType에 Severity 매핑 존재
- [ ] 신규 IdempotencyDomain이 IdempotencyService에서 사용 가능
- [ ] 신규 IdempotencyKey 팩토리 메서드가 올바른 키 생성

---

## 6. 참고 코드 위치

| 기능 | 파일 위치 |
|------|----------|
| **Governance 체크** | `packages/selfhealing-python/src/selfhealing/services/governance_checks.py` |
| **Chaos Scheduler** | `packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py` |
| **Auto Tuning** | `packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py` |
| **Audit Event Buffer** | `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py` |
| **Corruption Shield** | `packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py` |
| **Shadow Logger** | `packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py` |
| **WAL** | `packages/selfhealing-python/src/selfhealing/audit/wal.py` |
| **Security Violation** | `packages/selfhealing-python/src/selfhealing/services/security_violation_service.py` |
| **Idempotency** | `packages/selfhealing-python/src/selfhealing/services/idempotency_service.py` |

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-01-07 | 초기 문서 작성 |

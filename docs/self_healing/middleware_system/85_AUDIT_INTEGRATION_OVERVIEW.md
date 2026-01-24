# 85. Audit 연동 누락 기능 통합 - 개요

## 1. 문서 목적

`selfhealing.audit` 및 `selfhealing.services.audit` 시스템과 연동되지 않은 기능들을 식별하고, 통합 연동을 위한 구현 가이드를 제공한다.

## 2. 분석 배경

새로운 기능들이 추가되면서 기존 audit 미들웨어와의 연동이 누락된 경우가 발견되었다. 일부 서비스는 `logger.warning`만 사용하거나, 독립적인 audit 시스템을 구현하여 중앙 audit trail과 분리되어 있다.

## 3. 기존 Audit 시스템 구조

### 3.1 핵심 모듈

| 모듈 | 위치 | 역할 |
|------|------|------|
| AuditLogger | `selfhealing.audit.logger` | 설정 변경 audit 로깅 (메인 진입점) |
| CascadeEventAuditor | `selfhealing.audit.cascade_auditor` | Cascade 이벤트 감사 |
| services.audit | `selfhealing.services.audit` | 도메인별 audit 헬퍼 함수 모음 |
| WAL | `selfhealing.audit.wal` | Write-Ahead Log 영속성 |

### 3.2 제공되는 Audit 헬퍼 함수

`selfhealing.services.audit` 패키지에서 제공하는 함수들:

**DLQ 관련**
- `log_dlq_store_audit` - DLQ 저장 기록
- `log_dlq_replay_audit` - DLQ 재처리 기록

**Circuit Breaker 관련**
- `log_cb_state_change_audit` - CB 상태 변경
- `log_governance_blocked_audit` - Governance 차단
- `log_cb_state_change_with_trace_audit` - Trace ID 포함 CB 상태 변경

**시스템 제어**
- `log_system_control_audit` - 시스템 제어 액션
- `log_rollback_audit` - 롤백 기록
- `log_retry_audit` - 재시도 기록

**Chaos & Emergency**
- `log_chaos_experiment_audit` - Chaos 실험
- `log_emergency_mode_audit` - 긴급 모드
- `log_kill_switch_override_audit` - Kill Switch

**Compliance & FinOps**
- `log_compliance_audit` - 컴플라이언스
- `log_finops_audit` - FinOps
- `log_data_access_audit` - 데이터 접근

**Storage & Tasks**
- `log_storage_failure_audit` - 저장소 실패
- `log_drift_detection_audit` - Drift 감지

## 4. 발견된 누락 항목

> **참고**: 이 프로젝트의 표준 패턴은 **서비스 레이어**에서 `log_*_audit()` 헬퍼를 호출하는 것입니다.
> View에서 직접 audit을 호출하지 않으며, 서비스 레이어가 헬퍼를 호출하면 AuditMiddleware가 자동으로 수집합니다.
> (56_AUDIT_MIDDLEWARE_DESIGN.md 참조)

### 4.1 Services - Audit 연동 상태 (8개)

| 서비스 | 파일 | 누락된 기능 | 우선순위 | 상태 |
|--------|------|------------|----------|------|
| SecurityViolationService | `services/security/service.py` | 보안 위반 처리, IP 차단, 세션 무효화 | **P0** | ✅ 완료 (2026-01-24) |
| RegionalIsolationGate | `services/isolation/regional_gate.py` | 리전 격리/해제 | **P0** | ✅ 완료 (2026-01-24) |
| BlastRadiusService | `services/blast_radius/service.py` | set_policy, add_dependency, isolate_service | **P0** | ✅ 완료 (2026-01-24) |
| CleanupService | `services/cleanup_service.py` | archive_old_dlq_entries, cleanup_expired_config, purge_archived_dlq_entries | **P1** | ⏳ 대기 |
| PendingConfigService | `services/pending_config.py` | cancel_pending_change | **P1** | ⏳ 대기 |
| ConfigHistoryService | `services/config_history.py` | 설정 버전 저장/롤백 | **P1** | ⏳ 대기 |
| AdaptiveThrottle | `services/throttle/adaptive.py` | 동적 속도 제한 조절 | **P1** | ⏳ 대기 |
| LearningService | `services/learning/service.py` | 파라미터 블랙리스트 등록, 패턴 학습 | **P1** | ⏳ 대기 |

### 4.2 서비스 레이어에서 이미 연동된 항목 (참고)

다음 서비스들은 **이미 서비스 레이어에서 audit 헬퍼를 호출**하므로 View에서 별도 연동 불필요:

| 서비스 | 파일 | 사용 중인 audit 함수 | 라인 |
|--------|------|---------------------|------|
| SystemControlService | `services/system_control.py` | `log_system_control_audit` | L165 |
| EmergencyModeManager | `services/emergency_mode/manager.py` | `log_emergency_mode_audit` | L829 |
| RollbackService | `services/rollback/service.py` | `log_rollback_audit` | L126 |
| FinOpsService | `services/finops/service.py` | `log_finops_audit` | L228 |
| ComplianceService | `services/compliance/service.py` | `log_compliance_audit` | L194 |
| DLQService | `services/dlq/base.py` | `log_dlq_store_audit`, `log_dlq_replay_audit` | L87-100 |
| CanaryAudit | `services/canary/audit.py` | `log_system_control_audit` | L166 |
| RetryHandler | `services/retry_handler.py` | `log_retry_audit` | L233 |
| ReplayService | `services/replay_service.py` | `log_dlq_replay_audit` | L33 |
| CircuitBreaker ManualControl | `services/circuit_breaker/manual_control.py` | `log_cb_state_change_audit`, `log_kill_switch_override_audit` | L101, L151 |
| FreezeMode | `services/circuit_breaker/freeze_mode.py` | `log_freeze_mode_audit` | L178 |
| PanicThreshold | `services/circuit_breaker/panic_threshold.py` | `log_panic_threshold_audit` | L314 |
| ErrorBudgetGate | `services/error_budget_gate/gate.py` | `log_error_budget_blocked_audit` | L475 |
| ChaosGuard | `services/chaos/safety_guard/guard.py` | `log_governance_blocked_audit` | L560 |

### 4.3 더미 Adapter 사용 (Refactoring Required)

| 컴포넌트 | 파일 | 문제점 | 해결방안 | 우선순위 |
|----------|------|--------|----------|----------|
| auto_tuning view | `api/django/views/auto_tuning.py` | DummyAuditAdapter 사용, logger.info만 호출 | `ProviderRegistry.get_audit_adapter()` 교체 | **P1** |

> **상세**: `_create_default_service()`에서 DummyAuditAdapter 클래스 삭제 후, 
> `ProviderRegistry.get_audit_adapter()`로 실제 audit adapter 주입. (87번 문서 섹션 7 참조)

### 4.4 독립 Audit 시스템 (통합 검토 필요)

| 컴포넌트 | 파일 | 상태 |
|----------|------|------|
| EscalationAuditTrail | `services/namespace_emergency/escalation_audit.py` | 독립 구현, 통합 가능 |

## 5. 연동 원칙

### 5.1 필수 기록 대상

다음 유형의 이벤트는 반드시 audit 시스템에 기록되어야 한다:

1. **상태 전환** - 시스템/컴포넌트 상태 변경
2. **설정 변경** - 런타임 설정 수정
3. **보안 이벤트** - 위반 감지, 차단 조치
4. **수동 개입** - 운영자의 수동 조치
5. **격리/복구** - 장애 격리 및 복구 작업

### 5.2 Audit 함수 선택 가이드

| 이벤트 유형 | 권장 함수 |
|------------|----------|
| 보안 위반/차단 | `log_data_access_audit` 또는 신규 `log_security_violation_audit` |
| 리전/클러스터 격리 | `log_system_control_audit` |
| 속도 제한 변경 | `log_system_control_audit` |
| 학습 패턴 등록 | `log_system_control_audit` 또는 신규 `log_learning_audit` |
| 복구 작업 | `log_system_control_audit` |
| 임계값 변경 | `log_config_apply_audit` |

### 5.3 표준 Audit Entry 필드

모든 audit 기록은 다음 필드를 포함해야 한다:

- `action` - 수행된 액션 (예: "isolate_region", "block_ip")
- `target` - 대상 (예: "region:tokyo", "ip:1.2.3.4")
- `result` - 결과 (예: "success", "failed")
- `operator` - 수행 주체 (예: "system", "admin@example.com")
- `details` - 상세 정보 딕셔너리

## 6. 구현 문서 구성 및 구현 순서

> **중요**: 이 프로젝트에서 audit 연동의 정석은 **서비스 레이어**에서 `log_*_audit()` 헬퍼를 호출하는 것입니다.
> View는 서비스를 호출하고, 서비스가 audit 헬퍼를 호출합니다. (56_AUDIT_MIDDLEWARE_DESIGN.md 참조)
> 따라서 **View에서 별도 audit 작업 불필요** - 서비스 레이어만 수정하면 됩니다.

### 6.1 문서별 구현 대상

| 문서 | 대상 | 파일 수 | 상태 |
|------|------|---------|------|
| 86_AUDIT_INTEGRATION_SECURITY_ISOLATION.md | Security, Isolation, BlastRadius 서비스 | 3개 서비스 | ✅ 완료 |
| 87_AUDIT_INTEGRATION_LEARNING_RECOVERY.md | Learning, Throttle, Cleanup, PendingConfig, ConfigHistory 서비스 | 5개 서비스 | ⏳ 대기 |

> **삭제된 문서**: 88, 89번은 View 레벨 audit을 다루었으나, 이 프로젝트의 표준 패턴에 따라 View는 서비스를 호출하므로 별도 문서 불필요.

### 6.2 구현 순서 (Phase별) - 서비스 레이어만

#### Phase 1: Critical (즉시 - 1주 내) ✅ 완료 (2026-01-24)
**문서 참조**: 86

| 순서 | 파일 | 이유 | 상태 |
|------|------|------|------|
| 1 | `services/security/service.py` | 보안 위반 - 컴플라이언스 필수 | ✅ 완료 |
| 2 | `services/isolation/regional_gate.py` | 리전 격리 - 운영 영향도 높음 | ✅ 완료 |
| 3 | `services/blast_radius/service.py` | 서비스 격리 - 장애 범위 제어 | ✅ 완료 |

#### Phase 2: High (1-2주)
**문서 참조**: 87

| 순서 | 파일 | 이유 |
|------|------|------|
| 4 | `services/cleanup_service.py` | 데이터 삭제/아카이브 |
| 5 | `services/pending_config.py` | 설정 변경 예약/취소 |
| 6 | `services/config_history.py` | 설정 버전 이력 |
| 7 | `api/django/views/auto_tuning.py` | DummyAuditAdapter → ProviderRegistry.get_audit_adapter() 교체 |

> **7번 상세**: DummyAuditAdapter 클래스 삭제 후 `ProviderRegistry.get_audit_adapter()` 호출로 교체.
> 자세한 구현 방안은 87번 문서 섹션 7 참조.

#### Phase 3: Medium (2-3주)
**문서 참조**: 87

| 순서 | 파일 | 이유 |
|------|------|------|
| 8 | `services/throttle/adaptive.py` | 동적 속도 제한 |
| 9 | `services/learning/service.py` | 자가학습 패턴 |

## 7. 관련 문서

- 20_AUDIT_UNIFICATION_PLAN.md - Audit 통합 계획 (초기)
- 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md - Audit 통합 개선
- 76_CASCADE_EVENT_AUDIT.md - Cascade Event Audit 설계

# 87. Learning, Throttle, Cleanup 및 기타 서비스 Audit 연동 구현

## 1. 개요

이 문서는 서비스 레이어에서 audit 연동이 누락된 기능들의 구현 방안을 정의한다.

> **중요**: 이 프로젝트의 표준 패턴은 **서비스 레이어**에서 `log_*_audit()` 헬퍼를 호출하는 것이다.
> View는 서비스를 호출하고, 서비스가 audit을 담당한다. (56_AUDIT_MIDDLEWARE_DESIGN.md 참조)

### 1.1 대상 서비스 (5개)

| 서비스 | 파일 | 우선순위 |
|--------|------|----------|
| LearningService | `services/learning/service.py` | Medium |
| AdaptiveThrottle | `services/throttle/adaptive.py` | Medium |
| CleanupService | `services/cleanup_service.py` | High |
| PendingConfigService | `services/pending_config.py` | High |
| ConfigHistoryService | `services/config_history.py` | High |

### 1.2 특수 케이스 (1개)

| 대상 | 파일 | 문제 |
|------|------|------|
| AutoTuning View | `api/django/views/auto_tuning.py` | DummyAuditAdapter 제거 필요 |

---

## 2. LearningService 연동

### 2.1 대상 파일

- **위치**: `selfhealing/services/learning/service.py`
- **클래스**: `LearningService`

### 2.2 현재 상태

자가학습 DNA 서비스에서 파라미터 블랙리스트 등록, 패턴 학습 등 중요 이벤트가 audit 되지 않음.

### 2.3 연동이 필요한 메서드

| 메서드 | 발생 이벤트 | 연동 필요성 |
|--------|------------|------------|
| `register_blacklist` | 파라미터 블랙리스트 등록 | High - 학습 제외 항목 추적 |
| `start_session` | 학습 세션 시작 | Medium |
| `end_session` | 학습 세션 종료 | Medium |
| `update_pattern` | 패턴 업데이트 | High - 패턴 변경 이력 |
| `apply_learned_config` | 학습된 설정 적용 | Critical |

### 2.4 구현 방안

#### 2.4.1 register_blacklist 연동

**기록할 정보**:
- `action`: "parameter_blacklisted"
- `parameter_name`: 블랙리스트에 추가된 파라미터
- `reason`: 블랙리스트 사유
- `operator`: 시스템 또는 관리자

#### 2.4.2 apply_learned_config 연동

**기록할 정보**:
- `action`: "learned_config_applied"
- `config_version`: 적용된 설정 버전
- `changed_parameters`: 변경된 파라미터 목록
- `confidence_score`: 학습 신뢰도

---

## 3. AdaptiveThrottle 연동

### 3.1 대상 파일

- **위치**: `selfhealing/services/throttle/adaptive.py`
- **클래스**: `AdaptiveThrottle`

### 3.2 현재 상태

Netflix Gradient 기반 적응형 속도 제한 서비스에서 동적 조절 이벤트가 audit 되지 않음.

### 3.3 연동이 필요한 메서드

| 메서드 | 발생 이벤트 | 연동 필요성 |
|--------|------------|------------|
| `adjust_limit` | 속도 제한 조절 | High - 동적 변경 추적 |
| `set_baseline` | 기준선 설정 | Medium |
| `trigger_emergency_limit` | 긴급 제한 발동 | Critical |

### 3.4 구현 방안

#### 3.4.1 권장 Audit 함수

`log_system_control_audit` 활용.

#### 3.4.2 adjust_limit 연동

**기록할 정보**:
- `action`: "throttle_limit_adjusted"
- `previous_limit`: 이전 제한값
- `new_limit`: 새 제한값
- `adjustment_reason`: 조절 사유 (gradient, load 등)
- `current_load`: 현재 부하율

#### 3.4.3 trigger_emergency_limit 연동

**기록할 정보**:
- `action`: "emergency_throttle_activated"
- `trigger_condition`: 발동 조건
- `limit_applied`: 적용된 긴급 제한

---

## 4. CleanupService 연동

### 4.1 대상 파일

- **위치**: `selfhealing/services/cleanup_service.py`
- **클래스**: `CleanupService`

### 4.2 현재 상태

데이터 정리/아카이브/삭제 서비스에서 audit 연동 없음. 데이터 삭제는 감사 필수 대상.

### 4.3 연동이 필요한 메서드

| 라인 | 메서드 | 발생 이벤트 | 연동 필요성 |
|------|--------|------------|------------|
| L57 | `archive_old_dlq_entries` | DLQ 아카이브 | High |
| L98 | `cleanup_expired_config` | 만료 설정 정리 | High |
| L183 | `purge_archived_dlq_entries` | 아카이브 삭제 | **Critical** |

### 4.4 구현 방안

#### 4.4.1 권장 Audit 함수

`log_dlq_store_audit` 또는 신규 `log_cleanup_audit` 정의

#### 4.4.2 purge_archived_dlq_entries 연동

**기록할 정보**:
- `action`: "archived_dlq_purged"
- `purge_criteria`: 삭제 기준 (날짜, 개수 등)
- `affected_count`: 삭제된 항목 수
- `operator`: "system" (자동) 또는 관리자

#### 4.4.3 archive_old_dlq_entries 연동

**기록할 정보**:
- `action`: "dlq_archived"
- `archive_criteria`: 아카이브 기준
- `archived_count`: 아카이브된 항목 수

---

## 5. PendingConfigService 연동

### 5.1 대상 파일

- **위치**: `selfhealing/services/pending_config.py`

### 5.2 현재 상태

설정 변경 예약/취소 서비스에서 audit 연동 없음.

### 5.3 연동이 필요한 메서드

| 라인 | 메서드 | 발생 이벤트 | 연동 필요성 |
|------|--------|------------|------------|
| L201 | `cancel_pending_change` | 대기 중인 변경 취소 | High |

### 5.4 구현 방안

#### 5.4.1 권장 Audit 함수

`log_config_change` 사용

#### 5.4.2 cancel_pending_change 연동

**기록할 정보**:
- `action`: "pending_config_cancelled"
- `config_name`: 설정 이름
- `scheduled_at`: 예약 시간
- `cancel_reason`: 취소 사유
- `operator`: 요청자

---

## 6. ConfigHistoryService 연동

### 6.1 대상 파일

- **위치**: `selfhealing/services/config_history.py`

### 6.2 현재 상태

설정 버전 저장/롤백 서비스에서 audit 연동 없음.

### 6.3 구현 방안

#### 6.3.1 권장 Audit 함수

`log_config_change` 또는 `log_rollback_audit` 사용

#### 6.3.2 rollback 관련 연동

**기록할 정보**:
- `action`: "config_history_rollback"
- `config_name`: 설정 이름
- `from_version`: 이전 버전
- `to_version`: 롤백 대상 버전
- `operator`: 요청자

---

## 7. AutoTuning View - DummyAuditAdapter 교체

### 7.1 대상 파일

- **위치**: `selfhealing/api/django/views/auto_tuning.py`

### 7.2 현재 상태 (문제점)

`_create_default_service()` 함수에서 `DummyAuditAdapter`를 정의하여 사용 중:

```python
# 현재 코드 (L67-69)
class DummyAuditAdapter:
    def log(self, entry):
        logger.info(f"[Audit] {entry}")  # 실제 audit 시스템에 기록되지 않음!
```

**문제**: `AutoTuningService`는 `self.audit_adapter.log(entry)`를 호출하지만 (service.py L891), 
DummyAuditAdapter는 단순 로깅만 하고 **실제 audit 시스템과 연결되지 않음**.

### 7.3 원인 분석

1. `factory.py`에 `get_auto_tuning_service()` 함수가 **없음**
2. `_get_auto_tuning_service()`가 ImportError → fallback으로 `_create_default_service()` 호출
3. 결과적으로 모든 Dummy 어댑터 사용

### 7.4 수정 방안

#### 방안 A: _create_default_service()에서 실제 AuditAdapter 사용 (권장)

`ProviderRegistry.get_audit_adapter()`가 이미 존재하므로 이를 활용:

```python
# 수정 후 코드
def _create_default_service():
    """기본 AutoTuningService 생성"""
    from selfhealing.services.auto_tuning import AutoTuningService
    from selfhealing.factory import ProviderRegistry
    
    # ... 다른 Dummy 어댑터들 ...
    
    # DummyAuditAdapter 제거하고 실제 어댑터 사용
    audit_adapter = ProviderRegistry.get_audit_adapter()
    
    return AutoTuningService(
        metrics_adapter=DummyMetricsAdapter(),
        config_provider=DummyConfigProvider(),
        config_applier=DummyConfigApplier(),
        audit_adapter=audit_adapter,  # 실제 어댑터 사용
    )
```

#### 방안 B: factory.py에 get_auto_tuning_service() 추가

`ProviderRegistry`에 완전한 `get_auto_tuning_service()` 팩토리 메서드를 추가하여 
모든 어댑터를 실제 구현체로 연결.

### 7.5 권장 방안: A

- 변경 범위가 작음 (auto_tuning.py 한 파일만 수정)
- DummyAuditAdapter 클래스 정의 삭제
- `ProviderRegistry.get_audit_adapter()` 호출로 교체

### 7.6 체크리스트

- [ ] DummyAuditAdapter 클래스 정의 삭제
- [ ] `from selfhealing.factory import ProviderRegistry` 추가
- [ ] `ProviderRegistry.get_audit_adapter()` 호출로 교체
- [ ] 단위 테스트에서 실제 audit 기록 확인

---

## 8. 우선순위별 구현 순서

> **참고**: Recovery, DriftThreshold View는 이미 서비스 레이어에서 audit 연동됨.
> - recovery.py → RecoveryCoordinator가 log_recovery_audit 사용
> - drift_threshold.py → DriftDetectionService가 log_drift_detection_audit 사용

### Phase 1 (High - 1주 내)

| 순서 | 대상 | 이유 |
|------|------|------|
| 1 | CleanupService | 데이터 삭제/아카이브 - 감사 필수 |
| 2 | PendingConfigService | 설정 변경 예약/취소 |
| 3 | ConfigHistoryService | 설정 버전 이력 |
| 4 | AutoTuning View | DummyAuditAdapter 제거 (특수 케이스) |

### Phase 2 (Medium - 2주 내)

| 순서 | 대상 | 이유 |
|------|------|------|
| 5 | AdaptiveThrottle | 동적 속도 제한 |
| 6 | LearningService | 자가학습 패턴 |

---

## 9. 검증 체크리스트

### LearningService

- [ ] register_blacklist audit 기록
- [ ] apply_learned_config audit 기록
- [ ] 단위 테스트 추가

### AdaptiveThrottle

- [ ] adjust_limit audit 기록
- [ ] trigger_emergency_limit audit 기록
- [ ] 단위 테스트 추가

### CleanupService

- [ ] archive_old_dlq_entries audit 기록
- [ ] cleanup_expired_config audit 기록
- [ ] purge_archived_dlq_entries audit 기록
- [ ] 단위 테스트 추가

### PendingConfigService

- [ ] cancel_pending_change audit 기록
- [ ] 단위 테스트 추가

### ConfigHistoryService

- [ ] rollback 관련 audit 기록
- [ ] 단위 테스트 추가

### AutoTuning View (특수 케이스)

- [ ] DummyAuditAdapter 제거
- [ ] 실제 audit 함수 연동
- [ ] 단위 테스트 추가

---

## 10. 관련 문서

- 85_AUDIT_INTEGRATION_OVERVIEW.md - 개요
- 86_AUDIT_INTEGRATION_SECURITY_ISOLATION.md - Security, Isolation 연동
- 20_AUDIT_UNIFICATION_PLAN.md - 기존 통합 계획
- 56_AUDIT_MIDDLEWARE_DESIGN.md - Audit 미들웨어 설계 원칙

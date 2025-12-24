# Governance Implementation Roadmap

> 전체 거버넌스 구현 계획서 - RBAC, 긴급 에스컬레이션, 임계값 기반 권한, Config 통합

---

## 문서 목적

지금까지 논의한 거버넌스 관련 모든 내용을 정리하고, 구현 우선순위와 계획을 수립합니다.

---

## 1. 현재 구현 완료 항목 ✅

### 1.1 기본 RBAC

| 항목 | 파일 | 상태 |
|------|------|------|
| IsViewer | `permissions.py` | ✅ |
| IsOperator | `permissions.py` | ✅ |
| IsSelfHealingAdmin | `permissions.py` | ✅ |
| Django Groups (post_migrate) | `apps.py` | ✅ |
| 테스트 | `test_rbac_permissions.py` | ✅ |

### 1.2 긴급 에스컬레이션 (Break Glass)

| 항목 | 파일 | 상태 |
|------|------|------|
| EmergencyEscalationPermission | `permissions.py` | ✅ |
| 일방향 긴급권 (STRICT: Operator, NORMAL: Admin) | `permissions.py` | ✅ |
| EmergencyModeTracker | `services/governance.py` | ✅ |
| 자동 복귀 Celery Beat 태스크 | `tasks/governance.py` | ✅ |
| 테스트 | `test_emergency_escalation.py`, `test_governance_phase1.py` | ✅ |

### 1.3 임계값 기반 권한 (Risk-Based)

| 항목 | 파일 | 상태 |
|------|------|------|
| ThresholdBasedPermission | `permissions.py` | ✅ |
| 환경변수 임계값 설정 | `permissions.py` | ✅ |
| RuntimeConfigManager 연동 | `permissions.py` | ✅ |
| 테스트 | `test_threshold_permission.py`, `test_governance_phase1.py` | ✅ |

### 1.4 GovernanceConfig (Phase 1 완료) ✅

| 항목 | 파일 | 상태 |
|------|------|------|
| GovernanceConfig 데이터클래스 | `core/config.py` | ✅ |
| RuntimeConfigManager governance 통합 | `services/runtime_config.py` | ✅ |
| get_governance_config/update_governance_config | `services/runtime_config.py` | ✅ |
| 테스트 | `test_governance_phase1.py` | ✅ |

---

## 2. 추가 구현 필요 항목

### 2.1 ~~P1: 임계값의 런타임 설정화~~ ✅ 완료

**구현 완료:**
- GovernanceConfig 데이터클래스 생성
- RuntimeConfigManager에 governance 키 추가
- ThresholdBasedPermission이 RuntimeConfigManager 우선 조회
- 환경변수 폴백 지원

**구현된 코드 (permissions.py):**
```python
# 우선순위: RuntimeConfigManager > 환경변수 > 기본값
def _get_thresholds(self) -> Dict[str, float]:
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        governance = manager.get_governance_config()
        if governance:
            return {
                "operator_approve": governance.get("threshold_operator", 0.15),
                "admin_approve": governance.get("threshold_admin", 0.30),
            }
    except Exception:
        pass  # Fallback to env vars
    return {...}  # env vars
```

**API 사용 예시:**
```http
PUT /api/self-healing/config/governance/
{
    "threshold_operator": 0.20,
    "threshold_admin": 0.40,
    "emergency_expiry_hours": 6
}
```

---

### 2.2 ~~P1: 만료 후 자동 복귀 (Self-Cleanup)~~ ✅ 완료

**구현 완료:**
- EmergencyModeTracker 클래스 생성
- Celery Beat 태스크 추가 (`check_emergency_mode_expiry`)
- 자동 복귀 로직 구현
- 알림 발송 인터페이스

**자동 복귀 동작:**

| 시간 | 동작 | 상태 |
|------|------|------|
| 0h | 긴급 모드 전환 (Operator) | ✅ |
| 4h | 경고 알림 (Admin 전원) | ✅ |
| 6h | 최종 경고 ("2시간 후 자동 복귀") | ✅ |
| 8h | Safe Default로 자동 복귀 | ✅ |

**설정 (GovernanceConfig):**
```python
emergency_expiry_hours: int = 8  # 자동 복귀까지의 시간
emergency_warning_hours: int = 4  # 경고 시작 시간
emergency_final_warning_hours: int = 6  # 최종 경고 시간
```

---

### 2.3 P2: 거버넌스 상태 API (0.5일)

**목표:**
```http
GET /api/self-healing/governance/status/
```

**응답 예시:**
```json
{
    "status": "success",
    "governance": {
        "current_mode": "STRICT",
        "mode_changed_at": "2025-12-24T10:00:00Z",
        "mode_changed_by": "operator_kim",
        "mode_expires_at": "2025-12-24T18:00:00Z",
        "time_remaining_hours": 3.5,
        "thresholds": {
            "operator_approve": 0.15,
            "admin_approve": 0.30
        },
        "emergency_active": true,
        "pending_admin_acknowledgement": true
    }
}
```

---

### 2.4 P3: 4-Eyes Principle 간소화 버전 (2일)

**Phase 2 (간소화):**
- Admin A가 요청 → "PendingApproval" 상태로 저장
- Admin B에게 알림 → 24시간 내 승인/거부
- 승인 없으면 자동 만료

**API:**
```http
POST /api/self-healing/governance/approval-requests/
GET  /api/self-healing/governance/approval-requests/
POST /api/self-healing/governance/approval-requests/{id}/approve/
POST /api/self-healing/governance/approval-requests/{id}/reject/
```

**우선순위:** 금융권 컴플라이언스 필요 시에만 구현

---

## 3. Config API 통합 분석

### 3.1 현재 RuntimeConfigManager 지원 설정 (13종)

| Config Type | API Endpoint | 상태 |
|-------------|--------------|------|
| `circuit_breaker` | `/config/circuit-breaker/` | ✅ 통합됨 |
| `dlq` | `/config/dlq/` | ✅ 통합됨 |
| `retry` | `/config/retry/` | ✅ 통합됨 |
| `sla` | `/config/sla/` | ✅ 통합됨 |
| `rate_limit` | `/config/rate-limit/` | ✅ 통합됨 |
| `security` | `/config/security/` | ✅ 통합됨 |
| `idempotency` | `/config/idempotency/` | ✅ 통합됨 |
| `notification` | `/config/notification/` | ✅ 통합됨 |
| `forensic` | `/config/forensic/` | ✅ 통합됨 |
| `logging` | `/config/logging/` | ✅ 통합됨 |
| `metrics` | `/config/metrics/` | ✅ 통합됨 |
| `error_budget` | `/config/error-budget/` | ✅ 통합됨 |
| `slo` | `/config/slo/` | ✅ 통합됨 |

### 3.2 별도 API로 존재하는 설정 (통합 검토 대상)

#### 3.2.1 Drift Threshold (`drift_threshold.py`) ⚠️ 통합 권장

**현재:**
```
GET/PUT /api/self-healing/config/drift-thresholds/
```

**분석:**
- 자체 StateBackend 사용 (`DRIFT_THRESHOLD_CONFIG_KEY`)
- ConfigHistory 연동 있음
- RuntimeConfigManager와 별개로 동작

**권장:** ✅ **RuntimeConfigManager에 통합**
- 이유: 다른 임계값 설정들과 일관성 유지
- 작업량: 0.5일
- 추가할 키: `STORAGE_KEYS["drift_threshold"]`

---

#### 3.2.2 Tier Configuration (`tiering.py`) ⚠️ 통합 권장

**현재:**
```
GET/PUT /api/self-healing/config/tiers/
GET/PUT /api/self-healing/config/tier-mappings/
GET/PUT /api/self-healing/config/tier-overrides/
```

**분석:**
- TierRegistry 자체 저장소 사용
- 복잡한 구조 (definitions, mappings, overrides)

**권장:** ✅ **RuntimeConfigManager에 통합**
- 이유: Config History, Apply Strategy 혜택
- 작업량: 1일
- 추가할 키: `STORAGE_KEYS["tiering"]`

---

#### 3.2.3 L2 Storage Config (`l2_storage_config.py`) ⚠️ 통합 권장

**현재:**
```
GET/PUT /api/self-healing/l2-storage/config/
```

**분석:**
- `get_l2_storage_runtime_config()` 별도 함수
- RuntimeConfigManager와 별개

**권장:** ✅ **RuntimeConfigManager에 통합**
- 이유: 중앙 집중화, Config History
- 작업량: 0.5일
- 추가할 키: `STORAGE_KEYS["l2_storage"]`

---

#### 3.2.4 Chaos Engineering Config (`chaos.py`) 🔶 부분 통합

**현재:**
```
GET/PATCH /api/self-healing/chaos/config/safety-guard/
GET/PATCH /api/self-healing/chaos/config/blast-radius/
GET/PATCH /api/self-healing/chaos/config/scheduler/
GET/PATCH /api/self-healing/chaos/config/reports/
GET/PATCH /api/self-healing/chaos/config/stop-conditions/
GET/PATCH /api/self-healing/chaos/config/ttl/
GET/PATCH /api/self-healing/chaos/config/dry-run/
```

**분석:**
- 7개의 개별 설정 API
- 각각 별도 서비스에서 관리 (SafetyGuard, BlastRadiusPolicy 등)

**권장:** 🔶 **일부만 통합**
- SafetyGuard, BlastRadiusPolicy → RuntimeConfigManager 통합
- Scheduler, TTL, DryRun → 운영 특성상 별도 유지
- 작업량: 1.5일

---

#### 3.2.5 Emergency Config (`emergency.py`) 🔶 부분 통합

**현재:**
```
GET/PUT /api/self-healing/emergency/config/
GET     /api/self-healing/emergency/levels/
```

**분석:**
- Emergency 레벨 정의, 임계값 등
- GovernanceConfig와 일부 중복 가능

**권장:** 🔶 **governance에 병합 검토**
- Emergency 레벨은 별도 유지 (복잡한 로직)
- 기본 임계값은 GovernanceConfig로 통합
- 작업량: 0.5일

---

#### 3.2.6 System Control (`system_control.py`) ❌ 통합 불필요

**현재:**
```
GET  /api/self-healing/system/status/
POST /api/self-healing/system/enable/
POST /api/self-healing/system/disable/
```

**분석:**
- 킬 스위치 기능 (Enable/Disable)
- 상태 저장이 아닌 액션 수행

**권장:** ❌ **별도 유지**
- 이유: 액션 API이지 설정 API가 아님
- Config와 성격이 다름

---

#### 3.2.7 Circuit Breaker Control (`circuit_breaker.py`) ❌ 통합 불필요

**현재:**
```
POST /api/self-healing/control/
POST /api/self-healing/allow/{service}/
POST /api/self-healing/block/{service}/
```

**분석:**
- CB 상태 제어 (Allow/Block/Reset)
- 액션 수행 API

**권장:** ❌ **별도 유지**
- 이유: 액션 API

---

### 3.3 통합 권장 요약

| 설정 | 현재 위치 | 통합 권장 | 작업량 | 우선순위 |
|------|----------|----------|--------|---------|
| Drift Threshold | 별도 | ✅ RuntimeConfigManager | 0.5일 | P2 |
| Tiering | 별도 | ✅ RuntimeConfigManager | 1일 | P2 |
| L2 Storage | 별도 | ✅ RuntimeConfigManager | 0.5일 | P3 |
| Chaos (일부) | 별도 | 🔶 일부만 | 1.5일 | P3 |
| Emergency (일부) | 별도 | 🔶 Governance 병합 | 0.5일 | P2 |
| **Governance (신규)** | 없음 | ✅ **신규 추가** | 0.5일 | **P1** |

---

## 4. 신규 추가: GovernanceConfig

### 4.1 데이터 구조

```python
@dataclass
class GovernanceConfig:
    """거버넌스 관련 설정."""
    
    # 임계값 기반 권한
    threshold_operator: float = 0.15  # Operator 승인 상한
    threshold_admin: float = 0.30     # Admin 승인 상한
    
    # 긴급 에스컬레이션
    emergency_expiry_hours: int = 8       # 자동 복귀 시간
    emergency_warning_hours: int = 4      # 경고 시작 시간
    emergency_final_warning_hours: int = 6  # 최종 경고 시간
    
    # 운영 모드
    default_mode: str = "NORMAL"
    
    # 알림 설정
    notify_on_emergency: bool = True
    notify_channels: List[str] = field(default_factory=lambda: ["slack", "email"])
```

### 4.2 RuntimeConfigManager 확장

```python
STORAGE_KEYS = {
    # ... 기존 13개 ...
    "governance": "runtime_config:governance",  # 신규
    "drift_threshold": "runtime_config:drift_threshold",  # 통합
    "tiering": "runtime_config:tiering",  # 통합
}

CONFIG_CLASSES = {
    # ... 기존 ...
    "governance": GovernanceConfig,
    "drift_threshold": DriftThresholdConfig,
    "tiering": TieringConfig,
}
```

---

## 5. 구현 로드맵

### Phase 1: 핵심 거버넌스 (이번 주)

| 순서 | 작업 | 예상 시간 | 담당 |
|------|------|----------|------|
| 1 | GovernanceConfig 데이터클래스 생성 | 2h | - |
| 2 | RuntimeConfigManager에 governance 추가 | 2h | - |
| 3 | ThresholdBasedPermission 런타임 설정 연동 | 2h | - |
| 4 | EmergencyModeTracker 생성 | 3h | - |
| 5 | 자동 복귀 Celery Beat 태스크 | 3h | - |
| 6 | 테스트 작성 | 2h | - |
| **합계** | | **14h (2일)** | |

### Phase 1: 핵심 거버넌스 ✅ 완료 (2025-12-24)

| 순서 | 작업 | 예상 시간 | 상태 |
|------|------|----------|------|
| 1 | GovernanceConfig 데이터클래스 생성 | 2h | ✅ |
| 2 | RuntimeConfigManager에 governance 추가 | 2h | ✅ |
| 3 | ThresholdBasedPermission 런타임 설정 연동 | 2h | ✅ |
| 4 | EmergencyModeTracker 생성 | 3h | ✅ |
| 5 | 자동 복귀 Celery Beat 태스크 | 3h | ✅ |
| 6 | 테스트 작성 | 2h | ✅ |
| **합계** | | **14h (2일)** | ✅ |

### Phase 2: Config 통합 ✅ 완료 (2025-12-24)

| 순서 | 작업 | 예상 시간 | 상태 |
|------|------|----------|------|
| 1 | DriftThresholdConfig 통합 | 4h | ✅ |
| 2 | GovernanceRBACStatusView API | 4h | ✅ |
| 3 | GovernanceConfigView API | 2h | ✅ |
| 4 | DriftThresholdConfigView 리팩토링 | 2h | ✅ |
| 5 | EmergencyModeTracker expires_at 개선 | 2h | ✅ |
| 6 | 테스트 작성 (20개) | 4h | ✅ |
| **합계** | | **18h (2.25일)** | ✅ |

**구현된 기능:**
- `DriftThresholdConfig` 데이터클래스 (core/config.py)
- RuntimeConfigManager에 drift_threshold 키 추가
- `GET /api/self-healing/governance/status/` - RBAC 상태 조회
- `GET/PUT /api/self-healing/config/governance/` - 거버넌스 설정
- EmergencyModeTracker에 expires_at, time_remaining_hours 추가

### Phase 3: 고급 기능 ✅ 완료 (2025-12-24)

| 순서 | 작업 | 예상 시간 | 상태 |
|------|------|----------|------|
| 1 | L2StorageConfig 데이터클래스 생성 | 2h | ✅ |
| 2 | ChaosConfig 데이터클래스 생성 | 2h | ✅ |
| 3 | RuntimeConfigManager에 l2_storage, chaos 키 추가 | 2h | ✅ |
| 4 | ApprovalRequest 데이터클래스 생성 | 2h | ✅ |
| 5 | 4-Eyes Approval Workflow API 구현 | 4h | ✅ |
| 6 | L2StorageConfigManagedView API 구현 | 2h | ✅ |
| 7 | 테스트 작성 (27개) | 4h | ✅ |
| **합계** | | **18h (2.25일)** | ✅ |

**구현된 기능:**

1. **L2 Storage Config 통합**
   - `L2StorageConfig` 데이터클래스 (core/config.py)
   - RuntimeConfigManager에 l2_storage 키 추가
   - `get/update/reset_l2_storage_config()` 메서드
   - `GET/PUT /api/self-healing/config/l2-storage/` API

2. **Chaos Config 통합**
   - `ChaosConfig` 데이터클래스 (core/config.py)
   - RuntimeConfigManager에 chaos 키 추가 (기존 메서드와 통합)

3. **4-Eyes Approval Workflow**
   - `ApprovalRequest` 데이터클래스 (core/config.py)
   - `create/approve/reject_request()` 메서드
   - `expire_old_requests()`, `get_pending_requests_for_user()` 메서드
   - `POST /api/self-healing/governance/approval-requests/` - 요청 생성
   - `GET /api/self-healing/governance/approval-requests/` - 요청 목록
   - `POST /api/self-healing/governance/approval-requests/{id}/approve/` - 승인
   - `POST /api/self-healing/governance/approval-requests/{id}/reject/` - 거부

---

## 6. 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](16_GOVERNANCE_IMPLEMENTATION_PART1.md) - RBAC, 환경변수 Audit
- [16_GOVERNANCE_IMPLEMENTATION_PART1A.md](16_GOVERNANCE_IMPLEMENTATION_PART1A.md) - Rate Limit, 티어링
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Config Versioning, Fail-Safe
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드

---

## 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2025-12-24 | 1.0 | 초기 작성 - 전체 구현 계획 정리 |
| 2025-12-24 | 1.1 | Phase 1 완료 - GovernanceConfig, EmergencyModeTracker, Celery Beat 태스크, 테스트 |
| 2025-12-24 | 1.2 | Phase 2 완료 - DriftThresholdConfig 통합, GovernanceRBACStatusView, GovernanceConfigView |
| 2025-12-24 | 1.3 | Phase 3 완료 - L2StorageConfig, ChaosConfig, 4-Eyes Approval Workflow |

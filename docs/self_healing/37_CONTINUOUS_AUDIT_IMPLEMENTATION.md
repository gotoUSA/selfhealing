# Continuous Audit 구현 (Big 4 스타일)

📅 **작성일**: 2025-12-29  
📅 **구현일**: 2025-12-29  
🎯 **목적**: 자율 복구 시스템의 지속적 감사 추적 및 규정 준수  
📋 **버전**: v1.1.0 (구현 완료)
✅ **상태**: **구현 완료**

---

## 📌 개요

### 배경

Big 4 회계법인의 핵심 질문:
> "이 자율 복구 로직이 사고를 치면 누가 책임지느냐?"

### 목표

**모든 자동 조정을 위변조 불가능하게 기록**
- DORA, PCI-DSS, SOC2 규정 준수
- IT 감사 대응 자동화
- 책임 추적 가능한 의사결정 로그

### 설계 철학: Raw Data 우선

> **보고서 포맷팅은 제공하지 않음**

각 조직은 자체적인 감사 보고서 형식이 있습니다. 따라서:
- ✅ 완전하고 정확한 **Raw Data** 기록
- ✅ 강력한 **쿼리/필터/익스포트** 기능
- ✅ **JSON Lines / CSV** 익스포트
- ❌ 특정 형식의 보고서 생성 (사용자 책임)

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Continuous Audit System                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────────┐                                           │
│   │ Event Source    │ 모든 Self-Healing 이벤트                  │
│   │ - Auto Tuning   │                                           │
│   │ - CB State      │                                           │
│   │ - DLQ Replay    │                                           │
│   │ - Config Change │                                           │
│   └────────┬────────┘                                           │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │ Audit Recorder  │───▶│ Integrity Check │                   │
│   │ (중앙 집중)     │    │ (해시 체인)     │                   │
│   └────────┬────────┘    └─────────────────┘                   │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │ Audit Storage   │    │ Compliance      │                   │
│   │ - File (JSONL)  │    │ Checker         │                   │
│   │ - S3 WORM       │    │ (DORA/PCI/SOC2) │                   │
│   │ - Loki/Grafana  │    └─────────────────┘                   │
│   └────────┬────────┘                                           │
│            │                                                     │
│            ▼                                                     │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │ Alert on        │    │ Raw Data        │                   │
│   │ Violation       │    │ Export (JSONL)  │                   │
│   └─────────────────┘    └─────────────────┘                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 기존 시스템 연동

### 현재 감사 로그 시스템

```
packages/selfhealing-python/src/selfhealing/
├── adapters/audit/
│   ├── file_adapter.py      # JSON Lines 파일 기록 ✅
│   ├── stdout_adapter.py    # 표준 출력 ✅
│   └── null_adapter.py      # 비활성화용 ✅
│
└── interfaces/
    └── audit_adapter.py     # AuditEntry, AuditAction 정의 ✅
```

### 기존 AuditAction 확장

```python
# 기존 액션 (audit_adapter.py)
class AuditAction(str, Enum):
    # Circuit Breaker
    CB_FORCE_OPEN = "cb_force_open"
    CB_FORCE_CLOSE = "cb_force_close"
    CB_AUTO_OPEN = "cb_auto_open"
    CB_AUTO_CLOSE = "cb_auto_close"
    
    # DLQ
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY_START = "dlq_replay_start"
    
    # 기존 Governance
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"
    
    # 🆕 자율 조정 관련 (추가 필요)
    AUTO_TUNING_ADJUSTMENT = "auto_tuning_adjustment"
    AUTO_TUNING_ENABLED = "auto_tuning_enabled"
    AUTO_TUNING_DISABLED = "auto_tuning_disabled"
    AUTO_TUNING_BOUNDS_CHANGED = "auto_tuning_bounds_changed"
    AUTO_TUNING_REJECTED = "auto_tuning_rejected"  # 안전 한계 초과
    
    # 🆕 Drift 관련
    DNA_DRIFT_DETECTED = "dna_drift_detected"
    DNA_DRIFT_RESOLVED = "dna_drift_resolved"
    
    # 🆕 Compliance 관련
    COMPLIANCE_CHECK = "compliance_check"
    COMPLIANCE_VIOLATION = "compliance_violation"
```

---

## 📝 Audit Entry 구조

### 자율 조정 감사 로그

```json
{
    "id": "audit-20251229-001",
    "timestamp": "2025-12-29T10:30:00.123Z",
    "action": "auto_tuning_adjustment",
    "resource_type": "runtime_config",
    "resource_id": "timeout_ms",
    
    "actor": {
        "type": "system",
        "id": "runtime_feedback_loop",
        "name": "Self-Healing Auto Tuner"
    },
    
    "details": {
        "adjustment_type": "automatic",
        "parameter": "timeout_ms",
        "before": {
            "value": 5000,
            "set_at": "2025-12-28T00:00:00Z",
            "set_by": "deployment"
        },
        "after": {
            "value": 6000,
            "confidence": 0.85
        },
        "reason": "P99 레이턴시가 타임아웃의 80% 이상",
        "metrics_snapshot": {
            "p99_latency_ms": 4200,
            "error_rate": 0.02,
            "sample_count": 150,
            "observation_window": "5m"
        },
        "safety_check": {
            "within_bounds": true,
            "bounds": {"min": 100, "max": 30000},
            "change_ratio": 0.20
        }
    },
    
    "environment": "production",
    "service": "payment-service",
    "version": "2.8.0",
    
    "integrity": {
        "hash": "sha256:abc123...",
        "previous_hash": "sha256:xyz789...",
        "sequence": 12345
    }
}
```

### Drift 감지 감사 로그

```json
{
    "id": "audit-20251229-002",
    "timestamp": "2025-12-29T11:00:00.456Z",
    "action": "dna_drift_detected",
    "resource_type": "stage_dna",
    "resource_id": "stage14_dlq_api_test",
    
    "actor": {
        "type": "system",
        "id": "dna_drift_detector"
    },
    
    "details": {
        "drift_type": "configuration_mismatch",
        "declared": {
            "timeout_ms": 5000,
            "retry_count": 3
        },
        "actual": {
            "timeout_ms": 6000,
            "retry_count": 3
        },
        "drifted_fields": ["timeout_ms"],
        "severity": "medium",
        "auto_remediation": false,
        "recommendation": "DNA 선언 업데이트 또는 런타임 설정 롤백 필요"
    }
}
```

### Compliance 검사 감사 로그

```json
{
    "id": "audit-20251229-003",
    "timestamp": "2025-12-29T12:00:00.789Z",
    "action": "compliance_check",
    "resource_type": "self_healing_system",
    "resource_id": "global",
    
    "actor": {
        "type": "system",
        "id": "compliance_checker"
    },
    
    "details": {
        "standards_checked": ["DORA", "PCI-DSS", "SOC2"],
        "results": {
            "DORA": {
                "status": "compliant",
                "checks": {
                    "recovery_time_objective": "pass",
                    "audit_trail_complete": "pass",
                    "incident_response_documented": "pass"
                }
            },
            "PCI-DSS": {
                "status": "compliant",
                "checks": {
                    "access_control": "pass",
                    "audit_logging": "pass",
                    "change_management": "pass"
                }
            },
            "SOC2": {
                "status": "warning",
                "checks": {
                    "availability": "pass",
                    "confidentiality": "pass",
                    "processing_integrity": "warning"
                },
                "warnings": [
                    "자율 조정 승인 워크플로우 없음 (권장)"
                ]
            }
        },
        "overall_status": "compliant_with_warnings",
        "next_check_scheduled": "2025-12-29T13:00:00Z"
    }
}
```

---

## 🔧 Continuous Audit Recorder (구현 완료 ✅)

### 실제 구현 위치

```
packages/selfhealing-python/src/selfhealing/audit/
├── config.py              # AuditConfig (환경변수 기반)
├── continuous_audit.py    # ContinuousAuditRecorder
├── continuous_audit_api.py # REST API 엔드포인트
└── integrity.py           # HashChainManager, HashChainVerifier
```

### ContinuousAuditRecorder 사용법

```python
from selfhealing.audit import ContinuousAuditRecorder, AuditConfig
from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter

# 설정 로드 (환경변수에서 자동 로드)
config = AuditConfig.get_default()

# 레코더 생성
adapter = FileAuditLogAdapter("logs/continuous_audit.jsonl", rotate_daily=True)
recorder = ContinuousAuditRecorder(
    audit_adapter=adapter,
    config=config,
)

# 자율 조정 기록
audit_id = recorder.record_auto_tuning(
    parameter="timeout_ms",
    old_value=5000,
    new_value=6000,
    reason="P99 레이턴시가 타임아웃의 80% 이상",
    confidence=0.85,
    metrics_snapshot={"p99_latency_ms": 4200, "error_rate": 0.02},
    safety_check={"within_bounds": True, "bounds": {"min": 100, "max": 30000}},
)

# DNA Drift 기록
recorder.record_drift_detected(
    resource_id="stage14_dlq_api_test",
    declared={"timeout_ms": 5000},
    actual={"timeout_ms": 6000},
    drifted_fields=["timeout_ms"],
    severity="medium",
)

# Compliance 검사 기록
recorder.record_compliance_check(
    standards_checked=["DORA", "PCI-DSS", "SOC2"],
    results={"DORA": {"status": "compliant"}},
    overall_status="compliant",
)
```

### Raw Data 조회 및 익스포트

```python
# 자율 조정 이력 조회
entries = recorder.query_auto_tuning_history(
    parameter="timeout_ms",
    start_time=datetime(2025, 12, 1),
    limit=100,
)

# JSON Lines 익스포트 (스트리밍)
for line in recorder.export_jsonl():
    print(line)

# CSV 호환 형식 (평탄화된 데이터)
csv_data = recorder.export_csv_compatible()

# 무결성 검증
result = recorder.verify_integrity()
print(result["verified"])  # True/False
```

### 기존 코드 (계획)

```python
# packages/selfhealing-python/src/selfhealing/services/audit/continuous_audit.py

"""
Continuous Audit Recorder - Big 4 스타일 지속적 감사

모든 자동화된 결정을 위변조 불가능하게 기록
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction, AuditLogAdapter
from selfhealing.services.error_budget_gate.alert_manager import GateAlertManager

logger = logging.getLogger(__name__)


class ContinuousAuditRecorder:
    """
    지속적 감사 기록기
    
    특징:
    - 해시 체인으로 위변조 방지
    - 다중 스토리지 백엔드 지원
    - 규정 위반 시 즉시 알림
    - 감사 보고서 자동 생성
    """
    
    def __init__(
        self,
        audit_adapter: AuditLogAdapter,
        alert_manager: Optional[GateAlertManager] = None,
        compliance_checker = None,
    ):
        self.audit_adapter = audit_adapter
        self.alert_manager = alert_manager
        self.compliance_checker = compliance_checker
        
        self._sequence = 0
        self._previous_hash = "genesis"
        self._lock = threading.RLock()
    
    def record_auto_tuning(
        self,
        parameter: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        confidence: float,
        metrics_snapshot: Dict[str, Any],
        safety_check: Dict[str, Any],
    ):
        """자율 조정 기록"""
        entry = AuditEntry(
            action=AuditAction.AUTO_TUNING_ADJUSTMENT,
            resource_type="runtime_config",
            resource_id=parameter,
            actor_type="system",
            actor_id="runtime_feedback_loop",
            details={
                "adjustment_type": "automatic",
                "parameter": parameter,
                "before": {"value": old_value},
                "after": {"value": new_value, "confidence": confidence},
                "reason": reason,
                "metrics_snapshot": metrics_snapshot,
                "safety_check": safety_check,
            },
        )
        
        self._record_with_integrity(entry)
        
        # 알림 발송
        if self.alert_manager:
            self.alert_manager.send_auto_tuning_alert(
                parameter=parameter,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
            )
    
    def record_drift_detected(
        self,
        resource_id: str,
        declared: Dict[str, Any],
        actual: Dict[str, Any],
        drifted_fields: List[str],
        severity: str,
    ):
        """Drift 감지 기록"""
        entry = AuditEntry(
            action=AuditAction.DNA_DRIFT_DETECTED,
            resource_type="stage_dna",
            resource_id=resource_id,
            actor_type="system",
            actor_id="dna_drift_detector",
            details={
                "drift_type": "configuration_mismatch",
                "declared": declared,
                "actual": actual,
                "drifted_fields": drifted_fields,
                "severity": severity,
            },
        )
        
        self._record_with_integrity(entry)
        
        # 심각도에 따라 알림
        if severity in ("high", "critical") and self.alert_manager:
            self.alert_manager.send_drift_alert(resource_id, drifted_fields, severity)
    
    def record_compliance_check(
        self,
        results: Dict[str, Any],
        overall_status: str,
    ):
        """Compliance 검사 결과 기록"""
        entry = AuditEntry(
            action=AuditAction.COMPLIANCE_CHECK,
            resource_type="self_healing_system",
            resource_id="global",
            actor_type="system",
            actor_id="compliance_checker",
            details={
                "results": results,
                "overall_status": overall_status,
            },
        )
        
        self._record_with_integrity(entry)
        
        # 위반 시 알림
        if overall_status == "non_compliant" and self.alert_manager:
            self.alert_manager.send_compliance_violation_alert(results)
    
    def _record_with_integrity(self, entry: AuditEntry):
        """해시 체인과 함께 기록"""
        with self._lock:
            self._sequence += 1
            
            # 해시 계산
            entry_json = entry.to_json()
            current_hash = hashlib.sha256(
                f"{self._previous_hash}:{entry_json}".encode()
            ).hexdigest()
            
            # 무결성 정보 추가
            entry.details["integrity"] = {
                "sequence": self._sequence,
                "previous_hash": self._previous_hash[:16] + "...",
                "current_hash": current_hash[:16] + "...",
            }
            
            # 기록
            self.audit_adapter.log(entry)
            
            # 상태 업데이트
            self._previous_hash = current_hash
            
            logger.debug(f"[ContinuousAudit] Recorded: {entry.action} (seq={self._sequence})")
    
    def generate_audit_report(
        self,
        start_date: datetime,
        end_date: datetime,
        format: str = "markdown"
    ) -> str:
        """
        감사 보고서 생성
        
        Big 4 스타일의 형식화된 보고서
        """
        # 구현은 audit_adapter에서 로그 조회 후 포맷팅
        pass
```

---

## 📡 알림 채널

### 지원 채널

| 채널 | 용도 | 구현 상태 |
|------|------|----------|
| Slack | 실시간 알림 | 🔜 추가 예정 |
| PagerDuty | 긴급 알림 | 🔜 추가 예정 |
| Email | 일일 리포트 | 🔜 추가 예정 |
| Webhook | 커스텀 통합 | 🔜 추가 예정 |
| 로그 | 항상 기록 | ✅ 구현됨 |

### 알림 우선순위

| 이벤트 | 채널 | 우선순위 |
|--------|------|----------|
| 자율 조정 발생 | Slack, 로그 | 정보 (Info) |
| 안전 한계 초과 거부 | Slack, 로그 | 경고 (Warning) |
| Drift 감지 (High) | Slack, PagerDuty | 긴급 (Critical) |
| Compliance 위반 | PagerDuty, Email | 긴급 (Critical) |
| 시스템 비활성화 | PagerDuty | 긴급 (Critical) |

---

## 🔗 API 엔드포인트 (구현 완료 ✅)

### Raw Data 조회 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/audit/logs/` | 감사 로그 조회 (필터 지원) |
| GET | `/api/self-healing/audit/logs/{id}/` | 특정 로그 상세 |
| GET | `/api/self-healing/audit/auto-tuning/` | 자율 조정 이력 |
| GET | `/api/self-healing/audit/drift/` | DNA Drift 이력 |
| GET | `/api/self-healing/audit/compliance/` | Compliance 검사 이력 |

### 무결성 검증 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/audit/integrity/verify/` | 해시 체인 무결성 검증 |
| GET | `/api/self-healing/audit/integrity/state/` | 현재 체인 상태 |

### 익스포트 API (Raw Data)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/audit/export/jsonl/` | JSON Lines 스트리밍 익스포트 |
| GET | `/api/self-healing/audit/export/csv/` | CSV 형식 익스포트 |

### 설정 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/audit/config/` | 현재 감사 설정 조회 |

> **Note**: 보고서 포맷팅 API는 의도적으로 제외됨. 
> 각 조직에서 익스포트된 Raw Data를 자체 형식으로 가공해야 함.

---
## ⚠️ 하드코딩 문제 대책 (Configuration Strategy)

### 문제 인식

감사 로그 시스템에 하드코딩되면 안 되는 값들:

| 항목 | 하드코딩 위험 | 해결 방법 |
|------|----------------|------------|
| 해시 체인 시드 | 노출 시 보안 위험 | 환경변수/시크릿 |
| 보존 기간 | 규정마다 다름 | 설정 파일 |
| 알림 수신자 | 조직마다 다름 | 설정 파일 |

### 설정 계층 구조

```
우선순위 (높음 → 낮음):

1. 환경변수 (AUDIT_HASH_SEED, AUDIT_RETENTION_DAYS)
2. DNA 선언 (서비스별 설정)
3. 설정 파일 (settings.py, config.yaml)
4. 코드 기본값 (최후 수단)
```

### 구현 예제

```python
# packages/selfhealing-python/src/selfhealing/services/audit/config.py

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditConfig:
    """감사 로그 설정 - 환경변수 우선"""
    
    # 해시 체인 시드 (환경변수 필수)
    hash_seed: str = os.environ.get("AUDIT_HASH_SEED", "")
    
    # 보존 기간 (규정별 다름)
    retention_days: int = int(os.environ.get("AUDIT_RETENTION_DAYS", "365"))
    
    # 스토리지 백엔드
    storage_backend: str = os.environ.get("AUDIT_STORAGE", "file")  # file, s3, loki
    
    # S3 WORM 설정 (선택)
    s3_bucket: Optional[str] = os.environ.get("AUDIT_S3_BUCKET")
    s3_worm_enabled: bool = os.environ.get("AUDIT_S3_WORM", "false").lower() == "true"
    
    def __post_init__(self):
        if not self.hash_seed:
            raise ValueError(
                "AUDIT_HASH_SEED 환경변수가 설정되지 않았습니다. "
                "보안을 위해 반드시 설정하세요."
            )
    
    @classmethod
    def from_dna(cls, dna_config: dict) -> "AuditConfig":
        """DNA 선언에서 설정 로드 (환경변수가 우선)"""
        return cls(
            hash_seed=os.environ.get("AUDIT_HASH_SEED", dna_config.get("hash_seed", "")),
            retention_days=int(os.environ.get(
                "AUDIT_RETENTION_DAYS", 
                dna_config.get("retention_days", 365)
            )),
            storage_backend=os.environ.get(
                "AUDIT_STORAGE", 
                dna_config.get("storage", "file")
            ),
        )
```

### 규정별 보존 기간 기본값

| 규정 | 최소 보존 기간 | 권장값 |
|------|----------------|--------|
| DORA | 5년 | 7년 |
| PCI-DSS | 1년 | 3년 |
| SOC2 | 1년 | 3년 |
| GDPR | 목적 달성 시까지 | 서비스별 판단 |

---
## 📊 Compliance 규정 매핑

### DORA (Digital Operational Resilience Act)

| 요구사항 | Self-Healing 대응 |
|----------|-------------------|
| ICT 리스크 관리 | 자동 장애 감지 및 복구 |
| 사고 보고 | 감사 로그 + 알림 |
| 디지털 운영 복원력 테스트 | Chaos Engineering |
| 제3자 ICT 리스크 | Circuit Breaker |

### PCI-DSS

| 요구사항 | Self-Healing 대응 |
|----------|-------------------|
| 10.1 감사 추적 | Audit Log (JSONL/WORM) |
| 10.2 이벤트 로깅 | 모든 자동화 결정 기록 |
| 10.5 로그 보호 | 해시 체인 무결성 |
| 12.10 사고 대응 | 자동 알림 + 복구 |

### SOC2

| 기준 | Self-Healing 대응 |
|------|-------------------|
| 가용성 | 자동 복구, Circuit Breaker |
| 처리 무결성 | 멱등성, DLQ |
| 기밀성 | 로그 마스킹 |
| 개인정보보호 | 감사 로그 접근 제어 |

---

## 📚 관련 문서

- [36_RUNTIME_FEEDBACK_IMPLEMENTATION.md](./36_RUNTIME_FEEDBACK_IMPLEMENTATION.md) - 런타임 피드백
- [38_AUTO_TUNING_API.md](./38_AUTO_TUNING_API.md) - 자율 튜닝 API
- [12_ERROR_BUDGET.md](./12_ERROR_BUDGET.md) - Error Budget Gate

---

## ✅ 구현 현황 (2025-12-29)

### 구현 완료 항목

| 구성 요소 | 파일 | 상태 |
|----------|------|------|
| AuditConfig | `audit/config.py` | ✅ 완료 |
| ContinuousAuditRecorder | `audit/continuous_audit.py` | ✅ 완료 |
| REST API | `audit/continuous_audit_api.py` | ✅ 완료 |
| AuditAction 확장 | `interfaces/audit_adapter.py` | ✅ 완료 |
| 테스트 | `tests/unit/test_continuous_audit.py` | ✅ 28개 통과 |

### 새로 추가된 AuditAction

```python
# Auto Tuning
AUTO_TUNING_ADJUSTMENT = "auto_tuning_adjustment"
AUTO_TUNING_ENABLED = "auto_tuning_enabled"
AUTO_TUNING_DISABLED = "auto_tuning_disabled"
AUTO_TUNING_BOUNDS_CHANGED = "auto_tuning_bounds_changed"
AUTO_TUNING_REJECTED = "auto_tuning_rejected"
AUTO_TUNING_ROLLBACK = "auto_tuning_rollback"

# DNA Drift
DNA_DRIFT_DETECTED = "dna_drift_detected"
DNA_DRIFT_RESOLVED = "dna_drift_resolved"

# Compliance
COMPLIANCE_CHECK = "compliance_check"
COMPLIANCE_VIOLATION = "compliance_violation"
```

### 환경 변수

```bash
# 필수 (프로덕션)
AUDIT_HASH_SEED=your-secret-seed

# 선택적
AUDIT_RETENTION_DAYS=365
AUDIT_STORAGE=file          # file, s3, loki
AUDIT_S3_BUCKET=my-bucket
AUDIT_S3_WORM=true
AUDIT_ALERT_CHANNELS=slack,pagerduty
AUDIT_LOG_PATH=logs/continuous_audit.jsonl
```

### 테스트 결과

```
$ pytest tests/unit/test_continuous_audit.py -v
============================= 28 passed in 0.63s ==============================
```

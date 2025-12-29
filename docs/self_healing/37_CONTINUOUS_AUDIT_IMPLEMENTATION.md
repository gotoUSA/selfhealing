# Continuous Audit 구현 계획 (Big 4 스타일)

📅 **작성일**: 2025-12-29  
🎯 **목적**: 자율 복구 시스템의 지속적 감사 추적 및 규정 준수  
📋 **버전**: v1.0.0

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
│   │ Alert on        │    │ Report          │                   │
│   │ Violation       │    │ Generator       │                   │
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

## 🔧 Continuous Audit Recorder

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

## 🔗 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/self-healing/audit/logs/` | 감사 로그 조회 |
| GET | `/api/self-healing/audit/logs/{id}/` | 특정 로그 상세 |
| GET | `/api/self-healing/audit/report/` | 감사 보고서 생성 |
| GET | `/api/self-healing/audit/integrity/verify/` | 무결성 검증 |
| GET | `/api/self-healing/compliance/status/` | 규정 준수 상태 |
| GET | `/api/self-healing/compliance/check/` | 규정 검사 실행 |

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

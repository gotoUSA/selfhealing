# X-Test-Mode Audit 통합

**문서 번호:** 123  
**작성일:** 2026-01-27  
**상태:** 구현 완료 ✅  
**선행 문서:** 116-122, 20_AUDIT_UNIFICATION_PLAN.md

---

## 1. 목적

X-Test-Mode에서 수행된 모든 작업을 기존 Audit 시스템(WAL 기반)에 통합하여:
1. 세션 단위 추적 가능
2. 테스트 증적 보존
3. 다중 저장소 분산 문제 해결

---

## 2. 현재 상태 분석

### 2.1 X-Test 데이터 저장 현황

| 데이터 | 저장소 | 문제점 |
|--------|--------|--------|
| 시나리오 결과 | In-Memory (`_scenario_results`) | 서버 재시작 시 소멸 |
| Idempotency 키 | Redis (24h TTL) | TTL 후 추적 불가 |
| DLQ 항목 | PostgreSQL | 영구 저장, 별도 쿼리 필요 |
| 로그 | Python Logger | 비구조화, 세션 추적 어려움 |

### 2.2 기존 Audit 시스템

**위치:** `packages/selfhealing-python/src/selfhealing/services/audit/`

**인프라:**
- WAL (Write-Ahead Log) 기반 누락 0 보장
- Background Sync Worker → 중앙 저장소 동기화
- Reconciler → 누락 감지 및 재전송

**기존 함수 패턴:** (`chaos_audit.py` 참조)
```python
def log_chaos_experiment_audit(
    experiment_id: str,
    experiment_type: str,
    ...
) -> Optional[int]:
    """WAL에 chaos 실험 기록."""
    details = {...}
    return _write_to_wal(
        event_type="CHAOS_EXPERIMENT",
        source="ChaosMonkey",
        details=details,
        ...
    )
```

---

## 3. 설계

### 3.1 X-Test Audit 함수 추가

**위치:** `selfhealing/services/audit/xtest_audit.py` (신규)

```python
"""
X-Test-Mode Audit Helpers

X-Test-Mode 작업을 WAL 기반 Audit 시스템에 기록.

Usage:
    from selfhealing.services.audit.xtest_audit import (
        log_xtest_operation_audit,
        log_xtest_scenario_audit,
    )
"""

from selfhealing.services.audit.base import _write_to_wal

def log_xtest_operation_audit(
    session_id: str,
    action: str,
    component: str,
    details: dict,
    result: str,
    user: str = "anonymous",
    trace_id: Optional[str] = None,
) -> Optional[int]:
    """
    X-Test 단일 작업을 Audit 로그에 기록.
    
    Args:
        session_id: X-Test 세션 식별자 (헤더 또는 자동 생성)
        action: 수행 작업 (inject_dlq, run_scenario, reset, etc.)
        component: 대상 컴포넌트 (dlq, cb, idempotency, etc.)
        details: 작업 상세 정보
        result: 결과 (success, failed, dry_run)
        user: 수행자
        trace_id: 분산 추적 ID
    
    Returns:
        WAL 시퀀스 번호
    """
    return _write_to_wal(
        event_type="XTEST_OPERATION",
        source=f"XTest.{component}",
        details={
            "session_id": session_id,
            "action": action,
            "component": component,
            "result": result,
            "user": user,
            **details,
        },
        success=(result == "success"),
        domain="xtest",
        target_id=session_id,
        trace_id=trace_id,
    )


def log_xtest_scenario_audit(
    scenario_id: str,
    scenario_name: str,
    service_name: str,
    status: str,
    steps_count: int,
    errors: list,
    duration_ms: float,
    user: str = "anonymous",
) -> Optional[int]:
    """
    X-Test 시나리오 실행 결과를 Audit 로그에 기록.
    """
    return _write_to_wal(
        event_type="XTEST_SCENARIO",
        source="XTest.Integration",
        details={
            "scenario_id": scenario_id,
            "scenario_name": scenario_name,
            "service_name": service_name,
            "status": status,
            "steps_count": steps_count,
            "errors": errors,
            "duration_ms": duration_ms,
            "user": user,
        },
        success=(status == "completed"),
        domain="xtest",
        target_id=scenario_id,
    )
```

### 3.2 X-Test Base에 통합

**수정 대상:** `selfhealing/api/django/views/xtest/base.py`

```python
# 기존 코드에 추가
from selfhealing.services.audit.xtest_audit import log_xtest_operation_audit

class XTestModeMixin:
    ...
    
    def log_xtest_audit(
        self,
        request,
        action: str,
        component: str,
        result: dict,
    ) -> Optional[int]:
        """X-Test 작업을 Audit 로그에 기록."""
        session_id = request.headers.get(
            "X-Test-Session", 
            str(uuid.uuid4())[:8]
        )
        user = str(request.user) if request.user.is_authenticated else "anonymous"
        
        return log_xtest_operation_audit(
            session_id=session_id,
            action=action,
            component=component,
            details=result,
            result=result.get("status", "unknown"),
            user=user,
        )
```

### 3.3 각 View에 Audit 호출 추가

**패턴:** 모든 X-Test View의 Response 반환 직전에 호출

```python
class InjectDLQEntryView(XTestModeMixin, APIView):
    def post(self, request):
        ...
        response_data = {...}
        
        # Audit 기록 추가
        self.log_xtest_audit(
            request,
            action="inject_dlq",
            component="dlq",
            result=response_data,
        )
        
        return Response(response_data, ...)
```

---

## 4. 세션 추적 헤더

### 4.1 X-Test-Session 헤더 도입

| 헤더 | 용도 | 필수 |
|------|------|------|
| `X-Test-Mode` | chaos-monkey 인증 | ✅ 필수 |
| `X-Test-Session` | 세션 추적 (테스트 그룹핑) | ❌ 선택 (없으면 자동 생성) |

**사용 예시:**
```bash
curl -X POST /api/self-healing/xtest/dlq/inject/ \
  -H "X-Test-Mode: chaos-monkey" \
  -H "X-Test-Session: my-test-session-001" \
  -d '{"domain": "external_service"}'
```

### 4.2 세션 기반 조회 API (신규)

**엔드포인트:** `GET /api/self-healing/xtest/audit/session/{session_id}/`

```python
class XTestAuditSessionView(XTestModeMixin, APIView):
    """특정 세션의 모든 X-Test 작업 조회."""
    
    def get(self, request, session_id: str):
        # WAL에서 session_id로 필터링
        from selfhealing.audit.wal import get_wal_instance
        wal = get_wal_instance()
        
        entries = wal.query(
            filter_fn=lambda e: (
                e.get("domain") == "xtest" and
                e.get("details", {}).get("session_id") == session_id
            ),
            limit=100,
        )
        
        return Response({
            "session_id": session_id,
            "entries": entries,
            "count": len(entries),
        })
```

---

## 5. 구현 범위

### 5.1 Phase 1: Core (문서 123)

| 항목 | 파일 | 변경 내용 |
|------|------|-----------|
| xtest_audit.py | `services/audit/xtest_audit.py` | 신규 생성 |
| __init__.py | `services/audit/__init__.py` | export 추가 |
| audit_helpers.py | `services/audit_helpers.py` | re-export 추가 |
| base.py | `views/xtest/base.py` | `log_xtest_audit()` 추가 |

### 5.2 Phase 2: View Integration ✅ (구현 완료)

모든 X-Test View에 Audit 호출 추가 완료:
- `dlq.py` - 4개 View ✅ (inject, status, force_status, reset)
- `replay.py` - 4개 View ✅ (single, batch, trigger_cb_close, status)
- `retry.py` - 4개 View ✅ (backoff_preview, simulate, rate_limit_status, config)
- `rate_limit.py` - 5개 View ✅ (status, client, history, config, reset)
- `idempotency.py` - 5개 View ✅ (generate_key, check_duplicate, status, register, clear)
- `integration.py` - 4개 View ✅ (run_scenario, scenario_status, snapshot, reset)
- `circuit_breaker.py` - 5개 View ✅ (inject, reset, trigger_recovery, try_transition, switch_auto)

### 5.3 Phase 3: Session API (문서 125)

- 세션 조회 API
- 세션 통계 API
- 세션 Export API

---

## 6. 테스트 계획

### 6.1 단위 테스트

```python
class TestXTestAudit:
    def test_log_xtest_operation_audit(self, mock_wal):
        """X-Test 작업이 WAL에 기록되는지 확인."""
        seq = log_xtest_operation_audit(
            session_id="test-session",
            action="inject_dlq",
            component="dlq",
            details={"count": 5},
            result="success",
        )
        
        assert seq is not None
        mock_wal.write.assert_called_once()
        
    def test_log_xtest_scenario_audit(self, mock_wal):
        """시나리오 결과가 WAL에 기록되는지 확인."""
        ...
```

### 6.2 통합 테스트

```python
class TestXTestAuditIntegration:
    def test_session_tracking_across_operations(self):
        """동일 세션의 여러 작업이 추적되는지 확인."""
        session_id = "integration-test-001"
        
        # 1. DLQ 주입
        client.post("/xtest/dlq/inject/", headers={
            "X-Test-Session": session_id,
        })
        
        # 2. 시나리오 실행
        client.post("/xtest/integration/run-scenario/", headers={
            "X-Test-Session": session_id,
        })
        
        # 3. 세션 조회
        response = client.get(f"/xtest/audit/session/{session_id}/")
        assert response.data["count"] == 2
```

---

## 7. 예상 효과

| 이전 | 이후 |
|------|------|
| 4곳 분산 저장 | WAL 통합 + 기존 저장소 유지 |
| 세션 추적 불가 | `X-Test-Session` 헤더로 그룹핑 |
| 서버 재시작 시 In-Memory 소멸 | WAL 영구 보존 |
| 다중 서버 불일치 | Background Sync로 중앙화 |

---

## 8. 참고 문서

- [20_AUDIT_UNIFICATION_PLAN.md](../20_AUDIT_UNIFICATION_PLAN.md) - WAL 기반 Audit 아키텍처
- [116-122](./116_XTEST_MODE_BASE.md) - X-Test-Mode 구현 문서
- [chaos_audit.py](../../../packages/selfhealing-python/src/selfhealing/services/audit/chaos_audit.py) - 기존 Chaos Audit 패턴

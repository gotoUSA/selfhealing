# Auto Tuning API 구현 계획

📅 **작성일**: 2025-12-29  
🎯 **목적**: 자율 조정 On/Off 및 제어 API  
📋 **버전**: v1.0.0

---

## 📌 개요

### 필요성

자율 조정 시스템에는 **사람이 개입할 수 있는 제어 수단**이 필수입니다:

| 상황 | 필요한 제어 |
|------|-----------|
| 장애 발생 | 자율 조정 일시 중지, 수동 모드 전환 |
| 배포 중 | 자율 조정 OFF (안정화 대기) |
| 특정 모듈 문제 | 해당 모듈만 자율 조정 OFF |
| 규제 감사 | 모든 조정 기록 조회 |
| 안전 한계 | 자율 조정의 범위 제한 설정 |

### 현재 vs 필요

```
현재 API (전체 Kill Switch만 있음)
──────────────────────────────────────
POST /api/self-healing/system/enable/   ← 전체 시스템 ON
POST /api/self-healing/system/disable/  ← 전체 시스템 OFF

필요한 API (세분화된 자율 조정 제어)
──────────────────────────────────────
POST /api/self-healing/auto-tuning/enable/
POST /api/self-healing/auto-tuning/disable/
POST /api/self-healing/auto-tuning/{module}/enable/
PUT  /api/self-healing/auto-tuning/bounds/
GET  /api/self-healing/auto-tuning/history/
```

---

## 🔗 API 엔드포인트 상세

### 1. 전역 자율 조정 제어

#### GET /api/self-healing/auto-tuning/status/

자율 조정 시스템 상태 조회

**Response:**
```json
{
    "enabled": true,
    "mode": "automatic",  // automatic, manual, dry_run
    "last_adjustment": {
        "timestamp": "2025-12-29T10:30:00Z",
        "parameter": "timeout_ms",
        "old_value": 5000,
        "new_value": 6000
    },
    "statistics": {
        "total_adjustments_24h": 5,
        "adjustments_by_type": {
            "timeout_ms": 2,
            "retry_count": 1,
            "circuit_breaker_threshold": 2
        }
    },
    "modules": {
        "circuit_breaker": {"enabled": true, "last_adjustment": "2025-12-29T09:00:00Z"},
        "retry": {"enabled": true, "last_adjustment": "2025-12-29T08:30:00Z"},
        "jitter": {"enabled": false, "disabled_reason": "manual_override"}
    }
}
```

#### POST /api/self-healing/auto-tuning/enable/

자율 조정 활성화

**Request:**
```json
{
    "reason": "배포 완료 후 재활성화",
    "mode": "automatic"  // automatic, dry_run
}
```

**Response:**
```json
{
    "status": "enabled",
    "enabled_at": "2025-12-29T11:00:00Z",
    "enabled_by": "admin@example.com",
    "mode": "automatic",
    "audit_id": "audit-20251229-004"
}
```

#### POST /api/self-healing/auto-tuning/disable/

자율 조정 비활성화

**Request:**
```json
{
    "reason": "긴급 장애 대응 - 수동 모드 전환",
    "duration_minutes": 60,  // 선택: 자동 재활성화 시간
    "notify": true
}
```

**Response:**
```json
{
    "status": "disabled",
    "disabled_at": "2025-12-29T11:00:00Z",
    "disabled_by": "admin@example.com",
    "reason": "긴급 장애 대응 - 수동 모드 전환",
    "auto_enable_at": "2025-12-29T12:00:00Z",
    "audit_id": "audit-20251229-005"
}
```

---

### 2. 모듈별 자율 조정 제어

#### POST /api/self-healing/auto-tuning/{module}/enable/

특정 모듈 자율 조정 활성화

**Path Parameters:**
- `module`: circuit_breaker, retry, jitter, rate_limit, timeout

**Request:**
```json
{
    "reason": "circuit_breaker 자동 조정 재개"
}
```

#### POST /api/self-healing/auto-tuning/{module}/disable/

특정 모듈 자율 조정 비활성화

**Request:**
```json
{
    "reason": "retry 설정 수동 관리 필요",
    "duration_minutes": 120
}
```

---

### 3. 안전 한계 관리

#### GET /api/self-healing/auto-tuning/bounds/

현재 안전 한계 조회

**Response:**
```json
{
    "bounds": {
        "timeout_ms": {
            "min": 100,
            "max": 30000,
            "max_change_per_cycle": 0.3,
            "current_value": 5000
        },
        "retry_count": {
            "min": 0,
            "max": 10,
            "max_change_per_cycle": 0.5,
            "current_value": 3
        },
        "circuit_breaker_threshold": {
            "min": 0.1,
            "max": 0.9,
            "max_change_per_cycle": 0.2,
            "current_value": 0.5
        },
        "jitter_range": {
            "min": 0.01,
            "max": 1.0,
            "max_change_per_cycle": 0.5,
            "current_value": 0.1
        },
        "rate_limit_rps": {
            "min": 10,
            "max": 10000,
            "max_change_per_cycle": 0.2,
            "current_value": 1000
        }
    },
    "last_updated": "2025-12-28T00:00:00Z",
    "updated_by": "deployment"
}
```

#### PUT /api/self-healing/auto-tuning/bounds/

안전 한계 수정

**Request:**
```json
{
    "parameter": "timeout_ms",
    "bounds": {
        "min": 200,
        "max": 20000,
        "max_change_per_cycle": 0.2
    },
    "reason": "타임아웃 상한 조정 - 외부 서비스 응답 시간 증가"
}
```

**Response:**
```json
{
    "status": "updated",
    "parameter": "timeout_ms",
    "previous": {
        "min": 100,
        "max": 30000,
        "max_change_per_cycle": 0.3
    },
    "current": {
        "min": 200,
        "max": 20000,
        "max_change_per_cycle": 0.2
    },
    "updated_at": "2025-12-29T11:30:00Z",
    "updated_by": "admin@example.com",
    "audit_id": "audit-20251229-006"
}
```

---

### 4. 조정 이력 조회

#### GET /api/self-healing/auto-tuning/history/

조정 이력 조회

**Query Parameters:**
- `start_date`: 시작일 (ISO 8601)
- `end_date`: 종료일 (ISO 8601)
- `parameter`: 특정 파라미터 필터
- `page`: 페이지 번호
- `page_size`: 페이지 크기 (기본 20)

**Response:**
```json
{
    "total": 45,
    "page": 1,
    "page_size": 20,
    "items": [
        {
            "id": "adj-20251229-001",
            "timestamp": "2025-12-29T10:30:00Z",
            "parameter": "timeout_ms",
            "old_value": 5000,
            "new_value": 6000,
            "reason": "P99 레이턴시가 타임아웃의 80% 이상",
            "confidence": 0.85,
            "metrics": {
                "p99_latency_ms": 4200,
                "error_rate": 0.02
            },
            "result": "applied",
            "audit_id": "audit-20251229-001"
        },
        {
            "id": "adj-20251229-002",
            "timestamp": "2025-12-29T09:15:00Z",
            "parameter": "circuit_breaker_threshold",
            "old_value": 0.5,
            "new_value": 0.55,
            "reason": "에러율이 CB 임계값에 근접",
            "confidence": 0.72,
            "result": "applied"
        }
    ]
}
```

#### GET /api/self-healing/auto-tuning/history/{id}/

특정 조정 상세 조회

---

### 5. 수동 조정 (Override)

#### POST /api/self-healing/auto-tuning/override/

수동으로 파라미터 조정 (자율 조정 우회)

**Request:**
```json
{
    "parameter": "timeout_ms",
    "value": 8000,
    "reason": "외부 서비스 점검 중 - 타임아웃 일시 증가",
    "duration_minutes": 30,  // 선택: 자동 롤백 시간
    "disable_auto_tuning": true  // 해당 파라미터 자동 조정 일시 중지
}
```

**Response:**
```json
{
    "status": "applied",
    "parameter": "timeout_ms",
    "previous_value": 6000,
    "new_value": 8000,
    "override_type": "manual",
    "auto_rollback_at": "2025-12-29T12:00:00Z",
    "auto_tuning_disabled_until": "2025-12-29T12:00:00Z",
    "audit_id": "audit-20251229-007"
}
```

#### DELETE /api/self-healing/auto-tuning/override/{parameter}/

수동 조정 해제 (자동 조정 복원)

---

### 6. 메트릭 조회

#### GET /api/self-healing/auto-tuning/metrics/

현재 수집 중인 메트릭 조회

**Response:**
```json
{
    "collected_at": "2025-12-29T11:00:00Z",
    "metrics": {
        "p99_latency_ms": 4200,
        "p95_latency_ms": 3100,
        "p50_latency_ms": 1500,
        "error_rate": 0.02,
        "retry_exhausted_rate": 0.05,
        "retry_collision_rate": 0.03,
        "circuit_breaker_open_count": 0,
        "sample_count": 1500
    },
    "thresholds": {
        "timeout_ms": {
            "current": 6000,
            "will_adjust": false
        },
        "retry_count": {
            "current": 3,
            "will_adjust": false
        },
        "circuit_breaker_threshold": {
            "current": 0.55,
            "will_adjust": false
        }
    }
}
```

---

## 🔧 View 구현

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/auto_tuning.py

"""
Auto Tuning API Views

자율 조정 제어 API 엔드포인트
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser

from selfhealing.services.auto_tuning import AutoTuningService
from selfhealing.api.django.permissions import IsSelfHealingAdmin


class AutoTuningStatusView(APIView):
    """GET /api/self-healing/auto-tuning/status/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request):
        service = AutoTuningService()
        return Response(service.get_status())


class AutoTuningEnableView(APIView):
    """POST /api/self-healing/auto-tuning/enable/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request):
        service = AutoTuningService()
        reason = request.data.get("reason", "")
        mode = request.data.get("mode", "automatic")
        
        result = service.enable(
            reason=reason,
            mode=mode,
            enabled_by=request.user.email,
        )
        
        return Response(result, status=status.HTTP_200_OK)


class AutoTuningDisableView(APIView):
    """POST /api/self-healing/auto-tuning/disable/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request):
        service = AutoTuningService()
        reason = request.data.get("reason", "")
        duration = request.data.get("duration_minutes")
        
        result = service.disable(
            reason=reason,
            duration_minutes=duration,
            disabled_by=request.user.email,
        )
        
        return Response(result, status=status.HTTP_200_OK)


class AutoTuningModuleView(APIView):
    """
    POST /api/self-healing/auto-tuning/{module}/enable/
    POST /api/self-healing/auto-tuning/{module}/disable/
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request, module, action):
        service = AutoTuningService()
        reason = request.data.get("reason", "")
        duration = request.data.get("duration_minutes")
        
        if action == "enable":
            result = service.enable_module(module, reason, request.user.email)
        else:
            result = service.disable_module(module, reason, duration, request.user.email)
        
        return Response(result, status=status.HTTP_200_OK)


class AutoTuningBoundsView(APIView):
    """
    GET  /api/self-healing/auto-tuning/bounds/
    PUT  /api/self-healing/auto-tuning/bounds/
    """
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request):
        service = AutoTuningService()
        return Response(service.get_bounds())
    
    def put(self, request):
        service = AutoTuningService()
        result = service.update_bounds(
            parameter=request.data.get("parameter"),
            bounds=request.data.get("bounds"),
            reason=request.data.get("reason", ""),
            updated_by=request.user.email,
        )
        return Response(result, status=status.HTTP_200_OK)


class AutoTuningHistoryView(APIView):
    """GET /api/self-healing/auto-tuning/history/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request):
        service = AutoTuningService()
        
        history = service.get_history(
            start_date=request.query_params.get("start_date"),
            end_date=request.query_params.get("end_date"),
            parameter=request.query_params.get("parameter"),
            page=int(request.query_params.get("page", 1)),
            page_size=int(request.query_params.get("page_size", 20)),
        )
        
        return Response(history)


class AutoTuningOverrideView(APIView):
    """POST /api/self-healing/auto-tuning/override/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request):
        service = AutoTuningService()
        
        result = service.manual_override(
            parameter=request.data.get("parameter"),
            value=request.data.get("value"),
            reason=request.data.get("reason", ""),
            duration_minutes=request.data.get("duration_minutes"),
            disable_auto_tuning=request.data.get("disable_auto_tuning", True),
            overridden_by=request.user.email,
        )
        
        return Response(result, status=status.HTTP_200_OK)
    
    def delete(self, request, parameter):
        service = AutoTuningService()
        result = service.clear_override(parameter, request.user.email)
        return Response(result, status=status.HTTP_200_OK)


class AutoTuningMetricsView(APIView):
    """GET /api/self-healing/auto-tuning/metrics/"""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def get(self, request):
        service = AutoTuningService()
        return Response(service.get_current_metrics())
```

---

## 🛡️ 권한 및 보안

### 권한 레벨

| 권한 | 엔드포인트 | 설명 |
|------|-----------|------|
| Viewer | GET status, history, bounds, metrics | 조회만 가능 |
| Operator | POST override | 수동 조정 가능 |
| Admin | POST enable/disable, PUT bounds | 전체 제어 |

### 감사 로그

모든 API 호출은 감사 로그에 기록됩니다:

```json
{
    "action": "auto_tuning_disabled",
    "actor_type": "user",
    "actor_id": "admin@example.com",
    "details": {
        "reason": "긴급 장애 대응",
        "duration_minutes": 60,
        "ip_address": "192.168.1.100"
    }
}
```

---

## 📁 URL 패턴

```python
# packages/selfhealing-python/src/selfhealing/api/django/urls.py (추가)

from selfhealing.api.django.views.auto_tuning import (
    AutoTuningStatusView,
    AutoTuningEnableView,
    AutoTuningDisableView,
    AutoTuningModuleView,
    AutoTuningBoundsView,
    AutoTuningHistoryView,
    AutoTuningOverrideView,
    AutoTuningMetricsView,
)

urlpatterns += [
    # 자율 조정 제어
    path("auto-tuning/status/", AutoTuningStatusView.as_view(), name="auto-tuning-status"),
    path("auto-tuning/enable/", AutoTuningEnableView.as_view(), name="auto-tuning-enable"),
    path("auto-tuning/disable/", AutoTuningDisableView.as_view(), name="auto-tuning-disable"),
    
    # 모듈별 제어
    path("auto-tuning/<str:module>/enable/", AutoTuningModuleView.as_view(), {"action": "enable"}, name="auto-tuning-module-enable"),
    path("auto-tuning/<str:module>/disable/", AutoTuningModuleView.as_view(), {"action": "disable"}, name="auto-tuning-module-disable"),
    
    # 안전 한계
    path("auto-tuning/bounds/", AutoTuningBoundsView.as_view(), name="auto-tuning-bounds"),
    
    # 이력
    path("auto-tuning/history/", AutoTuningHistoryView.as_view(), name="auto-tuning-history"),
    path("auto-tuning/history/<str:id>/", AutoTuningHistoryView.as_view(), name="auto-tuning-history-detail"),
    
    # 수동 조정
    path("auto-tuning/override/", AutoTuningOverrideView.as_view(), name="auto-tuning-override"),
    path("auto-tuning/override/<str:parameter>/", AutoTuningOverrideView.as_view(), name="auto-tuning-override-clear"),
    
    # 메트릭
    path("auto-tuning/metrics/", AutoTuningMetricsView.as_view(), name="auto-tuning-metrics"),
]
```

---

## 📚 관련 문서

- [36_RUNTIME_FEEDBACK_IMPLEMENTATION.md](./36_RUNTIME_FEEDBACK_IMPLEMENTATION.md) - 런타임 피드백
- [37_CONTINUOUS_AUDIT_IMPLEMENTATION.md](./37_CONTINUOUS_AUDIT_IMPLEMENTATION.md) - 지속적 감사
- [5_CONTROL_API/](./5_CONTROL_API/) - 기존 Control API 문서

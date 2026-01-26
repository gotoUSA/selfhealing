# 113. 기존 Views 예외 처리 패턴 인벤토리

## 1. 분석 범위

- 경로: `selfhealing/api/django/views/`
- 분석 대상: 모든 예외 처리 블록 (100+ 매치)

---

## 2. 패턴별 분류

### 패턴 A: 최소 정보

**구조**: `{"error": str(e)}`

**사용 파일**:
| 파일 | 라인 | 예외 유형 |
|------|------|-----------|
| finops.py | 87, 136 | Exception |
| rollback.py | 90, 151 | Exception |
| learning.py | 145, 225 | Exception |
| compliance_dna.py | 85, 117 | Exception |
| blast_radius.py | 90, 143, 193 | Exception |

**특징**:
- 가장 간단한 형태
- error_type, status 필드 없음
- 주로 400 상태 코드 사용

---

### 패턴 B: status 필드 포함

**구조**: `{"status": "error", "error": str(e)}`

**사용 파일**:
| 파일 | 라인 범위 | 예외 유형 |
|------|-----------|-----------|
| canary.py | 208-425 | ValueError, Exception |
| health.py | 247-396 | ValueError, Exception |
| tiering.py | 199-740 | ValueError, Exception |
| l2_storage_config.py | 56-140 | ValueError, Exception |
| dashboard.py | 49 | Exception |
| drift_threshold.py | 79-205 | ValueError, Exception |
| dlq.py | 79-365 | ValueError, Exception |

**특징**:
- status 필드로 성공/실패 구분
- 일부는 errors 필드 포함 (serializer 에러)

---

### 패턴 C: success + message 필드

**구조**: `{"success": False, "error": str(e), "message": "..."}`

**사용 파일**:
| 파일 | 라인 범위 | 예외 유형 | 언어 |
|------|-----------|-----------|------|
| cascade.py | 125-598 | Exception | 영어 |
| emergency.py | 139-467 | ValueError, KeyError | 한국어 |
| system_control.py | 160-243 | Exception | 영어 |

**특징**:
- 불리언 success 필드
- 사용자 친화적 message 제공
- 다국어 혼용 (한/영)

---

### 패턴 D: detail 필드 사용

**구조**: `{"error": "...", "detail": str(e)}`

**사용 파일**:
| 파일 | 라인 | 컨텍스트 |
|------|------|----------|
| health.py | 205-210 | 메트릭 수집 실패 |

**특징**:
- 고정 error 메시지 + 기술적 detail
- 단 1개 파일에서만 사용

---

### 패턴 E: 도메인별 커스텀

**구조**: `{"reconciliation_result": "failed", "error": str(e)}`

**사용 파일**:
| 파일 | 라인 | 컨텍스트 |
|------|------|----------|
| governance/control_views.py | 90-95 | Reconcile 실패 |

**특징**:
- 도메인 특화 응답 구조
- 일반화하기 어려움

---

### 패턴 F: error_type 포함

**구조**: `{"status": "error", "error": "code", "message": str(e)}`

**사용 파일**:
| 파일 | 라인 범위 |
|------|-----------|
| xtest/observability.py | 116-435 |

**특징**:
- error 필드에 에러 코드 (문자열)
- message 필드에 상세 정보
- 가장 구조화된 형태

---

### 패턴 G: ConfigLockError 특수 처리

**구조**:
```json
{
    "status": "error",
    "error": str(e),
    "error_type": "config_locked",
    "current_owner": e.current_owner
}
```

**사용 파일**:
| 파일 | 라인 |
|------|------|
| canary.py | 255-264 |

**특징**:
- 특정 예외에 대한 풍부한 정보
- error_type으로 클라이언트 구분 가능
- 409 CONFLICT 상태 코드

---

## 3. HTTP 상태 코드 사용 현황

| 상태 코드 | 사용 상황 | 파일 예시 |
|-----------|-----------|-----------|
| 400 | 일반 예외, ValueError | finops, rollback, learning |
| 404 | 리소스 없음 | cascade, canary, dlq |
| 409 | ConfigLockError | canary |
| 500 | 시스템 예외 | cascade, health, governance |
| 503 | 서비스 불가 | finops (service not available) |

### 불일치 사례

| 예외 유형 | 파일 A | 파일 B |
|-----------|--------|--------|
| Exception | finops (400) | cascade (500) |
| ValueError | l2_storage_config (400) | health (500) |

---

## 4. 로깅 패턴 분석

### logger.exception 사용

| 파일 | 사용 여부 |
|------|-----------|
| governance/*.py | ✅ 모두 사용 |
| cascade.py | ❌ |
| emergency.py | ❌ |
| finops.py | ❌ |

### 로깅 메시지 형식

```python
# governance/ 스타일
logger.exception(f"[Governance] Approval request list failed: {e}")

# 대부분의 views
# 로깅 없음, 직접 Response 반환
```

---

## 5. Serializer 에러 처리

### 현재 패턴

```python
if not serializer.is_valid():
    return Response(
        {"status": "error", "errors": serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )
```

**사용 파일**: tiering.py, l2_storage_config.py, drift_threshold.py

### 표준화 고려

DRF 기본 ValidationError로 통합 가능:
```python
serializer.is_valid(raise_exception=True)
# → DRF가 ValidationError 발생 → 핸들러가 처리
```

---

## 6. 마이그레이션 우선순위

### 높음 (가장 많이 사용)

| 패턴 | 파일 수 | 우선순위 |
|------|---------|----------|
| 패턴 B | 8+ | ⭐⭐⭐ |
| 패턴 A | 5+ | ⭐⭐⭐ |

### 중간 (표준화 필요)

| 패턴 | 파일 수 | 우선순위 |
|------|---------|----------|
| 패턴 C | 3 | ⭐⭐ |
| 패턴 G | 1 | ⭐⭐ |

### 낮음 (특수 케이스)

| 패턴 | 파일 수 | 우선순위 |
|------|---------|----------|
| 패턴 E | 1 | ⭐ |
| 패턴 D | 1 | ⭐ |

---

## 7. 권장 표준 포맷

모든 패턴을 아우르는 표준 포맷:

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_FIELD_REQUIRED",
        "message": "필수 필드가 누락되었습니다.",
        "detail": "The 'amount' field is required.",
        "type": "validation_error",
        "field": "amount",
        "retryable": false
    },
    "meta": {
        "request_id": "uuid",
        "timestamp": "ISO8601",
        "path": "/api/...",
        "method": "POST"
    }
}
```

### 기존 패턴과의 매핑

| 기존 필드 | 표준 필드 |
|-----------|-----------|
| error (문자열) | error.detail |
| status: "error" | success: false |
| message | error.message |
| error_type | error.type |
| errors (serializer) | error.detail (JSON) |
| current_owner | error.context.current_owner |

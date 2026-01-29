# 157. OpenTelemetry SDK 도입 계획

> **문서 목적**: 현재 자체 구현된 트레이싱 시스템을 OTEL SDK로 전환하는 상세 계획을 제시합니다.

---

## 1. 현재 트레이싱 구현 분석

### 1.1 자체 구현 현황

현재 시스템은 OpenTelemetry 의존성 없이 자체적으로 트레이싱을 구현하고 있습니다.

**Trace ID 생성 및 관리:**

| 기능 | 현재 구현 | 코드 위치 |
|------|----------|----------|
| ID 생성 | `generate_trace_id()` - "req-{cluster_prefix}-{uuid8}" 형식 | `selfhealing/audit/trace.py` |
| 컨텍스트 저장 | `contextvars.ContextVar` + `threading.local` 하이브리드 | `selfhealing/audit/trace.py` |
| ID 설정/조회 | `set_trace_id()`, `get_trace_id()`, `clear_trace_id()` | `selfhealing/audit/trace.py` |
| 요청 추출 | `extract_trace_id_from_request()` - 5개 헤더 지원 | `selfhealing/audit/trace.py` |

**HTTP 클라이언트 헤더 전파:**

| 기능 | 현재 구현 | 코드 위치 |
|------|----------|----------|
| Chaos 플래그 전파 | `SelfHealingHttpClient` 클래스 | `selfhealing/services/http_client.py` |
| 헤더 자동 주입 | `_get_headers()` 메서드 | `selfhealing/services/http_client.py` |
| OTel 의존성 | **명시적으로 없음** (docstring 명시) | `selfhealing/services/http_client.py` |

**Celery Task 연동:**

| 기능 | 현재 구현 | 코드 위치 |
|------|----------|----------|
| Celery trace_id | `generate_celery_trace_id()` - "CELERY_{task_id}" 형식 | `selfhealing/audit/trace.py` |
| 컨텍스트 전파 | `set_celery_context()`, `get_celery_context()` | `selfhealing/audit/trace.py` |

### 1.2 W3C 호환 구조 (이미 설계됨)

`ExternalTraceContext` 데이터클래스가 W3C/OpenTelemetry 호환 구조로 이미 설계되어 있습니다.

**지원 필드:**

| 필드 | 설명 | W3C 표준 |
|------|------|----------|
| `trace_id` | 32 hex characters | traceparent trace-id |
| `span_id` | 16 hex characters | traceparent parent-id |
| `trace_flags` | 예: "01" = sampled | traceparent trace-flags |
| `baggage` | key-value 쌍 | W3C Baggage |
| `aws_xray_trace_id` | AWS X-Ray 호환 | X-Amzn-Trace-Id |
| `request_id` | 요청 ID | X-Request-ID |
| `correlation_id` | 상관관계 ID | X-Correlation-ID |

---

## 2. OTEL SDK 도입 범위

### 2.1 필요 패키지

| 패키지 | 용도 | 비고 |
|--------|------|------|
| `opentelemetry-api` | 기본 API | 필수 |
| `opentelemetry-sdk` | SDK 구현 | 필수 |
| `opentelemetry-exporter-otlp` | OTLP 프로토콜 전송 | 필수 |
| `opentelemetry-instrumentation-django` | Django 자동 계측 | 권장 |
| `opentelemetry-instrumentation-celery` | Celery 자동 계측 | 권장 |
| `opentelemetry-instrumentation-redis` | Redis 자동 계측 | 권장 |
| `opentelemetry-instrumentation-psycopg2` | PostgreSQL 자동 계측 | 권장 |
| `opentelemetry-instrumentation-requests` | HTTP 클라이언트 자동 계측 | 권장 |

### 2.2 영향받는 코드 영역

| 영역 | 현재 코드 | 변경 필요 |
|------|----------|----------|
| Django 미들웨어 | `trace_id_middleware` | OTEL 미들웨어로 대체 |
| HTTP 클라이언트 | `SelfHealingHttpClient` | OTEL 자동 계측 활용 |
| Celery Task | `generate_celery_trace_id()` | OTEL Celery 계측 활용 |
| Audit Logger | `AuditLogger` | trace_id 추출 방식 변경 |
| Circuit Breaker | `TracingConfig`, `TriggeringRequestInfo` | OTEL Span 연동 |
| Cascade Event | `ExternalTraceContext` | OTEL 컨텍스트 연동 |

---

## 3. 구현 단계

### 3.1 Phase 1: SDK 설치 및 기본 설정

**목표**: OTEL SDK 설치, TracerProvider 초기화, 기존 코드와 공존

**작업 항목:**

| # | 작업 | 영향받는 파일 | 검증 방법 |
|---|------|-------------|----------|
| 1.1 | requirements.txt에 OTEL 패키지 추가 | `requirements.txt` | pip install 성공 |
| 1.2 | TracerProvider 초기화 모듈 생성 | `selfhealing/observability/__init__.py` (신규) | import 에러 없음 |
| 1.3 | 환경변수 기반 설정 | `selfhealing/settings/observability.py` (신규) | 설정 로드 확인 |
| 1.4 | Django settings.py 연동 | `myproject/settings.py` | 서버 시작 확인 |

**환경변수 설계:**

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `OTEL_ENABLED` | false | OTEL 활성화 여부 |
| `OTEL_SERVICE_NAME` | selfhealing | 서비스 이름 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | http://localhost:4317 | Collector 엔드포인트 |
| `OTEL_TRACES_SAMPLER` | parentbased_traceidratio | 샘플링 전략 |
| `OTEL_TRACES_SAMPLER_ARG` | 0.01 | 기본 샘플링 비율 (1%) |
| `OTEL_RESOURCE_ATTRIBUTES` | - | 추가 리소스 속성 |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | 5000 | Exporter 타임아웃 (ms) |

### 3.1.1 상태 기반 가변 샘플링 (Adaptive Sampling)

**근거**: `EmergencyLevel` enum이 0~3 레벨 정의 (`services/emergency_mode/enums.py`)

평시에는 1%만 샘플링하되, Emergency Level 2 이상 또는 SLA 위반(Throttle 발생) 시 100%로 즉시 전환:

| 상태 | 샘플링 비율 | 근거 |
|------|------------|------|
| `EmergencyLevel.NORMAL` | 1% | 저장소 비용 최적화 |
| `EmergencyLevel.LEVEL_1` | 10% | 경미한 장애 모니터링 |
| `EmergencyLevel.LEVEL_2` 이상 | 100% | 전체 추적 필요 |
| SLA_CRITICAL (500ms 초과) | 100% | RTT 임계치 위반 시 |
| 429 응답 발생 | 100% | Throttle 발생 시 |

**구현 위치**: `selfhealing/observability/sampler.py` (신규)

샘플러는 현재 `EmergencyLevel`과 `sla_critical_ms` 설정을 참조하여 동적으로 비율 결정

### 3.2 Phase 2: Django 자동 계측

**목표**: Django 요청/응답 자동 Span 생성

**작업 항목:**

| # | 작업 | 영향받는 파일 | 검증 방법 |
|---|------|-------------|----------|
| 2.1 | Django instrumentation 활성화 | `myproject/settings.py` | 요청 시 Span 생성 확인 |
| 2.2 | 기존 `trace_id_middleware` 호환 레이어 | `selfhealing/audit/trace.py` | 기존 테스트 통과 |
| 2.3 | `extract_trace_id_from_request()` 수정 | `selfhealing/audit/trace.py` | OTEL trace_id 추출 |

**호환성 전략:**

기존 `trace_id_middleware`와 OTEL 미들웨어가 공존할 수 있도록:
1. OTEL이 활성화되면 OTEL에서 trace_id 추출
2. OTEL이 비활성화되면 기존 로직 사용
3. 기존 `get_trace_id()` 함수는 동일한 인터페이스 유지

### 3.3 Phase 3: HTTP 클라이언트 계측

**목표**: 외부 HTTP 호출 자동 Span 생성 및 컨텍스트 전파

**작업 항목:**

| # | 작업 | 영향받는 파일 | 검증 방법 |
|---|------|-------------|----------|
| 3.1 | requests instrumentation 활성화 | OTEL 초기화 코드 | 외부 호출 Span 확인 |
| 3.2 | `SelfHealingHttpClient` 수정 | `selfhealing/services/http_client.py` | traceparent 헤더 자동 주입 |
| 3.3 | Chaos 플래그 전파 유지 | `selfhealing/services/http_client.py` | X-Self-Healing-Synthetic 헤더 확인 |

**현재 코드 고려사항:**

`SelfHealingHttpClient`는 "OpenTelemetry 의존 없이" 설계되었으므로:
- OTEL 활성화 시: OTEL 자동 계측 활용
- OTEL 비활성화 시: 기존 수동 헤더 주입 유지

### 3.3.1 중복 기록 방지 (Suppress Instrumentation)

**근거**: `event_buffer.py`의 `has_event_from_source()` 메서드로 중복 확인 패턴 존재

`opentelemetry-instrumentation-requests` 사용 시 기존 Audit 시스템과 중복 기록 방지:

| 상황 | 처리 방법 |
|------|----------|
| OTEL + Audit 동시 기록 | `suppress_instrumentation` 컨텍스트 활용 |
| 내부 헬스체크 호출 | Span 생성 제외 |
| Collector 전송 호출 | 자체 Span 생성 안함 |

**구현 패턴:**
```python
from opentelemetry.context import suppress_instrumentation

# 특정 호출에서 자동 계측 억제
with suppress_instrumentation():
    # 이 블록 내 HTTP 호출은 Span 생성 안함
    internal_health_check()
```

**Audit 연동 주의사항:**
- 111번 문서의 Audit 시스템과 OTEL이 동일 요청을 중복 기록하지 않도록 주의
- `source="ExceptionHandler"` 등 소스별 중복 확인 로직 활용

### 3.4 Phase 4: Celery 계측

**목표**: Celery Task 자동 Span 생성 및 컨텍스트 전파

**작업 항목:**

| # | 작업 | 영향받는 파일 | 검증 방법 |
|---|------|-------------|----------|
| 4.1 | Celery instrumentation 활성화 | Celery 초기화 코드 | Task 실행 Span 확인 |
| 4.2 | 기존 `generate_celery_trace_id()` 호환 | `selfhealing/audit/trace.py` | 기존 테스트 통과 |
| 4.3 | Celery 컨텍스트 전파 검증 | 테스트 코드 | 부모-자식 Span 연결 |

**현재 코드 고려사항:**

기존 "CELERY_{task_id}" 형식 trace_id와 OTEL Span의 관계:
- OTEL trace_id: 32자 hex (표준)
- 기존 trace_id: "CELERY_{task_id}" (커스텀)
- 호환 레이어에서 양쪽 형식 모두 지원

### 3.4.1 contextvars 기반 컨텍스트 보장

**근거**: `trace.py`에 `contextvars.ContextVar` + `threading.local` 하이브리드 이미 구현

Celery/비동기 경계에서 컨텍스트 유실 방지:

| 환경 | 컨텍스트 저장 | 코드 위치 |
|------|-------------|----------|
| Async/Await | `contextvars.ContextVar` | `trace.py#L19-21` |
| Thread Pool | `threading.local` 폴백 | `trace.py#L24` |
| Celery Task | `set_celery_task_context()` | `trace.py#L303-327` |

OTEL SDK는 내부적으로 `contextvars`를 사용하므로 기존 패턴과 자연스럽게 통합

### 3.5 Phase 5: Circuit Breaker 및 Audit 연동

**목표**: CB 상태 변화 및 Audit 이벤트에 OTEL Span 정보 포함

**작업 항목:**

| # | 작업 | 영향받는 파일 | 검증 방법 |
|---|------|-------------|----------|
| 5.1 | `TracingConfig` OTEL 연동 | `selfhealing/services/circuit_breaker/tracing.py` | CB 상태 변화 시 Span 확인 |
| 5.2 | `TriggeringRequestInfo` 확장 | `selfhealing/services/circuit_breaker/tracing.py` | OTEL trace_id 포함 |
| 5.3 | `ExternalTraceContext` 연동 | `selfhealing/audit/cascade_event.py` | Cascade Event에 Span 정보 |
| 5.4 | `AuditLogger` trace_id 연동 | `selfhealing/audit/logger.py` | Audit 로그에 trace_id |

**현재 코드 고려사항:**

`TracingConfig`에 이미 `create_spans` 플래그가 있음:
- 현재: `create_spans=True`이지만 실제 OTEL Span 생성 코드 없음
- 변경: OTEL SDK를 사용하여 실제 Span 생성

### 3.5.1 CausationChain의 OTEL Span 승격

**근거**: 76번 문서의 `CausationChain` 및 `ExternalTraceContext` 구조

CascadeEvent 발생 시 OTEL trace_id와 span_id를 감사 로그에 포함:

| 필드 | 소스 | 저장 위치 |
|------|------|----------|
| `trace_id` | OTEL Span context | `ExternalTraceContext.trace_id` |
| `span_id` | OTEL Span context | `ExternalTraceContext.span_id` |
| `causation_chain` | 이벤트 ID 체인 | `CascadeEventArchive.causation_chain` |

**연동 방식:**
- Span Link: causation_chain의 각 이벤트를 OTEL Span Link로 연결
- Span Attribute: `cascade.event_id`, `cascade.trigger_type` 등 속성 추가

### 3.5.2 Trace ID 형식 정책 (Display-only Truncation)

**근거**: `trace.py`에서 W3C traceparent 파싱 시 앞 8자만 추출 (`req-{parts[1][:8]}`)

| 용도 | ID 형식 | 예시 |
|------|---------|------|
| 내부 저장 (Loki/Tempo) | 전체 W3C ID (32자 hex) | `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6` |
| 로그 기록 | 전체 W3C ID (32자 hex) | 글로벌 통합 시 충돌 방지 |
| UI 표시/대시보드 | `req-*` 형식 (8자) | `req-a1b2c3d4` |
| Audit 로그 | 전체 + 표시용 병기 | `trace_id` 필드에 전체, `trace_id_short`에 8자 |

**주의**: $16^8 \approx 42억$ 조합으로 충돌 확률은 낮지만, 글로벌 로그 통합 시 위험할 수 있으므로 내부 저장은 항상 전체 ID 사용

---

## 4. 구현 순서 체크리스트

### 4.1 Phase 1: SDK 설치 및 기본 설정

- [x] **1.1** requirements.txt에 OTEL 패키지 추가
  - opentelemetry-api
  - opentelemetry-sdk
  - opentelemetry-exporter-otlp
- [x] **1.2** `selfhealing/observability/` 디렉토리 생성
- [x] **1.3** `selfhealing/observability/__init__.py` 작성
  - TracerProvider 초기화
  - OTLP Exporter 설정
- [x] **1.4** `selfhealing/settings/observability.py` 작성
  - Pydantic Settings 클래스
  - 환경변수 매핑
- [x] **1.5** Django settings.py에 OTEL 초기화 호출 추가
- [x] **1.6** docker-compose.yml에 OTEL 환경변수 추가
- [x] **1.7** 기본 Span 생성 테스트 (49개 단위 테스트 통과)

### 4.2 Phase 2: Django 자동 계측

- [x] **2.1** requirements.txt에 django instrumentation 추가
  - opentelemetry-instrumentation-django
- [x] **2.2** Django 미들웨어 자동 계측 활성화
- [x] **2.3** `trace.py`의 `get_trace_id()` 수정
  - OTEL 활성화 시 OTEL trace_id 반환
  - OTEL 비활성화 시 기존 로직 유지
- [x] **2.4** `extract_trace_id_from_request()` 수정
  - OTEL Span에서 trace_id 추출 우선
- [x] **2.5** 기존 `trace_id_middleware` 동작 테스트 (13개 테스트 통과)
- [x] **2.6** X-Request-ID 응답 헤더 검증

### 4.3 Phase 3: HTTP 클라이언트 계측

- [x] **3.1** requirements.txt에 requests instrumentation 추가
  - opentelemetry-instrumentation-requests
- [x] **3.2** requests 자동 계측 활성화
- [x] **3.3** `SelfHealingHttpClient` 수정
  - OTEL 활성화 시 수동 헤더 주입 생략
  - Chaos 플래그 전파는 유지
- [x] **3.4** 외부 호출 Span 생성 검증
- [x] **3.5** traceparent 헤더 자동 주입 검증

### 4.4 Phase 4: Celery 계측

- [x] **4.1** requirements.txt에 celery instrumentation 추가
  - opentelemetry-instrumentation-celery
- [x] **4.2** Celery 자동 계측 활성화
- [x] **4.3** `generate_celery_trace_id()` 호환 레이어 추가
- [x] **4.4** Task 실행 Span 생성 검증
- [x] **4.5** 부모-자식 Span 연결 검증

### 4.5 Phase 5: Circuit Breaker 및 Audit 연동

- [x] **5.1** `TracingConfig` 수정
  - OTEL Span 생성 로직 추가
- [x] **5.2** `TriggeringRequestInfo` 확장
  - OTEL trace_id/span_id 자동 채움
- [x] **5.3** `ExternalTraceContext` OTEL 연동
  - 현재 Span에서 컨텍스트 추출
- [x] **5.4** `AuditLogger` 수정
  - OTEL trace_id 자동 포함
- [x] **5.5** CB 상태 변화 Span 검증
- [x] **5.6** Audit 로그에 trace_id 포함 검증

---

## 5. 호환성 전략

### 5.1 기능 플래그 기반 전환

| 플래그 | 값 | 동작 |
|--------|---|------|
| `OTEL_ENABLED=false` | 기본값 | 기존 자체 트레이싱 사용 |
| `OTEL_ENABLED=true` | 활성화 | OTEL SDK 사용 |

### 5.2 점진적 롤아웃

1. **개발 환경**: OTEL_ENABLED=true로 테스트
2. **스테이징**: 카나리 배포로 검증
3. **프로덕션**: 점진적 활성화

### 5.3 롤백 계획

OTEL 도입 후 문제 발생 시:
1. `OTEL_ENABLED=false`로 환경변수 변경
2. 기존 자체 트레이싱으로 자동 폴백
3. 코드 수정 없이 즉시 롤백 가능

---

## 6. 테스트 전략

### 6.1 단위 테스트

| 테스트 대상 | 검증 항목 |
|------------|----------|
| TracerProvider 초기화 | 예외 없이 초기화 완료 |
| get_trace_id() | OTEL/비OTEL 모드 모두 동작 |
| Django 미들웨어 | 요청별 Span 생성 |
| HTTP 클라이언트 | 자동 헤더 주입 |

### 6.2 통합 테스트

| 테스트 대상 | 검증 항목 |
|------------|----------|
| Django → Celery | trace_id 전파 |
| Django → 외부 API | traceparent 헤더 전송 |
| CB 상태 변화 | Span에 상태 정보 포함 |

### 6.3 E2E 테스트

| 테스트 대상 | 검증 항목 |
|------------|----------|
| Collector 전송 | OTLP 엔드포인트 도달 |
| Tempo 저장 | Trace 검색 가능 |
| Grafana 시각화 | Trace 그래프 표시 |

---

## 7. 관련 문서

- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md): 전체 아키텍처 개요
- [158_OTEL_COLLECTOR_CONFIGURATION.md](158_OTEL_COLLECTOR_CONFIGURATION.md): Collector 구성
- [28_CELERY_TRACE_ID_STANDARDIZATION.md](28_CELERY_TRACE_ID_STANDARDIZATION.md): 기존 Celery trace_id 표준

# Zero-Install Self-Healing Architecture

## 개요

이 문서는 **고객 시스템에 최소 침습**으로 Self-Healing 기능을 제공하기 위한 아키텍처를 정의합니다.

### 핵심 원칙

| 원칙 | 설명 |
|------|------|
| **Zero Dependency** | selfhealing 패키지는 외부 의존성 없음 |
| **Zero Code Change** | 고객 비즈니스 코드 수정 불필요 |
| **One-Line Setup** | 한 줄 설정으로 활성화 |
| **External Storage** | 고객 DB 사용 안함 → 우리 서버로 전송 |
| **External Dashboard** | 고객 시스템에 대시보드 없음 → Cloud 제공 |

---

## 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           고객 시스템                                        │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  pip install selfhealing                                            │   │
│  │  (필수 의존성: 없음)                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  settings.py (Django) 또는 main.py (FastAPI)                        │   │
│  │                                                                      │   │
│  │  # 한 줄 설정                                                        │   │
│  │  import selfhealing                                                  │   │
│  │  selfhealing.init(api_key="sk_xxx", endpoint="https://api.sh.io")   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Auto-Instrument Layer                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │ HTTP        │  │ Exception   │  │ Log         │                  │   │
│  │  │ Middleware  │  │ Hook        │  │ Handler     │                  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Data Collection Layer                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │ InMemory    │  │ Circuit     │  │ Metrics     │                  │   │
│  │  │ Buffer      │  │ Breaker     │  │ Aggregator  │                  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    HTTP Exporter (Background Thread)                 │   │
│  │  - 배치 전송 (5초마다 또는 100개 이벤트)                               │   │
│  │  - 재시도 로직 내장                                                   │   │
│  │  - 오프라인 버퍼링                                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │ HTTPS POST
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SelfHealing Cloud (우리 서버)                          │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  API Gateway: POST /api/v1/events                                   │   │
│  │  - API Key 인증                                                      │   │
│  │  - Rate Limiting                                                     │   │
│  │  - Tenant Isolation                                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Event Processing Pipeline                                          │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │ Ingestion   │  │ Analysis    │  │ Storage     │                  │   │
│  │  │ Service     │→ │ Engine      │→ │ (우리 DB)   │                  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│         ┌──────────────────────────┼──────────────────────────┐            │
│         ▼                          ▼                          ▼            │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐        │
│  │ Dashboard   │          │ Alerting    │          │ Webhook     │        │
│  │ (Web UI)    │          │ Engine      │          │ Dispatcher  │        │
│  └─────────────┘          └─────────────┘          └─────────────┘        │
│         │                          │                          │            │
│         ▼                          ▼                          ▼            │
│  [고객 웹 접속]            [이메일/SMS]               [Slack/Discord]       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 구현 Phase 정의

### Phase 1: 고객 SDK (필수)

고객이 `pip install selfhealing` 후 한 줄 설정으로 사용 가능한 기능들.

| 컴포넌트 | 설명 | 고객 작업 | 문서 |
|---------|------|----------|------|
| HTTP Exporter | 이벤트를 Cloud로 전송 | 없음 (자동) | [01-PHASE1-HTTP-EXPORTER.md](01-PHASE1-HTTP-EXPORTER.md) |
| Auto-Instrument Middleware | 프레임워크 자동 감지 + 계측 | 없음 (자동) | [02-PHASE1-AUTO-INSTRUMENT.md](02-PHASE1-AUTO-INSTRUMENT.md) |
| Exception Hook | 전역 예외 자동 캡처 | 없음 (자동) | [03-PHASE1-EXCEPTION-HOOK.md](03-PHASE1-EXCEPTION-HOOK.md) |

**고객 경험:**
```python
# settings.py (Django) - 이게 전부
import selfhealing
selfhealing.init(api_key="sk_live_xxx")
```

### Phase 2: Cloud Platform (우리 서버)

**고객 설치/설정 불필요.** 우리가 운영하는 서버에서 제공.

| 컴포넌트 | 설명 | 고객 작업 | 문서 |
|---------|------|----------|------|
| Cloud Dashboard | 웹 대시보드 | 웹 접속만 | [04-PHASE2-CLOUD-DASHBOARD.md](04-PHASE2-CLOUD-DASHBOARD.md) |
| Webhook Dispatcher | Slack/Discord 알림 | 웹훅 URL 등록 | [05-PHASE2-WEBHOOK.md](05-PHASE2-WEBHOOK.md) |
| Agent Mode (선택) | Sidecar 로그 수집 | Docker 실행 | [06-PHASE2-AGENT-MODE.md](06-PHASE2-AGENT-MODE.md) |

---

## 고객 경험 시나리오

### 시나리오 1: 최소 설치 (권장)

```bash
# 1. 설치
pip install selfhealing

# 2. 설정 (한 줄)
# settings.py
import selfhealing
selfhealing.init(api_key="sk_live_xxx")

# 3. 끝! 대시보드 접속
# https://dashboard.selfhealing.io
```

**결과:**
- ✅ 모든 HTTP 에러 자동 캡처
- ✅ 예외 자동 캡처
- ✅ Circuit Breaker 자동 적용
- ✅ Cloud 대시보드에서 실시간 모니터링
- ✅ Slack 알림 수신

### 시나리오 2: 고급 설정 (선택)

```python
import selfhealing

selfhealing.init(
    api_key="sk_live_xxx",
    
    # 선택적 설정
    service_name="payment-service",
    environment="production",
    
    # Circuit Breaker 커스터마이징
    circuit_breaker={
        "failure_threshold": 5,
        "recovery_timeout": 60,
    },
    
    # 특정 경로 제외
    exclude_paths=["/health", "/metrics"],
    
    # 샘플링 (고트래픽 환경)
    sample_rate=0.1,  # 10% 샘플링
)
```

---

## 기술 스택

### Phase 1 (SDK)

| 컴포넌트 | 기술 | 이유 |
|---------|------|------|
| HTTP Client | `urllib3` 또는 내장 `urllib` | 외부 의존성 최소화 |
| Background Thread | `threading` | 표준 라이브러리 |
| Buffer | `collections.deque` | 표준 라이브러리 |
| Serialization | `json` | 표준 라이브러리 |

### Phase 2 (Cloud)

| 컴포넌트 | 기술 | 이유 |
|---------|------|------|
| API Gateway | FastAPI | 고성능, 타입 안전 |
| Database | PostgreSQL + TimescaleDB | 시계열 데이터 최적화 |
| Message Queue | Redis Streams | 실시간 처리 |
| Dashboard | React + Grafana | 표준 시각화 |
| Alerting | 자체 구현 | 커스터마이징 |

---

## 보안 고려사항

| 항목 | 구현 |
|------|------|
| API Key 인증 | Bearer Token + HMAC 서명 |
| 전송 암호화 | HTTPS (TLS 1.3) |
| 데이터 격리 | Tenant ID별 파티셔닝 |
| PII 필터링 | 민감 데이터 자동 마스킹 |
| Rate Limiting | 분당 10,000 이벤트 |

---

## 문서 목록

1. [00-OVERVIEW.md](00-OVERVIEW.md) - 전체 개요 (현재 문서)
2. [01-PHASE1-HTTP-EXPORTER.md](01-PHASE1-HTTP-EXPORTER.md) - HTTP Exporter 상세
3. [02-PHASE1-AUTO-INSTRUMENT.md](02-PHASE1-AUTO-INSTRUMENT.md) - 자동 계측 상세
4. [03-PHASE1-EXCEPTION-HOOK.md](03-PHASE1-EXCEPTION-HOOK.md) - 예외 훅 상세
5. [04-PHASE2-CLOUD-DASHBOARD.md](04-PHASE2-CLOUD-DASHBOARD.md) - Cloud Dashboard 상세
6. [05-PHASE2-WEBHOOK.md](05-PHASE2-WEBHOOK.md) - Webhook Dispatcher 상세
7. [06-PHASE2-AGENT-MODE.md](06-PHASE2-AGENT-MODE.md) - Agent Mode 상세 (선택)

---

## 다음 단계

1. Phase 1 구현 시작 → [01-PHASE1-HTTP-EXPORTER.md](01-PHASE1-HTTP-EXPORTER.md)
2. 테스트 환경 구축
3. Phase 2 Cloud 인프라 설계

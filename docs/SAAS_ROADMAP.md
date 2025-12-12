# SelfHealing SaaS 전환 로드맵

## 📋 개요

현재 `selfhealing` 패키지를 SaaS 서비스로 전환하기 위한 로드맵입니다.

---

## 🎯 현재 상태 vs SaaS 목표

### 현재 (라이브러리 형태)
```python
# 개발자가 직접 설정해야 함
from selfhealing.services.dlq_service import DLQService, DLQConfig
from selfhealing.adapters.memory.repositories import InMemoryFailedOperationRepository

repo = InMemoryFailedOperationRepository()
config = DLQConfig(enabled=True, retention_days=30, max_replay_attempts=2)
service = DLQService(config=config, repository=repo)
```

### SaaS 목표 (API 키만 연결)
```python
# 고객이 해야 할 것
import selfhealing

selfhealing.init(api_key="sh_live_abc123...")

# 끝! 자동으로 모든 설정 완료
```

---

## 📦 SaaS 구성 요소

| 구분 | 현재 | SaaS 버전 필요 |
|------|------|----------------|
| **인증** | 없음 | API Key 발급/검증 서버 |
| **저장소** | 로컬 DB | 클라우드 DB (고객별 격리) |
| **대시보드** | Django Admin | 웹 대시보드 SaaS |
| **설정** | 코드로 직접 | 대시보드에서 클릭 |
| **알림** | 없음 | Slack/Email 연동 |
| **과금** | 없음 | 사용량 기반 과금 |

---

## 🔧 SaaS 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    고객 앱 (Django/FastAPI)              │
│  ┌─────────────────────────────────────────────────┐   │
│  │  import selfhealing                              │   │
│  │  selfhealing.init(api_key="sh_live_xxx")        │   │  ← 이것만!
│  │                                                  │   │
│  │  # 자동으로 Circuit Breaker, DLQ 활성화         │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼ HTTPS
┌─────────────────────────────────────────────────────────┐
│              SelfHealing Cloud (운영 서버)               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ API 서버  │  │ Dashboard │  │ 알림 서버 │              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  ┌──────────────────────────────────────────────────┐  │
│  │              PostgreSQL (고객별 테넌트)           │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 SDK 사용 예시 (목표)

### Django
```python
# settings.py
INSTALLED_APPS = [
    ...
    'selfhealing.django',  # 자동 미들웨어 등록
]

SELFHEALING = {
    "API_KEY": "sh_live_abc123...",
    # 끝! 나머지는 대시보드에서 설정
}
```

### FastAPI
```python
# main.py
from fastapi import FastAPI
from selfhealing.fastapi import SelfHealingMiddleware

app = FastAPI()
app.add_middleware(SelfHealingMiddleware, api_key="sh_live_abc123...")
# 끝!
```

---

## 📋 개발 단계

### Phase 1: 클라우드 백엔드 (신규)
- [ ] API Key 발급/관리 서버
- [ ] 멀티테넌트 데이터베이스 설계
- [ ] REST API for SDK ↔ Cloud 통신
- [ ] 인증/인가 시스템

### Phase 2: SDK 업그레이드
- [ ] `selfhealing.init(api_key=...)` 함수
- [ ] 자동 설정 로드 (Cloud에서)
- [ ] 로컬 캐싱 (오프라인 대비)
- [ ] 비동기 이벤트 전송

### Phase 3: 대시보드 (신규)
- [ ] 회원가입/로그인
- [ ] 프로젝트/API Key 관리
- [ ] Circuit Breaker 상태 시각화
- [ ] DLQ 항목 관리 (승인/거부)
- [ ] 알림 설정 (Slack, Email, Webhook)

### Phase 4: 과금 시스템 (신규)
- [ ] 사용량 추적
- [ ] Stripe 연동
- [ ] 플랜 관리 (Free, Pro, Enterprise)
- [ ] 인보이스 생성

---

## 💰 과금 모델 (예시)

| 플랜 | 가격 | DLQ 항목/월 | Circuit Breaker | 보관 기간 |
|------|------|-------------|-----------------|-----------|
| Free | $0 | 1,000 | 3개 | 7일 |
| Pro | $29/월 | 50,000 | 무제한 | 30일 |
| Enterprise | 협의 | 무제한 | 무제한 | 1년 |

---

## 🛠 기술 스택 (예상)

### 클라우드 백엔드
- **Framework**: FastAPI (Python) 또는 Go
- **Database**: PostgreSQL + Redis
- **Queue**: Celery + RabbitMQ
- **Infrastructure**: AWS/GCP + Kubernetes

### 대시보드
- **Frontend**: React + TypeScript
- **UI**: Tailwind CSS + shadcn/ui
- **Charts**: Recharts

### SDK
- **Python**: 현재 selfhealing 패키지 확장
- **JavaScript/TypeScript**: 신규 개발 (Node.js용)

---

## 📅 예상 일정

| 단계 | 기간 | 산출물 |
|------|------|--------|
| Phase 1 | 4-6주 | 클라우드 백엔드 MVP |
| Phase 2 | 2-3주 | SDK v2.0 |
| Phase 3 | 4-6주 | 대시보드 MVP |
| Phase 4 | 2-3주 | 과금 시스템 |
| **Total** | **12-18주** | **SaaS MVP** |

---

## 📝 비고

- 현재 `selfhealing` 패키지는 **SDK 코어**로 사용
- 클라우드 서비스가 추가되어도 **self-hosted 옵션** 유지 가능
- 점진적 전환: 라이브러리 → SaaS 하이브리드 → Full SaaS

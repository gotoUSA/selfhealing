# Self-Hosted 배포 가이드

## 개요

다른 회사가 SelfHealing 시스템 전체를 구매하여 자체 인프라에서 운영하는 경우를 위한 가이드.

---

## 현재 상태 평가

### ✅ 이미 갖춰진 것

| 항목 | 상태 | 비고 |
|------|------|------|
| Docker Compose | ✅ 완비 | 전체 스택 정의됨 |
| 환경 변수 분리 | ✅ 완비 | `.env` 파일 사용 |
| Database | ✅ PostgreSQL | 이미 컨테이너화 |
| Message Queue | ✅ Redis | 이미 컨테이너화 |
| Background Worker | ✅ Celery | 이미 컨테이너화 |
| Reverse Proxy | ✅ Nginx | 이미 컨테이너화 |
| Monitoring | ✅ Flower | 이미 컨테이너화 |

### ⚠️ 추가 필요한 것 (Phase 2 Cloud 기능)

| 항목 | 상태 | 설명 |
|------|------|------|
| Dashboard UI | 🔨 미구현 | React/Vue 앱 필요 |
| API Gateway | 🔨 미구현 | 이벤트 수신 API |
| Webhook Worker | 🔨 미구현 | 알림 발송 워커 |
| TimescaleDB | 🔨 미구현 | 시계열 데이터용 |
| Kafka | ⚪ 선택 | 대규모 시 필요 |

---

## 현재 설치 용이성: ⭐⭐⭐⭐ (4/5)

### 설치 명령어 (현재)

```bash
# 1. 저장소 클론
git clone https://github.com/your-org/selfhealing.git
cd selfhealing

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일 편집

# 3. 실행
docker-compose up -d

# 4. 마이그레이션
docker-compose exec web python manage.py migrate

# 5. 접속
open http://localhost:8000
```

**5단계로 완료!** 이미 상당히 간단합니다.

---

## 개선 제안: 더 쉬운 설치

### Option 1: 원클릭 설치 스크립트

```bash
#!/bin/bash
# install.sh

set -e

echo "🚀 SelfHealing 설치 시작..."

# 필수 도구 확인
command -v docker >/dev/null 2>&1 || { echo "Docker가 필요합니다."; exit 1; }
command -v docker-compose >/dev/null 2>&1 || { echo "Docker Compose가 필요합니다."; exit 1; }

# 환경 변수 설정
if [ ! -f .env ]; then
    cp .env.example .env
    
    # 랜덤 시크릿 키 생성
    SECRET_KEY=$(openssl rand -base64 32)
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    
    echo "✅ .env 파일 생성됨"
fi

# 컨테이너 시작
echo "📦 Docker 컨테이너 시작 중..."
docker-compose up -d

# DB 준비 대기
echo "⏳ 데이터베이스 준비 대기 중..."
sleep 10

# 마이그레이션
echo "🗄️ 데이터베이스 마이그레이션..."
docker-compose exec -T web python manage.py migrate

# 초기 데이터
docker-compose exec -T web python manage.py loaddata initial_data.json 2>/dev/null || true

echo ""
echo "✅ 설치 완료!"
echo ""
echo "📊 대시보드: http://localhost:8000"
echo "🌸 Flower 모니터링: http://localhost:5555"
echo ""
echo "관리자 계정 생성:"
echo "  docker-compose exec web python manage.py createsuperuser"
```

### Option 2: Helm Chart (Kubernetes)

```yaml
# helm/selfhealing/values.yaml

replicaCount:
  web: 2
  worker: 3

image:
  repository: selfhealing/app
  tag: latest
  pullPolicy: IfNotPresent

postgresql:
  enabled: true
  auth:
    database: selfhealing
    username: selfhealing
    password: ""  # 자동 생성

redis:
  enabled: true
  architecture: standalone

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: selfhealing.example.com
      paths:
        - path: /
          pathType: Prefix

resources:
  web:
    limits:
      cpu: 500m
      memory: 512Mi
  worker:
    limits:
      cpu: 1000m
      memory: 1Gi
```

설치:
```bash
helm repo add selfhealing https://charts.selfhealing.io
helm install my-selfhealing selfhealing/selfhealing -f values.yaml
```

---

## Self-Hosted 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Customer Infrastructure                           │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Load Balancer                             │   │
│  │                    (nginx / traefik)                         │   │
│  └───────────────────────────┬─────────────────────────────────┘   │
│                              │                                       │
│         ┌────────────────────┼────────────────────┐                 │
│         │                    │                    │                 │
│         ▼                    ▼                    ▼                 │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Web App     │     │ Web App     │     │ Dashboard   │          │
│  │ (Django)    │     │ (Django)    │     │ (React)     │          │
│  │ :8000       │     │ :8000       │     │ :3000       │          │
│  └──────┬──────┘     └──────┬──────┘     └─────────────┘          │
│         │                   │                                       │
│         └─────────┬─────────┘                                       │
│                   │                                                  │
│         ┌─────────┴─────────┐                                       │
│         │                   │                                       │
│         ▼                   ▼                                       │
│  ┌─────────────┐     ┌─────────────┐                               │
│  │ PostgreSQL  │     │ Redis       │                               │
│  │ (Primary)   │     │ (Cluster)   │                               │
│  └─────────────┘     └──────┬──────┘                               │
│                             │                                       │
│         ┌───────────────────┼───────────────────┐                  │
│         ▼                   ▼                   ▼                  │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Celery      │     │ Celery      │     │ Celery Beat │          │
│  │ Worker 1    │     │ Worker 2    │     │ (Scheduler) │          │
│  └─────────────┘     └─────────────┘     └─────────────┘          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 필요 리소스

### Minimum (소규모)

| 리소스 | 사양 |
|--------|------|
| CPU | 4 cores |
| Memory | 8 GB |
| Storage | 100 GB SSD |
| 앱 수 | 1-10 |
| 이벤트/일 | ~100,000 |

### Recommended (중규모)

| 리소스 | 사양 |
|--------|------|
| CPU | 8 cores |
| Memory | 16 GB |
| Storage | 500 GB SSD |
| 앱 수 | 10-50 |
| 이벤트/일 | ~1,000,000 |

### Enterprise (대규모)

| 리소스 | 사양 |
|--------|------|
| CPU | 32+ cores |
| Memory | 64+ GB |
| Storage | 2 TB+ SSD (Cluster) |
| 앱 수 | 50+ |
| 이벤트/일 | 10,000,000+ |
| 추가 | Kafka, PostgreSQL Cluster |

---

## 라이선스 모델 제안

### Option A: Enterprise License

```
SelfHealing Enterprise License

- 무제한 앱/서버 연결
- 모든 기능 포함
- 소스 코드 접근
- 1년 기술 지원
- 가격: 연간 $XX,XXX
```

### Option B: Node-Based License

```
SelfHealing Node License

- 노드당 라이선스
- 모든 기능 포함
- 소스 코드 미포함 (Docker 이미지만)
- 가격: 노드당 연간 $X,XXX
```

### Option C: White-Label OEM

```
SelfHealing OEM License

- 리브랜딩 허용
- 소스 코드 포함
- 재판매 권리
- 가격: 일회성 $XXX,XXX + 로열티
```

---

## 현재 부족한 점 & 로드맵

### 1. 멀티테넌시 미지원 🔴

현재: 단일 조직용
필요: 여러 고객을 한 인스턴스에서 관리

```python
# 추가 필요
class Tenant(models.Model):
    name = models.CharField(max_length=100)
    api_key = models.CharField(max_length=100)
    
class Event(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    # ...
```

### 2. 전용 Dashboard UI 미구현 🔴

현재: Django Admin만 존재
필요: 전용 React/Vue 대시보드

### 3. API 문서화 부족 🟡

현재: 기본 DRF 스펙
필요: OpenAPI 완전 문서화

### 4. 배포 자동화 부족 🟡

현재: docker-compose만
필요: Helm Chart, Terraform 모듈

---

## 결론

### 현재 Self-Hosted 배포 가능 여부

| 기능 | 가능 여부 |
|------|----------|
| Core Self-Healing | ✅ 가능 |
| Circuit Breaker | ✅ 가능 |
| DLQ | ✅ 가능 |
| 기본 모니터링 | ✅ 가능 |
| Admin UI | ✅ 가능 (Django Admin) |
| 전용 Dashboard | 🔨 개발 필요 |
| 멀티테넌시 | 🔨 개발 필요 |

### 평가

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   Self-Hosted 준비도: 70%                                    │
│   ████████████████████████░░░░░░░░░░                        │
│                                                              │
│   ✅ 핵심 기능: 100%                                         │
│   ✅ 컨테이너화: 100%                                        │
│   ✅ 환경 설정 분리: 100%                                    │
│   🟡 문서화: 60%                                             │
│   🔴 전용 UI: 20%                                            │
│   🔴 멀티테넌시: 0%                                          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 추천 로드맵

```
Phase 1 (2주): 설치 자동화
  - install.sh 스크립트
  - 환경 변수 검증
  - 헬스체크 강화

Phase 2 (4주): Dashboard UI
  - React 기반 대시보드
  - 실시간 모니터링
  - 알림 설정

Phase 3 (2주): 멀티테넌시
  - Tenant 모델
  - API 키 관리
  - 데이터 격리

Phase 4 (2주): Enterprise 기능
  - LDAP/SSO 연동
  - Audit 로그
  - 백업/복구 도구
```

---

## 다음 단계

← [07-SUMMARY.md](07-SUMMARY.md)로 돌아가기

# 🚀 프로덕션 배포 전 체크리스트

> 현재 프로젝트는 **포트폴리오용 배포 연습** 목적입니다.
> 실제 프로덕션 운영 시에는 아래 항목들을 반드시 완료해야 합니다.

---

## 📋 현재 완료된 항목

### ✅ 테스트 자동화 설정
- [x] Newman 실행 스크립트에 timeout/delay 설정 추가
- [x] Postman 테스트 Contract 검증 강화 (타입/필드 검증)
- [x] Production 환경 테스트 실행 방지
- [x] Tier 1/2/3 분리 및 CI 정책 명시
- [x] pytest 단위/통합/동시성 테스트 구조
- [x] 테스트 데이터 자동 생성 (Setup Collection)
- [x] verified/unverified 사용자 분리 테스트

### ✅ 코드 품질 도구
- [x] radon (복잡도 분석)
- [x] bandit (보안 분석)
- [x] vulture (죽은 코드 탐지)
- [x] pip-audit (의존성 보안)

### ⏳ 나중에 도입할 도구
- [ ] mutmut (Mutation Testing) - 핵심 로직 안정화 후

---

## 📌 프로덕션 전에 해야 할 것들

### 1. CI/CD 파이프라인 구축 (필수)

```yaml
# .github/workflows/ci.yml 예시
name: CI Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  # 1단계: pytest
  pytest:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
      redis:
        image: redis:7
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements-dev.txt
      - run: pytest shopping/tests/ -v --cov=shopping

  # 2단계: Postman Tier 1/2 (pytest 통과 후)
  postman-critical:
    needs: pytest
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker-compose up -d
      - run: |
          # Tier 1: Money Integrity (실패 시 배포 중단)
          bash scripts/run_postman_tests.sh tier1
          # Tier 2: Security (실패 시 배포 중단)
          bash scripts/run_postman_tests.sh tier2

  # 3단계: Postman Tier 3 (경고만)
  postman-journey:
    needs: postman-critical
    runs-on: ubuntu-latest
    continue-on-error: true  # 실패해도 배포 가능
    steps:
      - uses: actions/checkout@v4
      - run: docker-compose up -d
      - run: bash scripts/run_postman_tests.sh tier3
```

### 2. 환경 변수 및 시크릿 관리

```bash
# 절대 코드에 하드코딩하면 안 되는 것들
SECRET_KEY=<django-secret>
DATABASE_URL=<production-db-url>
TOSS_SECRET_KEY=<toss-api-key>
OAUTH_CLIENT_SECRET=<oauth-secret>

# 사용해야 할 도구
- GitHub Secrets
- AWS Secrets Manager
- HashiCorp Vault
```

### 3. 모니터링 및 로깅

| 영역 | 권장 도구 | 용도 |
|------|----------|------|
| APM | Sentry, Datadog | 에러 추적, 성능 모니터링 |
| 로그 | ELK Stack, CloudWatch | 중앙화된 로그 관리 |
| 메트릭 | Prometheus + Grafana | 시스템 메트릭 시각화 |
| 알림 | PagerDuty, Slack | 장애 알림 |

**최소 설정:**
```python
# settings/production.py
import sentry_sdk

sentry_sdk.init(
    dsn="https://xxx@sentry.io/xxx",
    environment="production",
    traces_sample_rate=0.1,
)
```

### 4. 보안 강화

| 항목 | 체크 | 설명 |
|------|------|------|
| HTTPS 강제 | ⬜ | `SECURE_SSL_REDIRECT = True` |
| HSTS | ⬜ | `SECURE_HSTS_SECONDS = 31536000` |
| CSRF 보호 | ⬜ | `CSRF_COOKIE_SECURE = True` |
| Session 보안 | ⬜ | `SESSION_COOKIE_SECURE = True` |
| DEBUG 끄기 | ⬜ | `DEBUG = False` |
| ALLOWED_HOSTS | ⬜ | 실제 도메인만 허용 |
| CORS 설정 | ⬜ | 필요한 origin만 허용 |

```python
# settings/production.py
DEBUG = False
ALLOWED_HOSTS = ['yourdomain.com', 'www.yourdomain.com']

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

### 5. 데이터베이스 설정

| 항목 | 체크 | 설명 |
|------|------|------|
| 커넥션 풀 | ⬜ | `django-db-connection-pool` 또는 PgBouncer |
| 백업 자동화 | ⬜ | 일별 백업 + 보관 정책 |
| 읽기 전용 복제본 | ⬜ | 읽기 부하 분산 (선택) |
| 인덱스 최적화 | ⬜ | 느린 쿼리 분석 후 인덱스 추가 |

### 6. Celery 프로덕션 설정

```python
# settings/production.py
CELERY_BROKER_URL = os.environ['REDIS_URL']
CELERY_RESULT_BACKEND = os.environ['REDIS_URL']

# 프로덕션 권장 설정
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# 재시도 정책
CELERY_TASK_RETRY_BACKOFF = True
CELERY_TASK_RETRY_BACKOFF_MAX = 600  # 최대 10분
```

### 7. Rate Limiting 튜닝

```python
# 프로덕션 환경에 맞는 적절한 제한값 설정
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_RATES': {
        'login': '5/min',           # 로그인 시도 제한
        'register': '3/hour',       # 회원가입 제한
        'payment_request': '10/min', # 결제 요청 제한
        'order_create': '20/min',   # 주문 생성 제한
    }
}
```

### 8. 정적 파일 및 미디어

| 항목 | 체크 | 설명 |
|------|------|------|
| collectstatic | ⬜ | `python manage.py collectstatic` |
| CDN 연동 | ⬜ | CloudFront, Cloudflare 등 |
| 미디어 스토리지 | ⬜ | S3, GCS 등 외부 스토리지 |
| 이미지 최적화 | ⬜ | WebP 변환, 리사이징 |

### 9. 부하 테스트 (선택)

```bash
# Locust로 프로덕션 전 부하 테스트
# 주의: 스테이징 환경에서만 실행!
locust -f load_tests/locustfile.py --host=https://staging.example.com

# 권장 목표
# - 동시 사용자 100명 처리
# - 평균 응답 시간 < 500ms
# - 에러율 < 1%
```

### 10. 롤백 계획

```bash
# 배포 실패 시 롤백 절차
1. 이전 버전 Docker 이미지 태그 확인
2. docker-compose down
3. 이전 버전으로 docker-compose up
4. 데이터베이스 마이그레이션 롤백 (필요시)
   python manage.py migrate <app_name> <previous_migration>
```

---

## 🎯 포트폴리오용 배포에서 강조할 포인트

### 면접에서 어필할 수 있는 것들:

1. **테스트 전략 계층화**
   - "테스트를 Tier 1/2/3으로 분리하여 금전 관련 테스트는 실패 시 배포 중단되도록 설계했습니다"

2. **Contract Testing**
   - "Postman 테스트에서 단순 200 OK가 아닌 응답 스키마까지 검증합니다"

3. **환경 분리**
   - "Production 환경에서는 자동화 테스트가 실행되지 않도록 보호했습니다"

4. **코드 품질 도구**
   - "radon, bandit, mutmut 등으로 코드 품질과 테스트 품질을 측정합니다"

5. **동시성 처리**
   - "pytest로 Race Condition 테스트를 작성하여 동시성 이슈를 검증합니다"

---

## 📅 마일스톤 제안

| 단계 | 목표 | 기간 |
|------|------|------|
| Phase 1 | 배포 환경 구축 (AWS/GCP) | 1주 |
| Phase 2 | CI/CD 파이프라인 연동 | 1주 |
| Phase 3 | 모니터링/로깅 설정 | 3일 |
| Phase 4 | 부하 테스트 및 튜닝 | 3일 |
| Phase 5 | 문서화 및 README 정리 | 2일 |

---

## 📚 참고 자료

- [Django Deployment Checklist](https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/)
- [The Twelve-Factor App](https://12factor.net/)
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)
- [Postman Newman CI Integration](https://learning.postman.com/docs/collections/using-newman-cli/continuous-integration/)

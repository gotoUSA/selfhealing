# Django Shopping Mall API

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.2.4-092E20?style=for-the-badge&logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.16-ff1709?style=for-the-badge&logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-316192?style=for-the-badge&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.5-37814A?style=for-the-badge&logo=celery&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)

> 🛍️ Django REST Framework 기반 실전 이커머스 백엔드 API

토스페이먼츠 결제, JWT 인증, 포인트 시스템을 갖춘 확장 가능한 쇼핑몰 API 서버

---

## ⚡ 5분 안에 시작하기 (Quick Start)

**필수 요구사항**: Docker Desktop 설치 필요 ([다운로드](https://www.docker.com/products/docker-desktop/))

```bash
# 1. 저장소 클론
git clone https://github.com/gotoUSA/django-shopping-mall.git
cd django-shopping-mall

# 2. 환경변수 파일 생성 (그대로 사용해도 테스트 환경에서는 동작)
cp .env.example .env

# 3. Docker Compose로 전체 서비스 시작 (첫 실행 시 이미지 빌드 약 2-3분 소요)
docker-compose up -d

# 4. 데이터베이스 초기화 (마이그레이션)
docker-compose exec web python manage.py migrate

# 5. 관리자 계정 생성
docker-compose exec web python manage.py createsuperuser

# 6. 테스트 데이터 생성 (상품, 카테고리 등)
docker-compose exec web python manage.py create_test_data --preset basic

# 🎉 완료! 아래 주소로 접속
```

### 접속 주소

| 서비스 | URL | 설명 |
|--------|-----|------|
| **API 문서 (Swagger)** | http://localhost:8000/swagger/ | 전체 API 엔드포인트 테스트 |
| **API 문서 (ReDoc)** | http://localhost:8000/redoc/ | 깔끔한 API 문서 뷰어 |
| **관리자 페이지** | http://localhost:8000/admin/ | Django Admin |
| **Celery 모니터링** | http://localhost:5555/ | 비동기 작업 상태 확인 |

### 문제 해결

```bash
# Docker 컨테이너 상태 확인
docker-compose ps

# 로그 확인 (에러 발생 시)
docker-compose logs web     # Django 앱 로그
docker-compose logs db      # PostgreSQL 로그

# 모든 서비스 재시작
docker-compose down && docker-compose up -d

# 완전 초기화 (데이터 포함 삭제)
docker-compose down -v
```

---

## 🛠 기술 스택

| 분류 | 기술 |
|------|------|
| **Backend** | Python 3.12, Django 5.2.4, DRF 3.16.0 |
| **Database** | PostgreSQL 15 |
| **Authentication** | Simple JWT, django-allauth (소셜 로그인) |
| **Payment** | Toss Payments API (카드/계좌/가상계좌) |
| **Async** | Celery 5.5.3, Redis 7, Celery Beat, Flower |
| **API Docs** | drf-spectacular (OpenAPI 3.0) |
| **Deployment** | Docker, Docker Compose, Nginx |
| **Testing** | pytest, pytest-django, coverage (70%+) |
| **Monitoring** | Prometheus, Grafana, Flower |

## ✨ 주요 기능

- **🔐 인증**: JWT 토큰, 소셜 로그인(구글/카카오/네이버), 이메일 인증
- **💳 결제**: 토스페이먼츠 연동 (카드/계좌/가상계좌), 웹훅 동기화
- **📦 상품**: 계층형 카테고리(MPTT), 다중 이미지, 리뷰, 상품 문의(Q&A)
- **🛒 장바구니 & 찜하기**: 실시간 재고 검증, Wishlist 기능
- **💰 포인트**: 등급별 적립(1-5%), FIFO 만료 처리, 자동 알림
- **⚡ 비동기**: Celery Beat 스케줄러, 이메일 발송, 포인트 만료 처리
- **🔄 Self-Healing**: Circuit Breaker, Dead Letter Queue, 자동 복구

## 🚀 상세 설치 가이드

### 방법 1: Docker Compose (권장)

위의 "5분 안에 시작하기" 섹션 참조

### 방법 2: 로컬 개발 환경 (Docker 없이)

**필수 요구사항:**
- Python 3.12+
- PostgreSQL 15+
- Redis 7+

```bash
# 1. 저장소 클론
git clone https://github.com/gotoUSA/django-shopping-mall.git
cd django-shopping-mall

# 2. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 설정
cp .env.example .env
# .env 파일 편집:
# - DATABASE_HOST=localhost (PostgreSQL이 로컬에서 실행 중이라면)
# - REDIS_URL=redis://localhost:6379/0

# 5. PostgreSQL 데이터베이스 생성 (psql에서)
# CREATE DATABASE shopping_db;
# CREATE USER shopping_user WITH PASSWORD 'shopping_pass';
# GRANT ALL PRIVILEGES ON DATABASE shopping_db TO shopping_user;

# 6. 마이그레이션
python manage.py migrate

# 7. 개발 서버 실행
python manage.py runserver

# 8. (별도 터미널) Celery 워커 실행
celery -A myproject worker -l info

# 9. (별도 터미널) Celery Beat 실행
celery -A myproject beat -l info
```

## 📚 문서

- **[API 문서](docs/API.md)** - 전체 엔드포인트 상세 가이드
- **[설치 가이드](docs/SETUP.md)** - 환경 설정 및 배포
- **[데이터 모델](docs/MODELS.md)** - DB 구조 및 관계
- **[테스트](docs/TESTING.md)** - 테스트 작성 및 실행
- **[기능](docs/FEATURES.md)** - 주요 기능

## 🧪 테스트

```bash
# Docker 환경에서 전체 테스트 실행
docker-compose run --rm web python -m pytest shopping/tests/ -o "addopts="

# 특정 테스트만 실행
docker-compose run --rm web python -m pytest shopping/tests/api/ -o "addopts=" -v

# 커버리지 리포트 생성
docker-compose run --rm web python -m pytest shopping/tests/ -o "addopts=" --cov=shopping --cov-report=html

# 로컬 환경에서 테스트 (가상환경 활성화 후)
pytest shopping/tests/unit/ -v
```

### 테스트 구조

```
shopping/tests/
├── admin/          # Django Admin 테스트
├── api/            # API 엔드포인트 테스트
│   ├── auth/       # 인증 관련 (로그인, 회원가입, 비밀번호)
│   ├── cart/       # 장바구니
│   ├── order/      # 주문
│   ├── payment/    # 결제
│   └── point/      # 포인트
├── unit/           # 단위 테스트
│   ├── models/     # 모델 테스트
│   ├── serializers/# 시리얼라이저 테스트
│   └── services/   # 서비스 로직 테스트
├── integration/    # 통합 테스트
├── schema/         # API 스키마/계약 테스트
└── tasks/          # Celery 태스크 테스트
```

## 📁 프로젝트 구조

```
myproject/
├── myproject/              # Django 프로젝트 설정
│   ├── settings/           # 환경별 설정 (base, dev, test, prod)
│   ├── urls.py
│   └── celery.py           # Celery 설정
├── shopping/               # 메인 앱
│   ├── models/             # 데이터 모델 (User, Product, Order, Payment 등)
│   ├── views/              # API 뷰 (ViewSet 기반)
│   ├── serializers/        # DRF 시리얼라이저
│   ├── services/           # 비즈니스 로직 레이어
│   ├── admin/              # Django Admin 커스터마이징
│   ├── tasks/              # Celery 비동기 작업
│   └── tests/              # 테스트 코드
├── docker/                 # Docker 관련 설정 (Grafana, Prometheus)
├── docs/                   # 프로젝트 문서
├── docker-compose.yml      # Docker Compose 설정
├── Dockerfile
├── requirements.txt        # Python 의존성
└── Makefile                # 편의 명령어
```

## 🔧 환경변수 설정

`.env.example` 파일을 `.env`로 복사하고 필요한 값을 설정합니다:

```bash
# 필수 설정 (테스트 환경에서는 기본값 사용 가능)
DJANGO_SECRET_KEY=your-secret-key      # Django 시크릿 키
DJANGO_DEBUG=True                       # 디버그 모드

# 데이터베이스 (Docker 사용 시 기본값 그대로 사용)
DATABASE_HOST=db                        # Docker: db, 로컬: localhost
DATABASE_NAME=shopping_db
DATABASE_USER=shopping_user
DATABASE_PASSWORD=shopping_pass

# 토스페이먼츠 (실제 결제 테스트 시 필요)
TOSS_CLIENT_KEY=test_ck_xxx             # 테스트 클라이언트 키
TOSS_SECRET_KEY=test_sk_xxx             # 테스트 시크릿 키
```

## 💡 개발 노트

이 프로젝트는 **Claude AI (Anthropic)**의 도움을 받아 개발되었습니다.

**AI 협업 영역:**
- RESTful API 아키텍처 설계 및 Best Practice 적용
- 토스페이먼츠 결제 로직 구현 및 웹훅 처리
- Celery 비동기 처리 구조 설계
- pytest 기반 테스트 코드 작성 및 품질 개선
- Docker Compose 환경 구성
- 코드 리뷰 및 보안 취약점 개선

**기술적 특징:**
- MPTT를 활용한 계층형 카테고리
- FIFO 기반 포인트 만료 처리
- JWT 블랙리스트로 안전한 로그아웃
- 트랜잭션 기반 주문/결제 처리

## 📄 라이선스

Apache License 2.0 - 자세한 내용은 [LICENSE](LICENSE) 참조

```
Copyright 2025 gotoUSA

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

## 👨‍💻 개발자

**GitHub**: [@gotoUSA](https://github.com/gotoUSA)

---

<div align="center">

Made with ❤️ using Django & Claude AI

</div>

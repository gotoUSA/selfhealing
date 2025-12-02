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

## 🛠 기술 스택

| 분류 | 기술 |
|------|------|
| **Backend** | Python 3.12, Django 5.2.4, DRF 3.16.0 |
| **Database** | PostgreSQL 15, SQLite (개발) |
| **Authentication** | Simple JWT, django-allauth (소셜 로그인) |
| **Payment** | Toss Payments API (카드/계좌/가상계좌) |
| **Async** | Celery 5.5.3, Redis 7, Celery Beat, Flower |
| **API Docs** | drf-yasg (Swagger/OpenAPI) |
| **Deployment** | Docker, Docker Compose |
| **Testing** | pytest, pytest-django, coverage |
| **CI/CD** | GitHub Actions |

## ✨ 주요 기능

- **🔐 인증**: JWT 토큰, 소셜 로그인(구글/카카오/네이버), 이메일 인증
- **💳 결제**: 토스페이먼츠 연동 (카드/계좌/가상계좌), 웹훅 동기화
- **📦 상품**: 계층형 카테고리(MPTT), 다중 이미지, 리뷰, 상품 문의(Q&A)
- **🛒 장바구니 & 찜하기**: 실시간 재고 검증, Wishlist 기능
- **💰 포인트**: 등급별 적립(1-5%), FIFO 만료 처리, 자동 알림
- **⚡ 비동기**: Celery Beat 스케줄러, 이메일 발송, 포인트 만료 처리

## 🚀 빠른 시작

### Docker Compose 사용 (권장)

```bash
# 1. 저장소 클론
git clone https://github.com/gotoUSA/django-shopping-mall.git
cd django-shopping-mall

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에서 SECRET_KEY, TOSS_CLIENT_KEY, TOSS_SECRET_KEY 설정

# 3. Docker Compose로 전체 서비스 실행
docker-compose up -d
# 실행되는 서비스:
# - web (Django API 서버) - 포트 8000
# - db (PostgreSQL) - 포트 5432
# - redis (Redis)
# - celery_worker (비동기 작업 처리)
# - celery_beat (스케줄 작업)
# - flower (Celery 모니터링) - 포트 5555

# 4. 마이그레이션 실행
docker-compose exec web python manage.py migrate

# 5. 슈퍼유저 생성
docker-compose exec web python manage.py createsuperuser

# 6. 테스트 데이터 생성 (선택)
docker-compose exec web python manage.py create_test_data --preset basic

# 7. 접속 확인
# API: http://localhost:8000/api/
# Admin: http://localhost:8000/admin/
# Swagger: http://localhost:8000/swagger/
# Flower: http://localhost:5555/
```

## 📚 문서

- **[API 문서](docs/API.md)** - 전체 엔드포인트 상세 가이드
- **[설치 가이드](docs/SETUP.md)** - 환경 설정 및 배포
- **[데이터 모델](docs/MODELS.md)** - DB 구조 및 관계
- **[테스트](docs/TESTING.md)** - 테스트 작성 및 실행
- **[기능](docs/FEATURES.md)** - 주요 기능

**자동 생성 문서:**
- Swagger UI: http://localhost:8000/swagger/
- ReDoc: http://localhost:8000/redoc/
- Flower (Celery 모니터링): http://localhost:5555/

## 🧪 테스트

```bash
# Docker 환경에서 테스트
docker-compose exec web pytest

# 커버리지 포함
docker-compose exec web pytest --cov=shopping --cov-report=html
```

## 📁 프로젝트 구조

```
django-shopping-mall/
├── myproject/          # Django 프로젝트 설정
├── shopping/           # 메인 앱
│   ├── models/        # 데이터 모델
│   ├── views/         # API 뷰
│   ├── serializers/   # DRF 시리얼라이저
│   ├── services/      # 비즈니스 로직
│   └── tests/         # 테스트 코드
├── docs/              # 상세 문서
└── requirements.txt
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

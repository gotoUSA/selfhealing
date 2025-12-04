# 구현된 기능 목록 (Implemented Features)

> 자동 생성일: 2025-12-04
> 스캔 범위: 프로젝트 루트, `myproject/`, `shopping/`, `load_tests/`, `scripts/` 디렉토리 전체
> 아키텍처: 5-Layer Architecture

---

## 📋 문서 구조

| # | Layer | 설명 | 파일 |
|---|-------|------|------|
| 1 | **Business Domain** | 사용자 기능 (Views) | [01-business-domain.md](./features/01-business-domain.md) |
| 2 | **Application** | 내부 구조 (Models, Services, Serializers, DTOs) | [02-application-layer.md](./features/02-application-layer.md) |
| 3 | **Technical** | 엔지니어링 역량 (Settings, Tasks, Utils, Admin) | [03-technical-layer.md](./features/03-technical-layer.md) |
| 4 | **Integration** | 외부 연동 (Toss, OAuth, Email) | [04-integration-layer.md](./features/04-integration-layer.md) |
| 5 | **Testing** | 테스트 및 품질 보증 (Fixtures, Factories, 부하테스트) | [05-testing-layer.md](./features/05-testing-layer.md) |

---

## 🔍 빠른 찾기

### 주요 기능별 위치

| 찾고 싶은 것 | 파일 | 섹션 |
|-------------|------|------|
| 회원가입/로그인 | 01-business-domain | 1.1 회원 관리 |
| 상품/카테고리 | 01-business-domain | 1.2 상품 관리 |
| 장바구니/찜 | 01-business-domain | 1.3 쇼핑 기능 |
| 주문/결제/환불 | 01-business-domain | 1.4 주문 관리 |
| 포인트/알림 | 01-business-domain | 1.5 포인트 & 알림 |
| 모델 정의 | 02-application-layer | 2.1 Domain Models |
| 서비스 로직 | 02-application-layer | 2.2 Services |
| Serializers | 02-application-layer | 2.3 Serializers |
| Celery 태스크 | 03-technical-layer | 3.2~3.3 |
| Admin 설정 | 03-technical-layer | 3.6 Admin |
| 토스 결제 연동 | 04-integration-layer | 4.1 토스페이먼츠 |
| 소셜 로그인 | 04-integration-layer | 4.2 OAuth 2.0 |
| 테스트 Fixtures | 05-testing-layer | 5.1~5.2 |
| 부하 테스트 | 05-testing-layer | 5.5 |




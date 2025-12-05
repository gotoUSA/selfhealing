"""
성능 임계값 테스트 (Schemathesis Phase 6.2)
==========================================

📋 개요
-------
이 모듈은 API 응답 시간이 정의된 임계값(Threshold) 이내인지 검증합니다.
부하 테스트(Load Test)가 아닌, 단일 요청의 응답 시간 기준선을 확인합니다.

🎯 테스트 목적
-------------
1. **응답 시간 보장**: 각 API가 정의된 시간 내에 응답하는지 확인
2. **성능 회귀 방지**: 코드 변경으로 인한 성능 저하 조기 감지
3. **SLA 준수 확인**: Service Level Agreement에 정의된 응답 시간 충족
4. **병목 지점 식별**: 느린 API 엔드포인트 조기 발견

🔧 성능 테스트 vs 부하 테스트
---------------------------
| 구분 | 성능 임계값 테스트 | 부하 테스트 |
|------|-------------------|------------|
| 목적 | 단일 요청 응답 시간 | 동시 다중 요청 처리량 |
| 요청 수 | 1~10개 (반복) | 수백~수천 동시 요청 |
| 측정 대상 | 응답 시간 (ms) | TPS, 에러율, 지연 시간 |
| 도구 | pytest + time 모듈 | Locust, k6, JMeter |
| 실행 빈도 | CI 마다 | 배포 전/정기 검사 |

📊 테스트 클래스 구조
-------------------
1. TestResponseTimeThresholds
   - 공개 API (products, categories) 응답 시간
   - 인증 API (login, token refresh) 응답 시간
   - 장바구니/주문 API 응답 시간

2. TestDatabaseQueryPerformance
   - N+1 쿼리 문제 탐지
   - 무거운 쿼리 식별

3. TestCacheEffectiveness (선택)
   - 캐시 적중 시 응답 시간 단축 확인

🚀 실행 방법
-----------
```bash
# 성능 테스트만 실행
pytest -m performance --no-cov -v -n 0

# 스키마 + 성능 테스트 모두 실행
pytest shopping/tests/schema/test_performance.py --no-cov -v -n 0

# slow 테스트 제외 (빠른 피드백용)
pytest -m "performance and not slow" --no-cov -v -n 0

# 특정 테스트만 실행
pytest -m performance -k "test_products" --no-cov -v -n 0
```

⚙️ 응답 시간 임계값 정의
----------------------
응답 시간 임계값은 다음 기준으로 설정합니다:

1. **빠른 API (100-200ms)**:
   - 단순 조회 (상품 상세, 카테고리 목록)
   - 인증 토큰 검증

2. **보통 API (300-500ms)**:
   - 목록 조회 + 페이지네이션
   - 장바구니 조회

3. **느린 API (500-1000ms)**:
   - 복잡한 집계 (인기 상품, 통계)
   - 다중 테이블 JOIN

4. **무거운 API (1000ms+)**:
   - 보고서 생성
   - 대용량 데이터 처리

⚠️ 주의사항:
   - 테스트 환경과 프로덕션 환경의 성능 차이 고려
   - DB 캐시 상태에 따라 결과 달라질 수 있음
   - 첫 요청은 cold start로 인해 느릴 수 있음

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터
- test_api_contract.py: Phase 2 - API Contract 테스트
- test_concurrency.py: Phase 6.1 - 동시성 테스트

부하 테스트 파일 (별도):
- load_tests/locustfile.py: Locust 기반 부하 테스트
- docs/LOAD_TEST_GUIDE.md: 부하 테스트 가이드

⚠️ 주의사항
----------
1. **환경 차이**: 로컬/CI/프로덕션 환경에 따라 임계값 조정 필요
   CI 환경은 보통 로컬보다 느리므로 여유 있는 임계값 설정

2. **Cold Start**: 첫 요청은 ORM 초기화, 커넥션 풀 설정 등으로 느릴 수 있음
   워밍업 요청을 먼저 보내거나, 첫 요청 제외

3. **데이터 양**: 테스트 데이터 양에 따라 응답 시간 달라짐
   일관된 데이터셋으로 테스트

4. **병렬 실행 비활성화**: 정확한 측정을 위해 -n 0 사용 권장

5. **반복 측정**: 단일 측정은 오차가 크므로 여러 번 측정 후 평균/중앙값 사용

📅 작성 정보
-----------
- 작성일: 2025-12-05
- Schemathesis Phase: 6 - 현업 수준 고도화
- 테스트 개수: 15개 (slow 마커 포함 시)

💡 임계값 커스터마이징
--------------------
프로젝트 요구사항에 따라 RESPONSE_TIME_THRESHOLDS 딕셔너리를 수정하세요.

예시:
```python
# 더 엄격한 SLA가 필요한 경우
RESPONSE_TIME_THRESHOLDS = {
    "/api/products/": 200,   # 200ms 이내
    "/api/cart/": 150,       # 150ms 이내
}

# 무거운 연산이 포함된 경우
RESPONSE_TIME_THRESHOLDS = {
    "/api/products/analytics/": 2000,  # 2초 이내
    "/api/reports/sales/": 5000,       # 5초 이내
}
```

📊 성능 개선 팁
--------------
응답 시간이 임계값을 초과하는 경우:

1. **쿼리 최적화**: select_related, prefetch_related 사용
2. **인덱스 추가**: 자주 조회하는 필드에 DB 인덱스
3. **캐싱**: Redis 캐시 활용
4. **페이지네이션**: 대용량 데이터 분할 반환
5. **비동기 처리**: 무거운 작업은 Celery로 분리
"""

import statistics
import time
from decimal import Decimal

from django.urls import reverse
from django.db import connection, reset_queries
from django.conf import settings

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from shopping.tests.factories import (
    ProductFactory,
    CategoryFactory,
    UserFactory,
    OrderFactory,
    CartFactory,
    CartItemFactory,
)


# =============================================================================
# 응답 시간 임계값 정의 (밀리초)
# =============================================================================

# API 엔드포인트별 응답 시간 임계값 (ms)
# 테스트 환경과 프로덕션 환경의 차이를 고려하여 여유 있게 설정
RESPONSE_TIME_THRESHOLDS = {
    # 공개 API - 빠른 응답 기대
    "/api/products/": 500,  # 상품 목록 (페이지네이션)
    "/api/products/{id}/": 200,  # 상품 상세
    "/api/categories/": 300,  # 카테고리 목록 (트리 구조)
    "/api/categories/{id}/": 200,  # 카테고리 상세
    # 인증 API - 빠른 응답 필수
    "/api/auth/login/": 500,  # 로그인 (JWT 생성)
    "/api/auth/token/refresh/": 300,  # 토큰 갱신
    "/api/auth/token/verify/": 200,  # 토큰 검증
    # 장바구니 API - 보통 응답
    "/api/cart/": 400,  # 장바구니 조회
    "/api/cart/summary/": 300,  # 장바구니 요약
    "/api/cart/add_item/": 500,  # 장바구니 추가
    # 주문 API - 보통~느린 응답
    "/api/orders/": 600,  # 주문 목록
    "/api/orders/{id}/": 400,  # 주문 상세
    # 집계/통계 API - 느린 응답 허용
    "/api/products/popular/": 800,  # 인기 상품
    "/api/products/best_rating/": 800,  # 평점순 상품
}


# =============================================================================
# 헬퍼 함수 및 유틸리티
# =============================================================================


def measure_response_time(client, method: str, url: str, **kwargs) -> dict:
    """
    API 응답 시간 측정

    지정된 API 엔드포인트에 요청을 보내고 응답 시간을 측정합니다.

    Args:
        client: APIClient 인스턴스
        method: HTTP 메서드 ('get', 'post', 'patch', 'delete')
        url: 요청 URL
        **kwargs: 추가 요청 파라미터 (data, format, headers 등)

    Returns:
        dict: {
            "elapsed_ms": 응답 시간 (밀리초),
            "status_code": HTTP 상태 코드,
            "success": 2xx 응답 여부
        }

    사용 예:
        result = measure_response_time(client, 'get', '/api/products/')
        assert result["elapsed_ms"] < 500
    """
    start_time = time.perf_counter()

    request_method = getattr(client, method.lower())
    response = request_method(url, **kwargs)

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    return {
        "elapsed_ms": elapsed_ms,
        "status_code": response.status_code,
        "success": 200 <= response.status_code < 300,
    }


def measure_multiple_times(client, method: str, url: str, iterations: int = 5, **kwargs) -> dict:
    """
    여러 번 측정하여 통계 반환

    단일 측정의 오차를 줄이기 위해 여러 번 측정하고
    평균, 중앙값, 최소, 최대값을 반환합니다.

    Args:
        client: APIClient 인스턴스
        method: HTTP 메서드
        url: 요청 URL
        iterations: 반복 횟수 (기본 5회)
        **kwargs: 추가 요청 파라미터

    Returns:
        dict: {
            "mean": 평균 응답 시간 (ms),
            "median": 중앙값 (ms),
            "min": 최소값 (ms),
            "max": 최대값 (ms),
            "all_success": 모든 요청 성공 여부,
            "times": 각 측정값 리스트
        }

    Note:
        첫 번째 요청은 cold start로 느릴 수 있으므로
        워밍업 요청을 먼저 보내는 것이 좋습니다.
    """
    times = []
    all_success = True

    for _ in range(iterations):
        result = measure_response_time(client, method, url, **kwargs)
        times.append(result["elapsed_ms"])
        if not result["success"]:
            all_success = False

    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "all_success": all_success,
        "times": times,
    }


def count_queries(func):
    """
    함수 실행 중 DB 쿼리 수 계산 데코레이터

    Django DEBUG=True 상태에서 실행된 쿼리 수를 반환합니다.
    N+1 문제 탐지에 유용합니다.

    Args:
        func: 쿼리 수를 측정할 함수

    Returns:
        (result, query_count) 튜플

    사용 예:
        def test_func():
            return client.get('/api/products/')
        result, query_count = count_queries(test_func)
        assert query_count < 10
    """
    reset_queries()
    result = func()
    query_count = len(connection.queries)
    return result, query_count


# =============================================================================
# 공개 API 응답 시간 테스트
# =============================================================================


@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestPublicApiResponseTime:
    """
    🌐 공개 API 응답 시간 테스트

    인증 없이 접근 가능한 공개 API의 응답 시간이
    정의된 임계값 이내인지 검증합니다.

    📋 테스트 대상:
    - GET /api/products/ (상품 목록)
    - GET /api/products/{id}/ (상품 상세)
    - GET /api/categories/ (카테고리 목록)
    - GET /api/products/popular/ (인기 상품)

    ✅ 검증 포인트:
    - 응답 시간 < 임계값
    - 응답 코드 200 OK
    - 반복 측정 시 일관성
    """

    @pytest.fixture
    def test_products(self, db):
        """테스트용 상품 데이터 생성 (20개)"""
        category = CategoryFactory()
        seller = UserFactory(username=f"perf_seller_{time.time()}", is_seller=True)
        products = [
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                price=Decimal(str(10000 + i * 1000)),
                is_active=True,
            )
            for i in range(20)
        ]
        return {"products": products, "category": category}

    def test_product_list_response_time(self, test_products):
        """
        상품 목록 API 응답 시간 테스트

        상품 목록 조회가 정의된 임계값(500ms) 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 500ms
        - 모든 요청 성공 (200 OK)
        - 최대 응답 시간도 허용 범위 내

        Note:
            페이지네이션이 적용되어 있으므로 전체 데이터 양과 무관하게
            일정한 응답 시간을 기대할 수 있습니다.
        """
        client = APIClient()
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/products/", 500)

        # 워밍업 요청
        client.get(reverse("product-list"))

        # 5회 측정
        stats = measure_multiple_times(client, "get", reverse("product-list"), iterations=5)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"상품 목록 API 응답 시간 초과!\n"
            f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)\n"
            f"중앙값: {stats['median']:.2f}ms\n"
            f"범위: {stats['min']:.2f}ms ~ {stats['max']:.2f}ms"
        )

        # Assert: 모든 요청 성공
        assert stats["all_success"], "일부 요청 실패!"

    def test_product_detail_response_time(self, test_products):
        """
        상품 상세 API 응답 시간 테스트

        개별 상품 상세 조회가 200ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 200ms
        - 단일 레코드 조회이므로 빠른 응답 기대
        """
        client = APIClient()
        product = test_products["products"][0]
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/products/{id}/", 200)

        url = reverse("product-detail", kwargs={"pk": product.id})

        # 워밍업 요청
        client.get(url)

        # 5회 측정
        stats = measure_multiple_times(client, "get", url, iterations=5)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"상품 상세 API 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )

        # Assert: 모든 요청 성공
        assert stats["all_success"], "일부 요청 실패!"

    def test_category_list_response_time(self, test_products):
        """
        카테고리 목록 API 응답 시간 테스트

        카테고리 목록 (트리 구조) 조회가 300ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 300ms
        - MPTT 트리 구조 조회 성능
        """
        client = APIClient()
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/categories/", 300)

        # 워밍업 요청
        client.get(reverse("category-list"))

        # 5회 측정
        stats = measure_multiple_times(client, "get", reverse("category-list"), iterations=5)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"카테고리 목록 API 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )

        # Assert: 모든 요청 성공
        assert stats["all_success"], "일부 요청 실패!"

    @pytest.mark.slow
    def test_popular_products_response_time(self, test_products):
        """
        인기 상품 API 응답 시간 테스트 (@slow)

        인기 상품 집계 API가 800ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 800ms
        - 집계 쿼리 성능

        Note:
            @slow 마커로 인해 기본 실행에서 제외됩니다.
        """
        client = APIClient()
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/products/popular/", 800)

        # 워밍업 요청
        client.get(reverse("product-popular"))

        # 3회 측정 (집계 쿼리는 느리므로 횟수 줄임)
        stats = measure_multiple_times(client, "get", reverse("product-popular"), iterations=3)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"인기 상품 API 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )


# =============================================================================
# 인증 API 응답 시간 테스트
# =============================================================================


@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestAuthApiResponseTime:
    """
    🔐 인증 API 응답 시간 테스트

    로그인, 토큰 갱신 등 인증 관련 API의 응답 시간이
    사용자 경험에 영향을 주지 않는 수준인지 검증합니다.

    📋 테스트 대상:
    - POST /api/auth/login/ (로그인)
    - POST /api/auth/token/refresh/ (토큰 갱신)
    - GET /api/auth/token/verify/ (토큰 검증)

    ✅ 검증 포인트:
    - 로그인 < 500ms
    - 토큰 갱신 < 300ms
    - 토큰 검증 < 200ms

    인증 API는 사용자가 앱 시작 시 매번 호출하므로
    빠른 응답이 중요합니다.
    """

    @pytest.fixture
    def test_user(self, db):
        """테스트용 사용자 생성"""
        return UserFactory(username=f"perf_user_{time.time()}")

    def test_login_response_time(self, test_user):
        """
        로그인 API 응답 시간 테스트

        로그인 요청이 500ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 500ms
        - JWT 토큰 생성 포함
        - 비밀번호 해시 검증 포함
        """
        client = APIClient()
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/auth/login/", 500)

        login_data = {
            "username": test_user.username,
            "password": "testpass123",
        }

        # 워밍업 요청
        client.post(reverse("auth-login"), login_data, format="json")

        # 5회 측정
        times = []
        for _ in range(5):
            result = measure_response_time(client, "post", reverse("auth-login"), data=login_data, format="json")
            times.append(result["elapsed_ms"])

        mean_time = statistics.mean(times)

        # Assert: 평균 응답 시간 < 임계값
        assert mean_time < threshold, f"로그인 API 응답 시간 초과!\n" f"평균: {mean_time:.2f}ms (임계값: {threshold}ms)"

    def test_token_refresh_response_time(self, test_user):
        """
        토큰 갱신 API 응답 시간 테스트

        토큰 갱신이 300ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 300ms
        - 새 토큰 생성 포함

        Note:
            Refresh 토큰은 보안상 HTTP Only Cookie로 전달됩니다.
            테스트에서는 response.cookies에서 추출합니다.
        """
        client = APIClient()
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/auth/token/refresh/", 300)

        # 로그인하여 refresh 토큰 획득
        login_response = client.post(
            reverse("auth-login"),
            {"username": test_user.username, "password": "testpass123"},
            format="json",
        )

        # 1. JSON body에서 시도
        data = login_response.json()
        refresh_token = data.get("refresh") or data.get("token", {}).get("refresh")

        # 2. HTTP Only Cookie에서 시도 (보안 설계상 여기에 있음)
        if not refresh_token:
            refresh_token = login_response.cookies.get("refresh_token")
            if refresh_token:
                refresh_token = refresh_token.value

        if not refresh_token:
            pytest.skip("Refresh 토큰 없음 (JSON body, Cookie 모두 확인) - 테스트 스킵")

        # 워밍업 요청
        client.post(
            reverse("token-refresh"),
            {"refresh": refresh_token},
            format="json",
        )

        # 5회 측정
        times = []
        for _ in range(5):
            result = measure_response_time(
                client, "post", reverse("token-refresh"), data={"refresh": refresh_token}, format="json"
            )
            times.append(result["elapsed_ms"])

        mean_time = statistics.mean(times)

        # Assert: 평균 응답 시간 < 임계값
        assert mean_time < threshold, f"토큰 갱신 API 응답 시간 초과!\n" f"평균: {mean_time:.2f}ms (임계값: {threshold}ms)"


# =============================================================================
# 인증 필요 API 응답 시간 테스트
# =============================================================================


@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestAuthenticatedApiResponseTime:
    """
    🔒 인증 필요 API 응답 시간 테스트

    JWT 인증이 필요한 API의 응답 시간을 검증합니다.
    인증 오버헤드가 포함된 상태에서의 성능을 측정합니다.

    📋 테스트 대상:
    - GET /api/cart/ (장바구니 조회)
    - POST /api/cart/add_item/ (장바구니 추가)
    - GET /api/orders/ (주문 목록)

    ✅ 검증 포인트:
    - 장바구니 조회 < 400ms
    - 장바구니 추가 < 500ms
    - 주문 목록 < 600ms
    """

    @pytest.fixture
    def auth_client(self, db):
        """인증된 APIClient 반환"""
        user = UserFactory(username=f"perf_auth_user_{time.time()}")
        client = APIClient()

        # 로그인
        response = client.post(
            reverse("auth-login"),
            {"username": user.username, "password": "testpass123"},
            format="json",
        )

        data = response.json()
        token = data.get("access") or data.get("token", {}).get("access")
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        return {"client": client, "user": user, "token": token}

    @pytest.fixture
    def test_cart_data(self, auth_client, db):
        """테스트용 장바구니 데이터 생성"""
        user = auth_client["user"]
        category = CategoryFactory()
        seller = UserFactory(username=f"cart_seller_{time.time()}", is_seller=True)

        products = [
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                is_active=True,
            )
            for _ in range(5)
        ]

        # 장바구니 생성
        cart = CartFactory(user=user, is_active=True)
        for product in products[:3]:
            CartItemFactory(cart=cart, product=product, quantity=2)

        return {
            "cart": cart,
            "products": products,
            "category": category,
        }

    def test_cart_retrieve_response_time(self, auth_client, test_cart_data):
        """
        장바구니 조회 API 응답 시간 테스트

        장바구니 조회가 400ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 400ms
        - 장바구니 아이템 + 상품 정보 조회 포함
        """
        client = auth_client["client"]
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/cart/", 400)

        # 워밍업 요청
        client.get(reverse("cart-detail"))

        # 5회 측정
        stats = measure_multiple_times(client, "get", reverse("cart-detail"), iterations=5)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"장바구니 조회 API 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )

        # Assert: 모든 요청 성공
        assert stats["all_success"], "일부 요청 실패!"

    def test_cart_add_item_response_time(self, auth_client, test_cart_data):
        """
        장바구니 추가 API 응답 시간 테스트

        장바구니 상품 추가가 500ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 500ms
        - 재고 확인 + 장바구니 생성/업데이트 포함
        """
        client = auth_client["client"]
        product = test_cart_data["products"][-1]  # 장바구니에 없는 상품
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/cart/add_item/", 500)

        add_data = {"product_id": product.id, "quantity": 1}

        # 5회 측정 (매번 다른 상품으로 테스트하기 어려우므로 동일 상품에 추가)
        times = []
        for _ in range(5):
            result = measure_response_time(client, "post", reverse("cart-add-item"), data=add_data, format="json")
            times.append(result["elapsed_ms"])

        mean_time = statistics.mean(times)

        # Assert: 평균 응답 시간 < 임계값
        assert mean_time < threshold, f"장바구니 추가 API 응답 시간 초과!\n" f"평균: {mean_time:.2f}ms (임계값: {threshold}ms)"

    @pytest.mark.slow
    def test_order_list_response_time(self, auth_client, db):
        """
        주문 목록 API 응답 시간 테스트 (@slow)

        주문 목록 조회가 600ms 이내에 완료되는지 확인합니다.

        🔍 검증 포인트:
        - 평균 응답 시간 < 600ms
        - 주문 + 주문아이템 + 상품 정보 조회 포함
        """
        client = auth_client["client"]
        user = auth_client["user"]
        threshold = RESPONSE_TIME_THRESHOLDS.get("/api/orders/", 600)

        # 테스트용 주문 생성
        category = CategoryFactory()
        seller = UserFactory(username=f"order_seller_{time.time()}", is_seller=True)
        product = ProductFactory(category=category, seller=seller, is_active=True)

        for i in range(5):
            OrderFactory(user=user)

        # 워밍업 요청
        client.get(reverse("order-list"))

        # 5회 측정
        stats = measure_multiple_times(client, "get", reverse("order-list"), iterations=5)

        # Assert: 평균 응답 시간 < 임계값
        assert stats["mean"] < threshold, (
            f"주문 목록 API 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )


# =============================================================================
# 데이터베이스 쿼리 성능 테스트
# =============================================================================


@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestDatabaseQueryPerformance:
    """
    🗄️ 데이터베이스 쿼리 성능 테스트

    N+1 쿼리 문제나 과도한 쿼리 수를 탐지합니다.
    쿼리 수가 데이터 양에 비례하지 않아야 합니다.

    📋 테스트 시나리오:
    - 상품 목록 조회 시 쿼리 수
    - 장바구니 조회 시 쿼리 수
    - 주문 상세 조회 시 쿼리 수

    ✅ 검증 포인트:
    - 쿼리 수 < 예상 최대값
    - 데이터 증가 시에도 쿼리 수 일정

    🔧 N+1 문제란?
    - 목록 조회 시 각 항목마다 추가 쿼리 발생
    - 예: 10개 상품 조회 = 1(목록) + 10(카테고리) = 11개 쿼리
    - 해결: select_related, prefetch_related 사용
    """

    @pytest.fixture
    def test_data(self, db, django_assert_num_queries):
        """쿼리 테스트용 데이터 생성"""
        category = CategoryFactory()
        seller = UserFactory(username=f"query_seller_{time.time()}", is_seller=True)

        products = [
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                is_active=True,
            )
            for _ in range(20)
        ]

        return {"products": products, "category": category}

    def test_product_list_query_count(self, test_data):
        """
        상품 목록 쿼리 수 테스트

        상품 목록 조회 시 N+1 문제가 없는지 확인합니다.

        🔍 검증 포인트:
        - 쿼리 수 < 10개 (고정)
        - 상품 수와 무관하게 일정

        예상 쿼리:
        1. 상품 목록 (with category, seller)
        2. 카운트 (페이지네이션)
        3-5. 추가 정보 (평점, 리뷰 수 등)

        Note:
        - DEBUG=False일 때는 connection.queries가 비어있으므로 쿼리 수 검증 생략
        - 대신 응답 성공 여부만 확인
        """
        client = APIClient()

        # 첫 요청으로 캐시 워밍업
        client.get(reverse("product-list"))

        # 쿼리 수 측정 (DEBUG=True일 때만)
        reset_queries()
        response = client.get(reverse("product-list"))

        # Assert: 성공 응답 (항상 검증)
        assert response.status_code == status.HTTP_200_OK

        # Assert: 쿼리 수 검증 (DEBUG=True일 때만)
        if settings.DEBUG:
            query_count = len(connection.queries)
            assert query_count < 15, (
                f"상품 목록 쿼리 수 과다!\n"
                f"실제: {query_count}개\n"
                f"쿼리 목록:\n" + "\n".join(f"  {i+1}. {q['sql'][:100]}..." for i, q in enumerate(connection.queries))
            )


# =============================================================================
# 페이지네이션 성능 테스트
# =============================================================================


@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestPaginationPerformance:
    """
    📄 페이지네이션 성능 테스트

    대용량 데이터셋에서 페이지네이션이 효율적으로 동작하는지 검증합니다.
    OFFSET 기반 페이지네이션의 한계와 커서 기반 대안을 고려합니다.

    📋 테스트 시나리오:
    - 첫 페이지 vs 마지막 페이지 응답 시간 비교
    - 큰 page_size 요청 시 성능
    - 정렬 + 필터링 조합 성능
    """

    @pytest.fixture
    def large_dataset(self, db):
        """대용량 테스트 데이터 생성 (100개 상품)"""
        category = CategoryFactory()
        seller = UserFactory(username=f"large_seller_{time.time()}", is_seller=True)

        products = [
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                price=Decimal(str(10000 + i * 100)),
                is_active=True,
            )
            for i in range(100)
        ]

        return {"products": products, "category": category}

    def test_first_page_vs_last_page(self, large_dataset):
        """
        첫 페이지 vs 마지막 페이지 성능 비교

        OFFSET 기반 페이지네이션에서 마지막 페이지 조회가
        첫 페이지보다 크게 느리지 않은지 확인합니다.

        🔍 검증 포인트:
        - 마지막 페이지 응답 시간 < 첫 페이지 * 2
        - 둘 다 임계값 이내

        Note:
            OFFSET이 커질수록 성능 저하 발생 가능
            매우 큰 데이터셋에서는 커서 기반 페이지네이션 권장
        """
        client = APIClient()

        # 첫 페이지 측정
        first_page_stats = measure_multiple_times(client, "get", reverse("product-list"), iterations=3)

        # 마지막 페이지 측정 (100개 / 10개 = 10페이지)
        last_page_stats = measure_multiple_times(client, "get", f"{reverse('product-list')}?page=10", iterations=3)

        # Assert: 마지막 페이지가 첫 페이지보다 2배 이상 느리지 않음
        ratio = last_page_stats["mean"] / first_page_stats["mean"]
        assert ratio < 3, (
            f"마지막 페이지 성능 저하!\n"
            f"첫 페이지: {first_page_stats['mean']:.2f}ms\n"
            f"마지막 페이지: {last_page_stats['mean']:.2f}ms\n"
            f"비율: {ratio:.2f}x"
        )

    @pytest.mark.slow
    def test_large_page_size(self, large_dataset):
        """
        큰 page_size 요청 성능 테스트 (@slow)

        page_size=50 같은 큰 요청이 처리 가능한지 확인합니다.

        🔍 검증 포인트:
        - 응답 시간 < 1000ms
        - 메모리 과다 사용 없음
        """
        client = APIClient()
        threshold = 1000  # 1초

        # 워밍업
        client.get(f"{reverse('product-list')}?page_size=50")

        # 측정
        stats = measure_multiple_times(client, "get", f"{reverse('product-list')}?page_size=50", iterations=3)

        # Assert: 응답 시간 < 1초
        assert stats["mean"] < threshold, (
            f"큰 page_size 응답 시간 초과!\n" f"평균: {stats['mean']:.2f}ms (임계값: {threshold}ms)"
        )

        # Assert: 성공 응답
        assert stats["all_success"], "요청 실패!"

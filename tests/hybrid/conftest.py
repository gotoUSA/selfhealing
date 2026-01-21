"""
Hybrid tests conftest - fixtures for Celery/Redis integration tests.

이 conftest는 tests/hybrid/ 폴더에서 shopping/tests/conftest.py의
픽스처를 사용할 수 있도록 import합니다.

Note:
    pytest가 tests/hybrid/만 직접 실행할 때 shopping/tests/conftest.py가
    자동으로 로드되지 않으므로, 필요한 픽스처를 명시적으로 import합니다.
"""
import pytest
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


# =============================================================================
# API Client Fixture
# =============================================================================


@pytest.fixture
def api_client():
    """
    DRF APIClient 인스턴스

    REST API 테스트용 클라이언트
    Function scope: 매 테스트마다 새로운 클라이언트 생성
    """
    return APIClient()


# =============================================================================
# Constants
# =============================================================================

DEFAULT_USER_POINTS = Decimal("100000")
DEFAULT_PRODUCT_PRICE = Decimal("10000")
DEFAULT_PRODUCT_STOCK = 10


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def user_factory(db):
    """
    User Factory - 유연한 사용자 생성

    사용 예시:
        user1 = user_factory()  # 기본값
        user2 = user_factory(username="custom", points=10000)  # 커스텀
        user3 = user_factory(is_email_verified=False)  # 미인증
    """
    import uuid

    def _create_user(**kwargs):
        unique_id = uuid.uuid4().hex[:8]
        defaults = {
            "username": f"testuser_{unique_id}",
            "email": f"test_{unique_id}@example.com",
            "password": "testpass123",
            "phone_number": f"010-{unique_id[:4]}-{unique_id[4:8]}",
            "points": DEFAULT_USER_POINTS,
            "is_email_verified": True,
        }
        # kwargs로 받은 값으로 defaults 덮어쓰기
        defaults.update(kwargs)

        # username이 중복될 수 있으므로 카운터 추가
        username = defaults.pop("username")
        counter = 1
        original_username = username
        while User.objects.filter(username=username).exists():
            username = f"{original_username}{counter}"
            counter += 1

        # email이 중복될 수 있으므로 카운터 추가
        email = defaults.pop("email")
        counter = 1
        original_email = email
        while User.objects.filter(email=email).exists():
            email_parts = original_email.split("@")
            email = f"{email_parts[0]}{counter}@{email_parts[1]}"
            counter += 1

        return User.objects.create_user(username=username, email=email, **defaults)

    return _create_user


@pytest.fixture
def seller_user(db):
    """
    판매자 사용자

    상품 등록/관리 권한을 가진 사용자
    """
    return User.objects.create_user(
        username="seller",
        email="seller@example.com",
        password="sellerpass123",
        phone_number="010-9999-8888",
        is_email_verified=True,
        is_seller=True,  # 판매자 권한 부여
    )


# =============================================================================
# Category & Product Fixtures
# =============================================================================


@pytest.fixture
def category(db):
    """
    기본 카테고리

    대부분의 상품 테스트에서 사용
    """
    from shopping.models.product import Category

    # 기존 category가 있다면 삭제 (중복 방지)
    Category.objects.filter(slug="test-category").delete()

    return Category.objects.create(
        name="테스트 카테고리",
        slug="test-category",
    )


@pytest.fixture
def product(db, category, seller_user):
    """
    기본 테스트 상품 (재고 있음)

    - 가격: DEFAULT_PRODUCT_PRICE (10,000원)
    - 재고: DEFAULT_PRODUCT_STOCK (10개)
    - 판매중
    """
    from shopping.models.product import Product

    return Product.objects.create(
        name="테스트 상품",
        slug="test-product",
        category=category,
        seller=seller_user,
        price=DEFAULT_PRODUCT_PRICE,
        stock=DEFAULT_PRODUCT_STOCK,
        sku="TEST-001",
        description="테스트 상품 설명",
        is_active=True,
    )


# =============================================================================
# Celery Eager Mode Fixtures
# =============================================================================


@pytest.fixture
def force_eager_mode(settings):
    """
    Celery eager mode 강제 활성화
    
    비동기 태스크를 동기적으로 실행하여 테스트 용이하게 함
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    yield

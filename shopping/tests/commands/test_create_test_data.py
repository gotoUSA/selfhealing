"""create_test_data management command 테스트

이 테스트는 개발환경용 테스트 데이터 생성 커맨드를 검증합니다.
일반 CI에서는 실행되지 않습니다. (pytest -m load_test 로 실행)
"""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command

import pytest

from shopping.models.cart import Cart
from shopping.models.order import Order
from shopping.models.product import Category, Product, ProductReview

User = get_user_model()


@pytest.mark.load_test
class TestCreateTestData:
    """create_test_data 커맨드 테스트"""

    def test_creates_data_with_minimal_preset(self, db):
        """minimal 프리셋으로 데이터 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)

        # Assert
        assert Category.objects.exists()
        assert Product.objects.exists()
        assert User.objects.filter(username__startswith="test_").exists()

    def test_creates_data_with_basic_preset(self, db):
        """basic 프리셋으로 데이터 생성 (기본값)"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="basic", stdout=out)

        # Assert
        assert Category.objects.count() >= 3
        assert User.objects.filter(username__startswith="test_").count() >= 5

    def test_creates_data_with_full_preset(self, db):
        """full 프리셋으로 데이터 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="full", stdout=out)

        # Assert
        assert Category.objects.count() >= 5
        assert User.objects.filter(username__startswith="test_").count() >= 20

    def test_clear_option_removes_existing_data(self, db):
        """clear 옵션으로 기존 데이터 삭제 후 재생성"""
        # Arrange
        out = StringIO()
        call_command("create_test_data", preset="minimal", stdout=out)
        initial_product_ids = set(Product.objects.values_list("id", flat=True))

        # Act
        call_command("create_test_data", preset="minimal", clear=True, stdout=out)

        # Assert
        new_product_ids = set(Product.objects.values_list("id", flat=True))
        # 기존 상품과 새 상품의 ID가 다름 (삭제 후 재생성됨)
        assert not initial_product_ids.intersection(new_product_ids)

    def test_creates_admin_user(self, db):
        """관리자 계정 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)

        # Assert
        admin = User.objects.filter(username="admin").first()
        assert admin is not None
        assert admin.is_staff is True
        assert admin.is_superuser is True

    def test_creates_categories_with_hierarchy(self, db):
        """카테고리 계층 구조 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)

        # Assert
        parent_categories = Category.objects.filter(parent__isnull=True)
        child_categories = Category.objects.filter(parent__isnull=False)
        assert parent_categories.exists()
        assert child_categories.exists()

    def test_creates_products_with_category(self, db):
        """카테고리가 있는 상품 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)

        # Assert
        products = Product.objects.select_related("category")
        for product in products:
            assert product.category is not None

    def test_users_option_overrides_preset(self, db):
        """users 옵션으로 프리셋 사용자 수 오버라이드"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", users=10, stdout=out)

        # Assert
        user_count = User.objects.filter(username__startswith="test_").count()
        assert user_count == 10

    def test_reviews_option_creates_reviews(self, db):
        """reviews 옵션으로 리뷰 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", reviews=True, stdout=out)

        # Assert
        assert ProductReview.objects.exists()

    def test_no_reviews_option_skips_reviews(self, db):
        """no-reviews 옵션으로 리뷰 생성 건너뜀"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="basic", no_reviews=True, stdout=out)

        # Assert
        assert not ProductReview.objects.exists()

    def test_show_presets_option(self, db):
        """show-presets 옵션으로 프리셋 정보 표시"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", show_presets=True, stdout=out)
        output = out.getvalue()

        # Assert
        assert "minimal" in output
        assert "basic" in output
        assert "full" in output

    def test_creates_sample_carts(self, db):
        """샘플 장바구니 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="basic", stdout=out)

        # Assert
        assert Cart.objects.exists()

    def test_creates_sample_orders(self, db):
        """샘플 주문 생성"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="basic", stdout=out)

        # Assert
        assert Order.objects.exists()

    def test_products_have_valid_sku(self, db):
        """상품의 SKU가 유니크함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="basic", stdout=out)

        # Assert
        skus = Product.objects.values_list("sku", flat=True)
        assert len(skus) == len(set(skus))

    def test_output_contains_summary(self, db):
        """출력에 요약 정보 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)
        output = out.getvalue()

        # Assert
        assert "완료" in output or "생성" in output

    def test_output_contains_test_accounts(self, db):
        """출력에 테스트 계정 정보 포함"""
        # Arrange
        out = StringIO()

        # Act
        call_command("create_test_data", preset="minimal", stdout=out)
        output = out.getvalue()

        # Assert
        assert "test_user" in output or "admin" in output

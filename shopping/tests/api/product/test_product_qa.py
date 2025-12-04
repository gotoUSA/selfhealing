"""
상품 문의 기능 테스트

Pytest + Fixture 패턴 사용
"""

from decimal import Decimal

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.notification import Notification
from shopping.models.product import Category, Product
from shopping.models.product_qa import ProductAnswer, ProductQuestion
from shopping.models.user import User
from shopping.services import ProductQAService


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def qa_category(db):
    """Q&A 테스트용 카테고리"""
    return Category.objects.create(name="테스트 카테고리", slug="test-category")


@pytest.fixture
def qa_seller(db):
    """Q&A 테스트용 판매자"""
    return User.objects.create_user(
        username="seller",
        email="seller@test.com",
        password="testpass123",
    )


@pytest.fixture
def qa_buyer(db):
    """Q&A 테스트용 구매자"""
    return User.objects.create_user(
        username="buyer",
        email="buyer@test.com",
        password="testpass123",
    )


@pytest.fixture
def qa_product(db, qa_category, qa_seller):
    """Q&A 테스트용 상품"""
    return Product.objects.create(
        name="테스트 상품",
        slug="test-product",
        category=qa_category,
        price=Decimal("10000"),
        stock=100,
        sku="TEST-001",
        seller=qa_seller,
    )


@pytest.fixture
def authenticated_buyer_client(qa_buyer):
    """구매자로 인증된 클라이언트"""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=qa_buyer)
    return client


@pytest.fixture
def authenticated_seller_client(qa_seller):
    """판매자로 인증된 클라이언트"""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=qa_seller)
    return client


# ==========================================
# 상품 문의 테스트
# ==========================================


class TestProductQuestion:
    """상품 문의 기능 테스트"""

    def test_create_question(self, authenticated_buyer_client, qa_buyer, qa_product):
        """문의 작성 테스트"""
        # Arrange
        url = reverse("product-question-list", kwargs={"product_pk": qa_product.id})
        data = {
            "title": "배송 문의",
            "content": "배송 언제 되나요?",
            "is_secret": False,
        }

        # Act
        response = authenticated_buyer_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert ProductQuestion.objects.count() == 1

        question = ProductQuestion.objects.first()
        assert question.user == qa_buyer
        assert question.product == qa_product
        assert question.title == "배송 문의"

    def test_create_secret_question(self, authenticated_buyer_client, qa_product):
        """비밀글 문의 작성 테스트"""
        # Arrange
        url = reverse("product-question-list", kwargs={"product_pk": qa_product.id})
        data = {"title": "교환 문의", "content": "교환 가능한가요?", "is_secret": True}

        # Act
        response = authenticated_buyer_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        question = ProductQuestion.objects.first()
        assert question.is_secret is True

    def test_list_questions_with_secret(
        self, api_client, authenticated_buyer_client, authenticated_seller_client, qa_buyer, qa_product
    ):
        """비밀글 포함 문의 목록 조회 테스트"""
        # Arrange
        ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="일반 문의",
            content="일반 문의 내용",
            is_secret=False,
        )
        ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="비밀 문의",
            content="비밀 문의 내용",
            is_secret=True,
        )
        url = reverse("product-question-list", kwargs={"product_pk": qa_product.id})

        # Act & Assert 1 - 비로그인 사용자: 일반 문의만 보임
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        # Act & Assert 2 - 작성자: 모두 보임
        response = authenticated_buyer_client.get(url)
        assert len(response.data["results"]) == 2

        # Act & Assert 3 - 판매자: 모두 보임
        response = authenticated_seller_client.get(url)
        assert len(response.data["results"]) == 2

    def test_update_question(self, authenticated_buyer_client, qa_buyer, qa_product):
        """문의 수정 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )
        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": qa_product.id, "pk": question.id},
        )
        data = {"title": "수정된 제목", "content": "수정된 내용"}

        # Act
        response = authenticated_buyer_client.patch(url, data)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        question.refresh_from_db()
        assert question.title == "수정된 제목"
        assert question.content == "수정된 내용"

    def test_cannot_update_answered_question(self, authenticated_buyer_client, qa_buyer, qa_seller, qa_product):
        """답변 달린 문의는 수정 불가 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )
        ProductQAService.create_answer(question=question, seller=qa_seller, content="내일 출발합니다")
        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": qa_product.id, "pk": question.id},
        )
        data = {"title": "수정 시도"}

        # Act
        response = authenticated_buyer_client.patch(url, data)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_question(self, authenticated_buyer_client, qa_buyer, qa_product):
        """문의 삭제 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )
        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": qa_product.id, "pk": question.id},
        )

        # Act
        response = authenticated_buyer_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert ProductQuestion.objects.count() == 0


# ==========================================
# 상품 답변 테스트
# ==========================================


class TestProductAnswer:
    """상품 답변 기능 테스트"""

    def test_create_answer(self, authenticated_seller_client, qa_buyer, qa_product):
        """답변 작성 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )
        url = reverse(
            "product-question-answer",
            kwargs={"product_pk": qa_product.id, "pk": question.id},
        )
        data = {"content": "내일 출발 예정입니다!"}

        # Act
        response = authenticated_seller_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_201_CREATED
        assert ProductAnswer.objects.count() == 1
        question.refresh_from_db()
        assert question.is_answered is True

    def test_answer_notification(self, qa_buyer, qa_seller, qa_product):
        """답변 작성 시 알림 생성 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # Act
        ProductQAService.create_answer(question=question, seller=qa_seller, content="내일 출발 예정입니다!")

        # Assert
        assert Notification.objects.count() == 1
        notification = Notification.objects.first()
        assert notification.user == qa_buyer
        assert notification.notification_type == "qa_answer"
        assert "답변" in notification.title
        assert notification.is_read is False

    def test_buyer_cannot_answer(self, authenticated_buyer_client, qa_buyer, qa_product):
        """구매자는 답변 작성 불가 테스트"""
        # Arrange
        question = ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )
        url = reverse(
            "product-question-answer",
            kwargs={"product_pk": qa_product.id, "pk": question.id},
        )
        data = {"content": "답변입니다"}

        # Act
        response = authenticated_buyer_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ==========================================
# 내 문의 목록 테스트
# ==========================================


class TestMyQuestions:
    """내 문의 목록 테스트"""

    def test_my_questions(self, authenticated_buyer_client, qa_buyer, qa_seller, qa_category, qa_product):
        """내 문의 목록 조회 테스트"""
        # Arrange
        product2 = Product.objects.create(
            name="상품2",
            slug="product-2",
            category=qa_category,
            price=Decimal("20000"),
            stock=50,
            sku="TEST-002",
            seller=qa_seller,
        )

        ProductQuestion.objects.create(
            product=qa_product,
            user=qa_buyer,
            title="문의1",
            content="내용1",
        )
        ProductQuestion.objects.create(
            product=product2,
            user=qa_buyer,
            title="문의2",
            content="내용2",
        )

        other_user = User.objects.create_user(username="other", email="other@test.com", password="testpass123")
        ProductQuestion.objects.create(
            product=qa_product,
            user=other_user,
            title="다른 사람 문의",
            content="내용",
        )

        url = reverse("my-question-list")

        # Act
        response = authenticated_buyer_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2  # 내 문의 2개만


# ==========================================
# 알림 기능 테스트
# ==========================================


@pytest.fixture
def notification_user(db):
    """알림 테스트용 사용자"""
    return User.objects.create_user(
        username="testuser",
        email="test@test.com",
        password="testpass123",
    )


@pytest.fixture
def notification_fixtures(db, notification_user):
    """알림 테스트용 데이터"""
    notification1 = Notification.objects.create(
        user=notification_user,
        notification_type="qa_answer",
        title="문의 답변",
        message="문의에 답변이 달렸습니다.",
        link="/products/1/questions/1",
    )

    notification2 = Notification.objects.create(
        user=notification_user,
        notification_type="point_earned",
        title="포인트 적립",
        message="500 포인트가 적립되었습니다.",
        is_read=True,
    )

    return {"notification1": notification1, "notification2": notification2}


@pytest.fixture
def authenticated_notification_client(notification_user):
    """알림 테스트용 인증된 클라이언트"""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=notification_user)
    return client


class TestNotification:
    """알림 기능 테스트"""

    def test_unread_count(self, authenticated_notification_client, notification_fixtures):
        """읽지 않은 알림 개수 조회 테스트"""
        # Arrange
        url = reverse("notification-unread")

        # Act
        response = authenticated_notification_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # 읽지 않은 알림 1개

    def test_mark_notification_as_read(self, authenticated_notification_client, notification_fixtures):
        """알림 읽음 처리 테스트"""
        # Arrange
        notification1 = notification_fixtures["notification1"]
        url = reverse("notification-mark-read")
        data = {"notification_ids": [notification1.id]}

        # Act
        response = authenticated_notification_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        notification1.refresh_from_db()
        assert notification1.is_read is True

    def test_mark_all_as_read(self, authenticated_notification_client, notification_user, notification_fixtures):
        """전체 알림 읽음 처리 테스트"""
        # Arrange
        Notification.objects.create(
            user=notification_user,
            notification_type="order_status",
            title="배송 완료",
            message="주문이 배송 완료되었습니다.",
        )
        url = reverse("notification-mark-read")
        data = {"notification_ids": []}  # 빈 배열 = 전체 읽음

        # Act
        response = authenticated_notification_client.post(url, data)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2  # 읽지 않은 것 2개
        unread_count = Notification.objects.filter(user=notification_user, is_read=False).count()
        assert unread_count == 0

    def test_clear_read_notifications(self, authenticated_notification_client, notification_user, notification_fixtures):
        """읽은 알림 삭제 테스트"""
        # Arrange
        url = reverse("notification-clear")

        # Act
        response = authenticated_notification_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # 읽은 알림 1개 삭제
        remaining = Notification.objects.filter(user=notification_user).count()
        assert remaining == 1

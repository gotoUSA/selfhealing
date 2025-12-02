from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.notification import Notification
from shopping.models.product import Category, Product
from shopping.models.product_qa import ProductAnswer, ProductQuestion
from shopping.models.user import User


class ProductQuestionTestCase(TestCase):
    """상품 문의 기능 테스트"""

    def setUp(self):
        """테스트 데이터 생성"""
        self.client = APIClient()

        # 카테고리 생성
        self.category = Category.objects.create(name="테스트 카테고리", slug="test-category")

        # 사용자 생성 (판매자)
        self.seller = User.objects.create_user(username="seller", email="seller@test.com", password="testpass123")

        # 사용자 생성 (구매자)
        self.buyer = User.objects.create_user(username="buyer", email="buyer@test.com", password="testpass123")

        # 상품 생성
        self.product = Product.objects.create(
            name="테스트 상품",
            slug="test-product",
            category=self.category,
            price=Decimal("10000"),
            stock=100,
            sku="TEST-001",
            seller=self.seller,
        )

    def test_create_question(self):
        """문의 작성 테스트"""
        self.client.force_authenticate(user=self.buyer)

        url = reverse("product-question-list", kwargs={"product_pk": self.product.id})
        data = {
            "title": "배송 문의",
            "content": "배송 언제 되나요?",
            "is_secret": False,
        }

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProductQuestion.objects.count(), 1)

        question = ProductQuestion.objects.first()
        self.assertEqual(question.user, self.buyer)
        self.assertEqual(question.product, self.product)
        self.assertEqual(question.title, "배송 문의")

    def test_create_secret_question(self):
        """비밀글 문의 작성 테스트"""
        self.client.force_authenticate(user=self.buyer)

        url = reverse("product-question-list", kwargs={"product_pk": self.product.id})
        data = {"title": "교환 문의", "content": "교환 가능한가요?", "is_secret": True}

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        question = ProductQuestion.objects.first()
        self.assertTrue(question.is_secret)

    def test_list_questions_with_secret(self):
        """비밀글 포함 문의 목록 조회 테스트"""
        # 일반 문의 생성
        ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="일반 문의",
            content="일반 문의 내용",
            is_secret=False,
        )

        # 비밀 문의 생성
        ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="비밀 문의",
            content="비밀 문의 내용",
            is_secret=True,
        )

        url = reverse("product-question-list", kwargs={"product_pk": self.product.id})

        # 1. 비로그인 사용자: 일반 문의만 보임
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

        # 2. 작성자: 모두 보임
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(url)
        self.assertEqual(len(response.data["results"]), 2)

        # 3. 판매자: 모두 보임
        self.client.force_authenticate(user=self.seller)
        response = self.client.get(url)
        self.assertEqual(len(response.data["results"]), 2)

    def test_update_question(self):
        """문의 수정 테스트"""
        # 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # 작성자로 로그인
        self.client.force_authenticate(user=self.buyer)

        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": self.product.id, "pk": question.id},
        )
        data = {"title": "수정된 제목", "content": "수정된 내용"}

        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        question.refresh_from_db()
        self.assertEqual(question.title, "수정된 제목")
        self.assertEqual(question.content, "수정된 내용")

    def test_cannot_update_answered_question(self):
        """답변 달린 문의는 수정 불가 테스트"""
        # 답변이 달린 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        from shopping.services import ProductQAService

        ProductQAService.create_answer(question=question, seller=self.seller, content="내일 출발합니다")

        # 작성자로 로그인
        self.client.force_authenticate(user=self.buyer)

        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": self.product.id, "pk": question.id},
        )
        data = {"title": "수정 시도"}

        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_question(self):
        """문의 삭제 테스트"""
        # 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # 작성자로 로그인
        self.client.force_authenticate(user=self.buyer)

        url = reverse(
            "product-question-detail",
            kwargs={"product_pk": self.product.id, "pk": question.id},
        )

        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ProductQuestion.objects.count(), 0)

    def test_create_answer(self):
        """답변 작성 테스트"""
        # 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # 판매자로 로그인
        self.client.force_authenticate(user=self.seller)

        url = reverse(
            "product-question-answer",
            kwargs={"product_pk": self.product.id, "pk": question.id},
        )
        data = {"content": "내일 출발 예정입니다!"}

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProductAnswer.objects.count(), 1)

        # 문의가 답변 완료 상태로 변경되었는지 확인
        question.refresh_from_db()
        self.assertTrue(question.is_answered)

    def test_answer_notification(self):
        """답변 작성 시 알림 생성 테스트"""
        # 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # 판매자가 답변 작성
        from shopping.services import ProductQAService

        ProductQAService.create_answer(question=question, seller=self.seller, content="내일 출발 예정입니다!")

        # 알림이 생성되었는지 확인
        self.assertEqual(Notification.objects.count(), 1)

        # 알림이 구매자에게 전송되었는지 확인
        notification = Notification.objects.first()
        self.assertEqual(notification.user, self.buyer)
        self.assertEqual(notification.notification_type, "qa_answer")
        self.assertIn("답변", notification.title)
        self.assertFalse(notification.is_read)

    def test_buyer_cannot_answer(self):
        """구매자는 답변 작성 불가 테스트"""
        # 문의 생성
        question = ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="배송 문의",
            content="배송 언제 되나요?",
        )

        # 구매자로 로그인
        self.client.force_authenticate(user=self.buyer)

        url = reverse(
            "product-question-answer",
            kwargs={"product_pk": self.product.id, "pk": question.id},
        )
        data = {"content": "답변입니다"}

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_my_questions(self):
        """내 문의 목록 조회 테스트"""
        # 여러 상품에 문의 작성
        product2 = Product.objects.create(
            name="상품2",
            slug="product-2",
            category=self.category,
            price=Decimal("20000"),
            stock=50,
            sku="TEST-002",
            seller=self.seller,
        )

        ProductQuestion.objects.create(
            product=self.product,
            user=self.buyer,
            title="문의1",
            content="내용1",
        )

        ProductQuestion.objects.create(
            product=product2,
            user=self.buyer,
            title="문의2",
            content="내용2",
        )

        # 다른 사용자 문의
        other_user = User.objects.create_user(username="other", email="other@test.com", password="testpass123")

        ProductQuestion.objects.create(
            product=self.product,
            user=other_user,
            title="다른 사람 문의",
            content="내용",
        )

        # 구매자로 로그인
        self.client.force_authenticate(user=self.buyer)

        url = reverse("my-question-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)  # 내 문의 2개만


class NotificationTestCase(TestCase):
    """알림 기능 테스트"""

    def setUp(self):
        """테스트 데이터 생성"""
        self.client = APIClient()

        # 사용자 생성
        self.user = User.objects.create_user(username="testuser", email="test@test.com", password="testpass123")

        # 알림 생성
        self.notification1 = Notification.objects.create(
            user=self.user,
            notification_type="qa_answer",
            title="문의 답변",
            message="문의에 답변이 달렸습니다.",
            link="/products/1/questions/1",
        )

        self.notification2 = Notification.objects.create(
            user=self.user,
            notification_type="point_earned",
            title="포인트 적립",
            message="500 포인트가 적립되었습니다.",
            is_read=True,
        )

    def test_unread_count(self):
        """읽지 않은 알림 개수 조회 테스트"""
        self.client.force_authenticate(user=self.user)

        url = reverse("notification-unread")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)  # 읽지 않은 알림 1개

    def test_mark_notification_as_read(self):
        """알림 읽음 처리 테스트"""
        self.client.force_authenticate(user=self.user)

        url = reverse("notification-mark-read")
        data = {"notification_ids": [self.notification1.id]}

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

        # DB 확인
        self.notification1.refresh_from_db()
        self.assertTrue(self.notification1.is_read)

    def test_mark_all_as_read(self):
        """전체 알림 읽음 처리 테스트"""
        # 추가 알림 생성
        Notification.objects.create(
            user=self.user,
            notification_type="order_status",
            title="배송 완료",
            message="주문이 배송 완료되었습니다.",
        )

        self.client.force_authenticate(user=self.user)

        url = reverse("notification-mark-read")
        data = {"notification_ids": []}  # 빈 배열 = 전체 읽음

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)  # 읽지 않은 것 2개

        # 모든 알림이 읽음 처리되었는지 확인
        unread_count = Notification.objects.filter(user=self.user, is_read=False).count()
        self.assertEqual(unread_count, 0)

    def test_clear_read_notifications(self):
        """읽은 알림 삭제 테스트"""
        self.client.force_authenticate(user=self.user)

        url = reverse("notification-clear")
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)  # 읽은 알림 1개 삭제

        # DB 확인 (읽지 않은 것만 남음)
        remaining = Notification.objects.filter(user=self.user).count()
        self.assertEqual(remaining, 1)

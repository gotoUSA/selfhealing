"""
ProductQAService 테스트
"""

import pytest
from django.db import IntegrityError

from shopping.models.notification import Notification
from shopping.models.product_qa import ProductAnswer
from shopping.services.product_qa_service import ProductQAService
from shopping.tests.factories import ProductQuestionFactory


@pytest.mark.django_db
class TestProductQAService:
    """ProductQAService 테스트 클래스"""

    def test_create_answer_success(self):
        """답변 생성 성공 테스트"""
        # Arrange
        question = ProductQuestionFactory()
        seller = question.product.seller  # 상품 판매자
        content = "문의하신 내용에 대한 답변입니다."

        # Act
        answer = ProductQAService.create_answer(
            question=question,
            seller=seller,
            content=content
        )

        # Assert
        assert answer.id is not None
        assert answer.content == content
        assert answer.seller == seller
        assert answer.question == question

        # 문의 상태 변경 확인
        question.refresh_from_db()
        assert question.is_answered is True

    def test_create_answer_notification_sent(self):
        """답변 생성 시 알림 발송 테스트"""
        # Arrange
        question = ProductQuestionFactory()
        seller = question.product.seller
        content = "답변입니다."

        # Act
        ProductQAService.create_answer(
            question=question,
            seller=seller,
            content=content
        )

        # Assert
        notifications = Notification.objects.filter(user=question.user)
        assert notifications.exists()

        notification = notifications.first()
        assert notification.notification_type == "qa_answer"
        assert notification.message == f'"{question.title}" 문의에 판매자가 답변했습니다.'
        assert f"/products/{question.product.id}/questions/{question.id}" in notification.link

    def test_create_answer_long_content(self):
        """긴 내용의 답변 생성 테스트"""
        # Arrange
        question = ProductQuestionFactory()
        seller = question.product.seller
        long_content = "A" * 5000  # 5000자 답변

        # Act
        answer = ProductQAService.create_answer(
            question=question,
            seller=seller,
            content=long_content
        )

        # Assert
        assert answer.content == long_content
        assert len(answer.content) == 5000

    def test_create_answer_duplicate_answer_raises_error(self):
        """이미 답변이 있는 질문에 중복 답변 시 IntegrityError 발생 테스트
        
        ProductAnswer.question은 OneToOneField이므로 
        동일 질문에 중복 답변 생성 시 DB 제약조건 위반 발생
        """
        # Arrange
        question = ProductQuestionFactory()
        seller = question.product.seller
        content = "첫 번째 답변입니다."

        # 첫 번째 답변 생성 (성공)
        ProductQAService.create_answer(
            question=question,
            seller=seller,
            content=content
        )

        # Act & Assert
        # 동일 질문에 두 번째 답변 시도 시 IntegrityError 발생
        with pytest.raises(IntegrityError):
            ProductAnswer.objects.create(
                question=question,
                seller=seller,
                content="중복 답변 시도"
            )

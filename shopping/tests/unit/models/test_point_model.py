"""PointHistory 모델 단위 테스트"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.tests.factories import OrderFactory, PointHistoryFactory, UserFactory


@pytest.mark.django_db
class TestPointHistoryLedgerImmutability:
    """PointHistory 원장 불변성 테스트 - delete/save 제한"""

    def test_delete_raises_value_error(self):
        """포인트 이력 삭제 시도 시 ValueError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistoryFactory(user=user, type="earn", points=100, balance=100)

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            history.delete()

        assert "포인트 이력은 삭제할 수 없습니다" in str(exc_info.value)
        assert "반제(Reversal) 거래" in str(exc_info.value)

    def test_save_allows_new_record_creation(self):
        """신규 레코드 생성은 허용"""
        # Arrange
        user = UserFactory()
        history = PointHistory(
            user=user,
            type="earn",
            points=100,
            balance=100,
            description="신규 적립",
        )

        # Act - 새 레코드 생성은 허용되어야 함
        history.save()

        # Assert
        assert history.pk is not None

    def test_save_allows_metadata_update_with_update_fields(self):
        """update_fields로 metadata 업데이트 허용"""
        # Arrange
        user = UserFactory()
        history = PointHistoryFactory(user=user, type="earn", points=100, balance=100)
        history.metadata = {"remaining_points": 50}

        # Act - metadata 업데이트는 허용
        history.save(update_fields=["metadata"])

        # Assert
        history.refresh_from_db()
        assert history.metadata == {"remaining_points": 50}

    def test_save_raises_error_for_core_field_update(self):
        """핵심 필드(points, balance 등) 수정 시도 시 ValueError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistoryFactory(user=user, type="earn", points=100, balance=100)
        history.points = 200  # 핵심 필드 수정 시도

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            history.save(update_fields=["points"])

        assert "핵심 필드는 수정할 수 없습니다" in str(exc_info.value)
        assert "points" in str(exc_info.value)

    def test_save_raises_error_for_balance_update(self):
        """balance 필드 수정 시도 시 ValueError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistoryFactory(user=user, type="earn", points=100, balance=100)
        history.balance = 999

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            history.save(update_fields=["balance"])

        assert "핵심 필드는 수정할 수 없습니다" in str(exc_info.value)

    def test_save_raises_error_for_type_update(self):
        """type 필드 수정 시도 시 ValueError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistoryFactory(user=user, type="earn", points=100, balance=100)
        history.type = "use"

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            history.save(update_fields=["type"])

        assert "핵심 필드는 수정할 수 없습니다" in str(exc_info.value)


@pytest.mark.django_db
class TestPointHistoryManagerOptimizedQuery:
    """PointHistoryManager.optimized_for_list() 최적화 쿼리 테스트"""

    def test_returns_queryset_with_order_prefetched(self, django_assert_num_queries):
        """order select_related 로드로 N+1 쿼리 방지"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)
        PointHistoryFactory(user=user, order=order)
        PointHistoryFactory(user=user, order=order)

        # Act
        with django_assert_num_queries(1):
            histories = list(PointHistory.objects.optimized_for_list())
            for history in histories:
                _ = history.order.order_number if history.order else None

        # Assert - 쿼리 수 검증은 위 context manager에서 수행


@pytest.mark.django_db
class TestPointHistoryCleanValidation:
    """PointHistory.clean() 유효성 검증 테스트"""

    def test_zero_points_raises_validation_error(self):
        """포인트 변동량 0일 때 ValidationError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistory(user=user, points=0, balance=100, type="earn")

        # Act
        with pytest.raises(ValidationError) as exc_info:
            history.full_clean()

        # Assert
        assert "포인트 변동량은 0이 될 수 없습니다" in str(exc_info.value)

    @pytest.mark.parametrize(
        "point_type,display_name",
        [
            ("earn", "적립"),
            ("cancel_refund", "취소환불"),
            ("admin_add", "관리자지급"),
            ("event", "이벤트"),
        ],
    )
    def test_positive_type_with_negative_points_raises_error(self, point_type, display_name):
        """양수 타입에 음수 포인트 설정 시 ValidationError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistory(user=user, points=-100, balance=0, type=point_type)

        # Act
        with pytest.raises(ValidationError) as exc_info:
            history.full_clean()

        # Assert
        assert f"{display_name}는 양수 포인트여야 합니다" in str(exc_info.value)

    @pytest.mark.parametrize(
        "point_type,display_name",
        [
            ("use", "사용"),
            ("cancel_deduct", "취소차감"),
            ("admin_deduct", "관리자차감"),
            ("expire", "만료"),
        ],
    )
    def test_negative_type_with_positive_points_raises_error(self, point_type, display_name):
        """음수 타입에 양수 포인트 설정 시 ValidationError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistory(user=user, points=100, balance=100, type=point_type)

        # Act
        with pytest.raises(ValidationError) as exc_info:
            history.full_clean()

        # Assert
        assert f"{display_name}는 음수 포인트여야 합니다" in str(exc_info.value)

    def test_negative_balance_raises_validation_error(self):
        """음수 잔액일 때 ValidationError 발생"""
        # Arrange
        user = UserFactory()
        history = PointHistory(user=user, points=-100, balance=-50, type="use")

        # Act
        with pytest.raises(ValidationError) as exc_info:
            history.full_clean()

        # Assert
        assert "잔액은 음수가 될 수 없습니다" in str(exc_info.value)


@pytest.mark.django_db
class TestPointHistoryCreateHistoryAutoDescription:
    """PointHistory.create_history() description 자동 생성 테스트"""

    def test_description_includes_order_number_when_order_provided(self):
        """order 제공 시 description에 주문번호 포함"""
        # Arrange
        user = UserFactory()
        order = OrderFactory(user=user)

        # Act
        history = PointHistory.create_history(
            user=user,
            points=100,
            balance=100,
            type="earn",
            order=order,
        )

        # Assert
        assert order.order_number in history.description
        assert "적립" in history.description


@pytest.mark.django_db
class TestPointHistoryGetUserBalance:
    """PointHistory.get_user_balance() 클래스 메서드 테스트"""

    def test_returns_latest_history_balance(self):
        """이력 존재 시 가장 최근 이력의 balance 반환"""
        # Arrange
        user = UserFactory()
        PointHistoryFactory(user=user, points=100, balance=100)
        PointHistoryFactory(user=user, points=200, balance=300)
        latest = PointHistoryFactory(user=user, points=50, balance=350)

        # Act
        balance = PointHistory.get_user_balance(user)

        # Assert
        assert balance == latest.balance

    def test_returns_zero_when_no_history_exists(self):
        """이력 미존재 시 0 반환"""
        # Arrange
        user = UserFactory()

        # Act
        balance = PointHistory.get_user_balance(user)

        # Assert
        assert balance == 0


@pytest.mark.django_db
class TestPointHistoryGetExpiringPoints:
    """PointHistory.get_expiring_points() 클래스 메서드 테스트"""

    def test_returns_expiring_points_within_threshold(self):
        """만료 임계값 내 포인트 정확히 반환"""
        # Arrange
        user = UserFactory()
        now = timezone.now()
        expires_soon = now + timedelta(days=15)
        expires_later = now + timedelta(days=60)
        expiring = PointHistoryFactory(
            user=user,
            type="earn",
            points=500,
            balance=500,
            expires_at=expires_soon,
        )
        PointHistoryFactory(
            user=user,
            type="earn",
            points=300,
            balance=800,
            expires_at=expires_later,
        )

        # Act
        result = PointHistory.get_expiring_points(user, days=30)

        # Assert
        assert result["total_expiring_points"] == expiring.points
        assert result["expiring_histories"].count() == 1

    def test_returns_zero_when_no_expiring_points(self):
        """만료 예정 포인트 미존재 시 0과 None 반환"""
        # Arrange
        user = UserFactory()
        far_future = timezone.now() + timedelta(days=365)
        PointHistoryFactory(user=user, type="earn", expires_at=far_future)

        # Act
        result = PointHistory.get_expiring_points(user, days=30)

        # Assert
        assert result["total_expiring_points"] == 0
        assert result["earliest_expire_date"] is None

    def test_returns_earliest_expire_date(self):
        """여러 만료 예정 포인트 중 가장 빠른 만료일 반환"""
        # Arrange
        user = UserFactory()
        now = timezone.now()
        earliest = now + timedelta(days=5)
        middle = now + timedelta(days=15)
        latest = now + timedelta(days=25)
        PointHistoryFactory(user=user, type="earn", points=100, balance=100, expires_at=middle)
        PointHistoryFactory(user=user, type="earn", points=200, balance=300, expires_at=earliest)
        PointHistoryFactory(user=user, type="earn", points=150, balance=450, expires_at=latest)

        # Act
        result = PointHistory.get_expiring_points(user, days=30)

        # Assert
        assert result["total_expiring_points"] == 450
        assert result["earliest_expire_date"] == earliest
        assert result["expiring_histories"].count() == 3

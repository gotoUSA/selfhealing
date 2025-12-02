"""
PointService 단위 테스트 - FIFO 포인트 사용

만료일 순서로 포인트를 소진하는 FIFO(First In, First Out) 로직과
최소 사용 금액, cancel_deduct 타입 등을 테스트합니다.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# FIFO 포인트 사용 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceUseFIFO:
    """FIFO 포인트 사용 테스트"""

    def test_use_points_fifo_success(self):
        """단일 적립 건에서 FIFO 사용"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # 포인트 적립
        PointService.add_points(user, 1000, type="earn")

        # Act
        result = service.use_points_fifo(user, 300, type="use")

        # Assert - 결과 확인
        assert result["success"] is True
        assert result["message"] == "300 포인트를 사용했습니다."
        assert len(result["used_details"]) == 1
        assert result["used_details"][0]["amount"] == 300

        # Assert - 사용자 포인트 감소
        user.refresh_from_db()
        assert user.points == 700

    def test_use_points_fifo_multiple_histories(self):
        """여러 적립 건에서 FIFO 사용"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # 여러 적립 건 생성 (만료일 다름)
        PointService.add_points(user, 100, type="earn")
        PointService.add_points(user, 200, type="earn")
        PointService.add_points(user, 300, type="earn")

        # Act
        result = service.use_points_fifo(user, 250, type="use")

        # Assert - 여러 적립 건에서 차감
        assert result["success"] is True
        assert len(result["used_details"]) == 2  # 첫 번째 100, 두 번째 150
        assert result["used_details"][0]["amount"] == 100
        assert result["used_details"][1]["amount"] == 150

        # Assert - 사용자 포인트 감소
        user.refresh_from_db()
        assert user.points == 350  # 600 - 250

    def test_use_points_fifo_order_by_expiry(self):
        """만료일 순서로 사용 확인"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # 만료일이 다른 적립 건 생성
        later_expiry = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=30),
        )
        earlier_expiry = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=10),
        )

        user.points = 300
        user.save()

        # Act
        result = service.use_points_fifo(user, 150, type="use")

        # Assert - 먼저 만료되는 것부터 사용
        assert result["success"] is True
        assert result["used_details"][0]["history_id"] == earlier_expiry.id
        assert result["used_details"][0]["amount"] == 100
        assert result["used_details"][1]["history_id"] == later_expiry.id
        assert result["used_details"][1]["amount"] == 50

    def test_use_points_fifo_insufficient_balance(self):
        """잔액 부족 시 FIFO 사용 실패"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()

        # Act
        result = service.use_points_fifo(user, 500, type="use")

        # Assert
        assert result["success"] is False
        assert result["message"] == "포인트가 부족합니다."
        assert len(result["used_details"]) == 0

    def test_use_points_fifo_zero_amount(self):
        """0 포인트 사용 시도"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # Act
        result = service.use_points_fifo(user, 0, type="use")

        # Assert
        assert result["success"] is False
        assert result["message"] == "사용할 포인트는 0보다 커야 합니다."

    def test_use_points_fifo_metadata_tracking(self):
        """메타데이터 추적 확인"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # 포인트 적립
        PointService.add_points(user, 1000, type="earn")
        point_history = PointHistory.objects.filter(user=user, type="earn").first()

        # Act
        service.use_points_fifo(user, 300, type="use")

        # Assert - 적립 이력의 메타데이터 추적
        point_history.refresh_from_db()
        assert point_history.metadata.get("used_amount") == 300
        assert "usage_history" in point_history.metadata
        assert len(point_history.metadata["usage_history"]) == 1
        assert point_history.metadata["usage_history"][0]["amount"] == 300

    def test_use_points_fifo_response_structure(self):
        """응답 구조 확인"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        PointService.add_points(user, 1000, type="earn")

        # Act
        result = service.use_points_fifo(user, 300, type="use")

        # Assert - 응답 구조 확인
        assert "success" in result
        assert "used_details" in result
        assert "message" in result
        assert isinstance(result["used_details"], list)
        if result["used_details"]:
            detail = result["used_details"][0]
            assert "history_id" in detail
            assert "amount" in detail
            assert "expires_at" in detail


# ==========================================
# 최소 사용 금액 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceUseFIFOMinimumAmount:
    """FIFO 포인트 사용 - 최소 금액 검증 (100포인트)"""

    def test_use_below_minimum_100_fails(self):
        """100포인트 미만 사용 실패"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(user=user, points=1000, balance=user.points)

        # Act
        result = service.use_points_fifo(user, 99, type="use")

        # Assert
        assert result["success"] is False
        assert "최소" in result["message"]
        assert "100" in result["message"]
        user.refresh_from_db()
        assert user.points == 1000

    def test_use_exactly_100_succeeds(self):
        """정확히 100포인트 사용 성공 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(user=user, points=1000, balance=user.points)

        # Act
        result = service.use_points_fifo(user, 100, type="use")

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 900

    def test_use_101_succeeds(self):
        """101포인트 사용 성공 (경계값+1)"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(user=user, points=1000, balance=user.points)

        # Act
        result = service.use_points_fifo(user, 101, type="use")

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 899

    def test_cancel_deduct_below_100_succeeds(self):
        """cancel_deduct는 최소 금액 제한 없음"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act - 50포인트 회수 (100 미만이지만 성공해야 함)
        result = service.use_points_fifo(user, 50, type="cancel_deduct")

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 950

    def test_cancel_deduct_1_point_succeeds(self):
        """cancel_deduct 1포인트도 가능 (극단적 경계값)"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()
        PointHistoryFactory.earn(
            user=user,
            points=100,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act
        result = service.use_points_fifo(user, 1, type="cancel_deduct")

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 99


# ==========================================
# cancel_deduct 타입 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceUseFIFOCancelDeduct:
    """FIFO 포인트 사용 - cancel_deduct (취소 회수) 타입"""

    def test_cancel_deduct_excludes_expired_points(self):
        """cancel_deduct는 만료된 포인트 제외"""
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()

        # 만료된 포인트 1000P
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 유효한 포인트 1000P
        valid_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act - 500P 회수
        result = service.use_points_fifo(user, 500, type="cancel_deduct")

        # Assert - 유효한 포인트에서만 회수
        assert result["success"] is True
        assert len(result["used_details"]) == 1
        assert result["used_details"][0]["history_id"] == valid_point.id

        user.refresh_from_db()
        assert user.points == 1500

    def test_cancel_deduct_insufficient_valid_points(self):
        """cancel_deduct - 유효한 포인트 부족 에러"""
        # Arrange
        user = UserFactory.with_points(1500)
        service = PointService()

        # 만료된 포인트 1000P
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 유효한 포인트 500P
        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=1500,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act - 800P 회수 시도 (유효한 포인트는 500P뿐)
        result = service.use_points_fifo(user, 800, type="cancel_deduct")

        # Assert
        assert result["success"] is False
        assert "유효한 포인트가 부족" in result["message"]
        assert "필요: 800" in result["message"]
        assert "사용 가능: 500" in result["message"]

        user.refresh_from_db()
        assert user.points == 1500  # 변화 없음

    def test_cancel_deduct_uses_only_unexpired_fifo(self):
        """cancel_deduct - 미만료 포인트만 FIFO 순서로 사용"""
        # Arrange
        user = UserFactory.with_points(3000)
        service = PointService()
        now = timezone.now()

        # 만료된 포인트 (제외됨)
        expired = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now - timedelta(days=1),
        )

        # 유효한 포인트 - 먼저 만료 (FIFO 1순위)
        valid_soon = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=10),
        )

        # 유효한 포인트 - 나중에 만료 (FIFO 2순위)
        valid_later = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=3000,
            expires_at=now + timedelta(days=100),
        )

        # Act - 1500P 회수
        result = service.use_points_fifo(user, 1500, type="cancel_deduct")

        # Assert
        assert result["success"] is True
        assert len(result["used_details"]) == 2

        # Assert - 만료 임박 포인트 먼저 사용
        assert result["used_details"][0]["history_id"] == valid_soon.id
        assert result["used_details"][0]["amount"] == 1000

        # Assert - 다음 포인트에서 나머지 사용
        assert result["used_details"][1]["history_id"] == valid_later.id
        assert result["used_details"][1]["amount"] == 500

        # Assert - 만료된 포인트는 사용되지 않음
        expired.refresh_from_db()
        assert expired.metadata.get("used_amount", 0) == 0

    def test_cancel_deduct_exact_amount_success(self):
        """cancel_deduct - 정확히 유효 포인트만큼 회수"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act - 정확히 1000P 회수
        result = service.use_points_fifo(user, 1000, type="cancel_deduct")

        # Assert
        assert result["success"] is True
        user.refresh_from_db()
        assert user.points == 0


# ==========================================
# 응답 구조 상세 검증
# ==========================================


@pytest.mark.django_db
class TestPointServiceUseFIFOResponseStructure:
    """FIFO 포인트 사용 - 응답 구조 상세 검증"""

    def test_success_response_structure(self):
        """성공 응답 구조 검증"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act
        result = service.use_points_fifo(user, 500, type="use")

        # Assert - 응답 구조
        assert result["success"] is True
        assert result["message"] == "500 포인트를 사용했습니다."
        assert len(result["used_details"]) == 1

        # Assert - 상세 정보
        detail = result["used_details"][0]
        assert detail["history_id"] == point.id
        assert detail["amount"] == 500
        assert "expires_at" in detail

    def test_failure_response_zero_amount(self):
        """0 포인트 사용 실패 응답"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # Act
        result = service.use_points_fifo(user, 0, type="use")

        # Assert
        assert result["success"] is False
        assert result["used_details"] == []
        assert "0보다 커야" in result["message"]

    def test_failure_response_insufficient_balance(self):
        """잔액 부족 실패 응답"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()

        # Act
        result = service.use_points_fifo(user, 500, type="use")

        # Assert
        assert result["success"] is False
        assert result["used_details"] == []
        assert "부족" in result["message"]

    def test_used_details_multiple_histories(self):
        """여러 적립 건 사용 시 상세 정보"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()
        now = timezone.now()

        point1 = PointHistoryFactory.earn(
            user=user,
            points=200,
            balance=200,
            expires_at=now + timedelta(days=10),
        )
        point2 = PointHistoryFactory.earn(
            user=user,
            points=300,
            balance=500,
            expires_at=now + timedelta(days=20),
        )

        # Act
        result = service.use_points_fifo(user, 400, type="use")

        # Assert - 여러 건 사용 상세
        assert result["success"] is True
        assert len(result["used_details"]) == 2

        # 첫 번째: 200P 전액
        assert result["used_details"][0]["history_id"] == point1.id
        assert result["used_details"][0]["amount"] == 200

        # 두 번째: 200P 일부
        assert result["used_details"][1]["history_id"] == point2.id
        assert result["used_details"][1]["amount"] == 200


# ==========================================
# FIFO 사용 이력 추적 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceFIFOHistoryTracking:
    """FIFO 사용 이력 추적 테스트"""

    def test_use_points_creates_history_with_negative_points(self):
        """포인트 사용 시 음수 이력 생성"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(user=user, points=1000, balance=user.points)

        # Act
        service.use_points_fifo(user, 300, type="use")

        # Assert - 음수 이력 생성
        use_history = PointHistory.objects.filter(user=user, type="use").first()
        assert use_history is not None
        assert use_history.points == -300
        assert use_history.balance == 700

    def test_use_points_fifo_metadata_tracks_used_details(self):
        """FIFO 사용 이력에 used_details 메타데이터 기록"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act
        service.use_points_fifo(user, 500, type="use")

        # Assert - 사용 이력의 메타데이터
        use_history = PointHistory.objects.filter(user=user, type="use").first()
        assert "used_details" in use_history.metadata
        assert len(use_history.metadata["used_details"]) == 1
        assert use_history.metadata["used_details"][0]["history_id"] == point.id

    def test_earn_history_usage_tracking_accumulates(self):
        """적립 이력의 사용량 누적 추적"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act - 여러 번 사용
        service.use_points_fifo(user, 200, type="use")
        service.use_points_fifo(user, 150, type="use")
        service.use_points_fifo(user, 100, type="use")

        # Assert - 누적 사용량
        point.refresh_from_db()
        assert point.metadata.get("used_amount") == 450
        assert len(point.metadata.get("usage_history", [])) == 3

    def test_use_points_with_custom_metadata(self):
        """커스텀 메타데이터와 함께 포인트 사용"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        order = OrderFactory(user=user)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        custom_metadata = {"promotion_id": 123, "campaign": "winter_sale"}

        # Act
        service.use_points_fifo(
            user,
            300,
            type="use",
            order=order,
            metadata=custom_metadata,
        )

        # Assert - 사용 이력에 커스텀 메타데이터 포함
        use_history = PointHistory.objects.filter(user=user, type="use").first()
        assert use_history.order == order
        assert use_history.metadata.get("promotion_id") == 123
        assert use_history.metadata.get("campaign") == "winter_sale"
        # used_details도 함께 저장되어야 함
        assert "used_details" in use_history.metadata


# ==========================================
# 사용 가능 포인트 조회 테스트
# ==========================================


@pytest.mark.django_db
class TestGetUsablePoints:
    """
    get_usable_points 메서드 테스트

    원장(PointHistory) 기준으로 실제 사용 가능한 포인트 계산 검증
    """

    def test_get_usable_points_basic(self):
        """기본 사용 가능 포인트 조회"""
        # Arrange
        user = UserFactory.with_points(1000)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() + timedelta(days=365),
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert
        assert usable == 1000

    def test_get_usable_points_excludes_expired(self):
        """만료된 포인트 제외 확인"""
        # Arrange
        user = UserFactory.with_points(1500)

        # 유효한 포인트
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() + timedelta(days=365),
        )
        # 만료된 포인트
        PointHistoryFactory.earn_expired(
            user=user,
            points=500,
            balance=1500,
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user, for_cancel=True)

        # Assert - 만료된 500P 제외
        assert usable == 1000

    def test_get_usable_points_with_partial_usage(self):
        """부분 사용된 포인트 계산"""
        # Arrange
        user = UserFactory.with_points(1000)
        PointHistoryFactory.with_partial_usage(
            user=user,
            points=1000,
            balance=1000,
            used_amount=300,  # 300P 이미 사용
            expires_at=timezone.now() + timedelta(days=365),
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert - 1000 - 300 = 700
        assert usable == 700

    def test_get_usable_points_multiple_earn_records(self):
        """여러 적립 건의 합계 계산"""
        # Arrange
        user = UserFactory.with_points(2500)

        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() + timedelta(days=365),
        )
        PointHistoryFactory.earn(
            user=user,
            points=1500,
            balance=2500,
            expires_at=timezone.now() + timedelta(days=365),
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert
        assert usable == 2500

    def test_get_usable_points_for_cancel_excludes_expired(self):
        """cancel_deduct용 조회는 만료된 포인트 제외"""
        # Arrange
        user = UserFactory.with_points(2000)

        # 유효한 포인트
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() + timedelta(days=365),
        )
        # 만료된 포인트 (for_cancel=True일 때 제외)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=timezone.now() - timedelta(days=1),
        )
        service = PointService()

        # Act
        usable_for_cancel = service.get_usable_points(user, for_cancel=True)
        usable_for_normal = service.get_usable_points(user, for_cancel=False)

        # Assert
        assert usable_for_cancel == 1000  # 만료된 것 제외
        assert usable_for_normal == 2000  # 전체 (만료 포함)

    def test_get_usable_points_excludes_expired_flag_in_metadata(self):
        """metadata에 expired=True인 포인트 제외"""
        # Arrange
        user = UserFactory.with_points(1500)

        # 정상 포인트
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() + timedelta(days=365),
        )
        # metadata에 expired=True 표시된 포인트
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=1500,
            expires_at=timezone.now() + timedelta(days=365),
        )
        expired_point.metadata = {"expired": True}
        expired_point.save(update_fields=["metadata"])

        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert
        assert usable == 1000

    def test_get_usable_points_zero_when_no_earn_records(self):
        """적립 이력이 없으면 0 반환"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert
        assert usable == 0

    def test_get_usable_points_zero_when_all_used(self):
        """모든 포인트가 사용된 경우 0 반환"""
        # Arrange
        user = UserFactory.with_points(0)
        PointHistoryFactory.with_partial_usage(
            user=user,
            points=1000,
            balance=0,
            used_amount=1000,  # 전부 사용됨
            expires_at=timezone.now() + timedelta(days=365),
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user)

        # Assert
        assert usable == 0

    def test_get_usable_points_complex_scenario(self):
        """
        복합 시나리오: 유효/만료/부분사용 혼합

        적립 1: 1000P, 300P 사용됨, 유효 → 남은 700P
        적립 2: 500P, 만료됨 → 0P (for_cancel에서 제외)
        적립 3: 200P, 전부 사용됨 → 0P
        예상: 700P
        """
        # Arrange
        user = UserFactory.with_points(700)

        # 적립 1: 부분 사용
        PointHistoryFactory.with_partial_usage(
            user=user,
            points=1000,
            balance=1000,
            used_amount=300,
            expires_at=timezone.now() + timedelta(days=365),
        )
        # 적립 2: 만료됨
        PointHistoryFactory.earn_expired(
            user=user,
            points=500,
            balance=1500,
        )
        # 적립 3: 전부 사용
        PointHistoryFactory.with_partial_usage(
            user=user,
            points=200,
            balance=1700,
            used_amount=200,
            expires_at=timezone.now() + timedelta(days=365),
        )
        service = PointService()

        # Act
        usable = service.get_usable_points(user, for_cancel=True)

        # Assert
        assert usable == 700

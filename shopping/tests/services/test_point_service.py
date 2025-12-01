"""PointService 단위 테스트"""

import logging
import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestPointServiceAddPoints:
    """포인트 추가 테스트"""

    def test_add_points_success(self):
        """정상적으로 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(user, 500, type="earn", description="테스트 적립")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == initial_points + 500

        # 이력 확인
        history = PointHistory.objects.filter(user=user, type="earn").latest("created_at")
        assert history.points == 500
        assert history.balance == user.points
        assert history.description == "테스트 적립"

    def test_add_points_with_order(self):
        """주문 연관 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user)

        # Act
        result = PointService.add_points(user, 100, type="earn", order=order, description="주문 적립")

        # Assert
        assert result is True
        history = PointHistory.objects.filter(user=user, order=order).first()
        assert history is not None
        assert history.order == order

    def test_add_points_with_metadata(self):
        """메타데이터 포함 포인트 추가"""
        # Arrange
        user = UserFactory.with_points(1000)
        metadata = {"event": "welcome_bonus", "campaign_id": 123}

        # Act
        result = PointService.add_points(user, 500, type="event", metadata=metadata)

        # Assert
        assert result is True
        history = PointHistory.objects.filter(user=user, type="event").first()
        assert history.metadata == metadata

    def test_add_points_zero_amount(self):
        """0 포인트 추가 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(user, 0)

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == initial_points

    def test_add_points_negative_amount(self):
        """음수 포인트 추가 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.add_points(user, -100)

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == initial_points

    def test_add_points_logging(self, caplog):
        """포인트 추가 시 로깅 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.point_service")
        user = UserFactory.with_points(1000)

        # Act
        PointService.add_points(user, 500, type="earn", description="테스트 적립")

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("포인트 추가" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("amount=500" in msg for msg in log_messages)


@pytest.mark.django_db(transaction=True)
class TestPointServiceAddPointsConcurrency:
    """포인트 추가 동시성 테스트"""

    def test_add_points_concurrency(self):
        """여러 스레드에서 동시 포인트 추가 (F() 객체 사용 검증)"""
        # Arrange
        user = UserFactory.with_points(1000)
        results = []
        lock = threading.Lock()

        def add_points_thread():
            try:
                success = PointService.add_points(user, 100, type="earn")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=add_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        user.refresh_from_db()
        assert user.points == 1000 + (100 * 5)


@pytest.mark.django_db
class TestPointServiceUsePoints:
    """포인트 차감 테스트"""

    def test_use_points_success(self):
        """정상적으로 포인트 차감"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, 300, type="use", description="테스트 사용")

        # Assert
        assert result is True
        user.refresh_from_db()
        assert user.points == initial_points - 300

        # 이력 확인
        history = PointHistory.objects.filter(user=user, type="use").latest("created_at")
        assert history.points == -300
        assert history.balance == user.points

    def test_use_points_with_order(self):
        """주문 연관 포인트 차감"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user)

        # Act
        result = PointService.use_points(user, 300, type="use", order=order, description="주문 사용")

        # Assert
        assert result is True
        history = PointHistory.objects.filter(user=user, order=order, type="use").first()
        assert history is not None
        assert history.order == order

    def test_use_points_insufficient_balance(self):
        """잔액 부족 시 포인트 차감 실패"""
        # Arrange
        user = UserFactory.with_points(100)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, 500)

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == initial_points

    def test_use_points_zero_amount(self):
        """0 포인트 차감 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, 0)

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == initial_points

    def test_use_points_negative_amount(self):
        """음수 포인트 차감 시도 (경계값)"""
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        # Act
        result = PointService.use_points(user, -100)

        # Assert
        assert result is False
        user.refresh_from_db()
        assert user.points == initial_points

    def test_use_points_logging(self, caplog):
        """포인트 차감 시 로깅 확인"""
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.point_service")
        user = UserFactory.with_points(1000)

        # Act
        PointService.use_points(user, 300, type="use", description="테스트 사용")

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("포인트 차감" in msg for msg in log_messages)
        assert any(f"user_id={user.id}" in msg for msg in log_messages)
        assert any("amount=300" in msg for msg in log_messages)


@pytest.mark.django_db(transaction=True)
class TestPointServiceUsePointsConcurrency:
    """포인트 차감 동시성 테스트"""

    def test_use_points_concurrency(self):
        """여러 스레드에서 동시 포인트 차감 (select_for_update 검증)"""
        # Arrange
        user = UserFactory.with_points(1000)
        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                success = PointService.use_points(user, 100, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=use_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        user.refresh_from_db()
        assert user.points == 1000 - (100 * 5)

    def test_use_points_concurrency_insufficient(self):
        """잔액 부족 시 동시 차감 (일부만 성공)"""
        # Arrange
        user = UserFactory.with_points(250)
        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                success = PointService.use_points(user, 100, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=use_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 2  # 250 / 100 = 2개만 성공

        user.refresh_from_db()
        assert user.points == 50

    def test_use_points_concurrency_20_users(self):
        """20명 동시 포인트 사용 - 중간 스케일 검증"""
        # Arrange
        user = UserFactory.with_points(20_000)
        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                success = PointService.use_points(user, 1000, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=use_points_thread) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 20

        user.refresh_from_db()
        assert user.points == 0

        # 이력 검증
        histories = PointHistory.objects.filter(user=user, type="use").order_by("created_at")
        assert histories.count() == 20
        assert all(h.points == -1000 for h in histories)


@pytest.mark.django_db
class TestPointServiceExpiredPoints:
    """만료 포인트 조회 테스트"""

    def test_get_expired_points_found(self):
        """만료된 포인트 조회 성공"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 1
        assert expired_points[0].id == expired_point.id

    def test_get_expired_points_none(self):
        """만료된 포인트 없음"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 유효한 포인트만 생성
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 0

    def test_get_expired_points_exclude_already_expired(self):
        """이미 만료 처리된 포인트 제외"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 이미 만료 처리된 포인트
        already_expired = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        already_expired.metadata["expired"] = True
        already_expired.save(update_fields=["metadata"])

        # 새로 만료된 포인트
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 1
        assert expired_points[0].points == 200

    def test_get_expired_points_only_earn_type(self):
        """earn 타입만 조회"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()

        # earn 타입 만료
        PointHistoryFactory.earn(
            user=user,
            points=100,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # use 타입 (만료 대상 아님)
        PointHistoryFactory(
            user=user,
            type="use",
            points=-50,
            balance=user.points - 50,
        )

        # Act
        expired_points = service.get_expired_points()

        # Assert
        assert len(expired_points) == 1
        assert expired_points[0].type == "earn"


@pytest.mark.django_db
class TestPointServiceExpiringPointsSoon:
    """만료 예정 포인트 조회 테스트"""

    def test_get_expiring_points_soon_default_7days(self):
        """7일 이내 만료 예정 포인트 조회 (기본값)"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 7일 이내 만료 예정
        expiring_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # 7일 이후 만료
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=10),
        )

        # Act
        expiring_points = service.get_expiring_points_soon()

        # Assert
        assert len(expiring_points) == 1
        assert expiring_points[0].id == expiring_point.id

    def test_get_expiring_points_soon_custom_days(self):
        """커스텀 일수로 만료 예정 포인트 조회"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 30일 이내 만료 예정
        expiring_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=20),
        )

        # Act
        expiring_points = service.get_expiring_points_soon(days=30)

        # Assert
        assert len(expiring_points) == 1
        assert expiring_points[0].id == expiring_point.id

    def test_get_expiring_points_soon_exclude_notified(self):
        """이미 알림 보낸 포인트 제외"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 알림 보낸 포인트
        notified_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        notified_point.metadata["expiry_notified"] = True
        notified_point.save(update_fields=["metadata"])

        # 알림 안 보낸 포인트
        not_notified = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        expiring_points = service.get_expiring_points_soon()

        # Assert
        assert len(expiring_points) == 1
        assert expiring_points[0].id == not_notified.id

    def test_get_expiring_points_soon_none(self):
        """만료 예정 포인트 없음"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 유효기간이 충분한 포인트
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=100),
        )

        # Act
        expiring_points = service.get_expiring_points_soon()

        # Assert
        assert len(expiring_points) == 0


@pytest.mark.django_db
class TestPointServiceExpirePoints:
    """만료 처리 테스트"""

    def test_expire_points_success(self):
        """만료 포인트 처리 성공"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        count = service.expire_points()

        # Assert
        assert count == 1

        user.refresh_from_db()
        assert user.points == 900  # 1000 - 100

        # 만료 이력 확인
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None
        assert expire_history.points == -100

        # 원본 이력에 만료 표시
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True

    def test_expire_points_multiple(self):
        """여러 건 만료 처리"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 여러 만료 포인트 생성
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() - timedelta(days=2),
        )

        # Act
        count = service.expire_points()

        # Assert
        assert count == 2

        user.refresh_from_db()
        assert user.points == 700  # 1000 - 100 - 200

    def test_expire_points_partial_used(self):
        """부분 사용된 포인트 만료"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 부분 사용된 포인트
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        expired_point.metadata["used_amount"] = 30
        expired_point.save(update_fields=["metadata"])

        # Act
        count = service.expire_points()

        # Assert
        assert count == 1

        user.refresh_from_db()
        assert user.points == 930  # 1000 - (100 - 30)

        # 만료 이력 확인
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history.points == -70  # 남은 포인트만 만료

    def test_expire_points_fully_used(self):
        """전액 사용된 포인트는 만료 처리 안 함"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 전액 사용된 포인트
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        expired_point.metadata["used_amount"] = 100
        expired_point.save(update_fields=["metadata"])

        # Act
        count = service.expire_points()

        # Assert
        assert count == 0

        user.refresh_from_db()
        assert user.points == 1000  # 변화 없음

    def test_expire_points_user_balance_update(self):
        """사용자 잔액 업데이트 확인"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert
        user.refresh_from_db()
        assert user.points == 300

    def test_expire_points_metadata_update(self):
        """메타데이터 업데이트 확인"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        expired_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True
        assert "expired_at" in expired_point.metadata
        assert expired_point.metadata.get("expired_amount") == 100
        assert expired_point.metadata.get("original_points") is None  # 원본 포인트는 기록 안 함

    def test_expire_points_error_handling(self, caplog):
        """만료 처리 중 에러 발생 시 continue"""
        # Arrange
        caplog.set_level(logging.ERROR, logger="shopping.services.point_service")
        user = UserFactory.with_points(1000)
        service = PointService()

        # 만료된 포인트 2개 생성
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        valid_expired = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        count = service.expire_points()

        # Assert
        assert count == 2  # 에러 발생해도 나머지는 처리


@pytest.mark.django_db(transaction=True)
class TestPointServiceExpirePointsConcurrency:
    """만료 처리 동시성 테스트"""

    def test_expire_points_concurrency(self):
        """여러 스레드에서 동시 만료 처리 (중복 방지 검증)"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )

        results = []
        lock = threading.Lock()

        def expire_points_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [threading.Thread(target=expire_points_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_count = sum(r.get("count", 0) for r in results)
        assert total_count == 1  # 중복 처리 방지

        user.refresh_from_db()
        assert user.points == 900


@pytest.mark.django_db
class TestPointServiceGetRemainingPoints:
    """남은 포인트 계산 테스트"""

    def test_get_remaining_points_full(self):
        """전액 남은 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()

        point_history = PointHistoryFactory.earn(user=user, points=100)

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 100

    def test_get_remaining_points_partial(self):
        """부분 사용된 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()

        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata["used_amount"] = 30
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 70

    def test_get_remaining_points_fully_used(self):
        """전액 사용된 포인트"""
        # Arrange
        user = UserFactory()
        service = PointService()

        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata["used_amount"] = 100
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 0

    def test_get_remaining_points_non_earn_type(self):
        """비적립 타입은 0 반환"""
        # Arrange
        user = UserFactory.with_points(100)
        service = PointService()

        point_history = PointHistoryFactory(
            user=user,
            type="use",
            points=-50,
            balance=user.points - 50,
        )

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 0

    def test_get_remaining_points_no_metadata(self):
        """메타데이터 없는 경우 전액 반환"""
        # Arrange
        user = UserFactory()
        service = PointService()

        point_history = PointHistoryFactory.earn(user=user, points=100)
        point_history.metadata = {}
        point_history.save(update_fields=["metadata"])

        # Act
        remaining = service.get_remaining_points(point_history)

        # Assert
        assert remaining == 100


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

        # Assert
        assert result["success"] is True
        assert result["message"] == "300 포인트를 사용했습니다."
        assert len(result["used_details"]) == 1
        assert result["used_details"][0]["amount"] == 300

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

        # Assert
        assert result["success"] is True
        assert len(result["used_details"]) == 2  # 첫 번째 100, 두 번째 150
        assert result["used_details"][0]["amount"] == 100
        assert result["used_details"][1]["amount"] == 150

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

        # Assert
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

        # Assert
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

        # Assert
        assert "success" in result
        assert "used_details" in result
        assert "message" in result
        assert isinstance(result["used_details"], list)
        if result["used_details"]:
            detail = result["used_details"][0]
            assert "history_id" in detail
            assert "amount" in detail
            assert "expires_at" in detail


@pytest.mark.django_db(transaction=True)
class TestPointServiceUseFIFOConcurrency:
    """FIFO 포인트 사용 동시성 테스트"""

    def test_use_points_fifo_concurrency(self):
        """여러 스레드에서 동시 FIFO 사용 (select_for_update 검증)"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()

        # 충분한 포인트 적립
        PointService.add_points(user, 1000, type="earn")

        results = []
        lock = threading.Lock()

        def use_fifo_thread():
            try:
                result = service.use_points_fifo(user, 100, type="use")
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"success": False, "error": str(e)})

        # Act
        threads = [threading.Thread(target=use_fifo_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        user.refresh_from_db()
        assert user.points == 500  # 1000 - (100 * 5)


@pytest.mark.django_db
class TestPointServiceNotifications:
    """알림 발송 테스트"""

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_success(self, mock_send_email):
        """만료 예정 알림 발송 성공"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 만료 예정 포인트 생성
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 1
        assert mock_send_email.called
        assert mock_send_email.call_count == 1

        # 알림 발송 표시 확인
        point_history = PointHistory.objects.filter(user=user, type="earn").first()
        assert point_history.metadata.get("expiry_notified") is True
        assert "notified_at" in point_history.metadata

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_multiple_users(self, mock_send_email):
        """여러 사용자에게 알림 발송"""
        # Arrange
        service = PointService()

        user1 = UserFactory(email="user1@example.com")
        user2 = UserFactory(email="user2@example.com")

        PointHistoryFactory.earn(
            user=user1,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        PointHistoryFactory.earn(
            user=user2,
            points=200,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 2
        assert mock_send_email.call_count == 2

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_grouping(self, mock_send_email):
        """사용자별 그룹화 확인"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 같은 사용자의 여러 만료 예정 포인트
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=6),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 1  # 사용자별로 1번만 발송
        assert mock_send_email.call_count == 1

        # 이메일 내용 확인
        call_args = mock_send_email.call_args
        assert "300" in call_args[0][1]  # subject에 총 포인트

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_metadata_update(self, mock_send_email):
        """메타데이터 업데이트 확인"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        point_history = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        service.send_expiry_notifications()

        # Assert
        point_history.refresh_from_db()
        assert point_history.metadata.get("expiry_notified") is True
        assert "notified_at" in point_history.metadata

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_error_handling(self, mock_send_email, caplog):
        """알림 발송 실패 시 에러 처리"""
        # Arrange
        caplog.set_level(logging.ERROR, logger="shopping.services.point_service")
        user = UserFactory(email="test@example.com")
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # 이메일 발송 실패 시뮬레이션
        mock_send_email.side_effect = Exception("Email send failed")

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 0  # 실패 시 카운트 안 함
        log_messages = [record.message for record in caplog.records]
        assert any("알림 발송 실패" in msg for msg in log_messages)

    def test_create_expiry_notification_message(self):
        """만료 알림 메시지 생성"""
        # Arrange
        user = UserFactory(username="testuser")
        service = PointService()

        point1 = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        point2 = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=10),
        )

        points = [point1, point2]
        total = 300

        # Act
        message = service._create_expiry_notification_message(user, points, total)

        # Assert
        assert "testuser" in message
        assert "300" in message
        assert "100" in message
        assert "200" in message
        assert "만료" in message


# =============================================================================
# 최소 사용 금액 테스트
# =============================================================================


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


# =============================================================================
# cancel_deduct 타입 테스트
# =============================================================================


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

        # Assert
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

        # 만료 임박 포인트 먼저 사용
        assert result["used_details"][0]["history_id"] == valid_soon.id
        assert result["used_details"][0]["amount"] == 1000

        # 다음 포인트에서 나머지 사용
        assert result["used_details"][1]["history_id"] == valid_later.id
        assert result["used_details"][1]["amount"] == 500

        # 만료된 포인트는 사용되지 않음
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


# =============================================================================
# 만료 처리 메타데이터 테스트
# =============================================================================


@pytest.mark.django_db
class TestPointServiceExpirePointsMetadata:
    """만료 처리 - 메타데이터 상세 검증"""

    def test_expire_metadata_contains_original_history_id(self):
        """만료 이력에 원본 history_id 기록"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None
        assert expire_history.metadata.get("original_history_id") == expired_point.id

    def test_expire_metadata_contains_original_points(self):
        """만료 이력에 원본 적립 포인트 기록"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        expired_point = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history.metadata.get("original_points") == 500

    def test_expire_metadata_contains_expired_amount(self):
        """만료 이력에 실제 만료된 금액 기록"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 1000P 적립 후 300P 사용
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )
        expired_point.metadata["used_amount"] = 300
        expired_point.save(update_fields=["metadata"])

        # Act
        service.expire_points()

        # Assert
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history.metadata.get("expired_amount") == 700  # 1000 - 300

    def test_expire_original_history_metadata_updated(self):
        """원본 이력의 메타데이터 업데이트 확인"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True
        assert "expired_at" in expired_point.metadata
        assert expired_point.metadata.get("expired_amount") == 1000


# =============================================================================
# 알림 발송 엣지 케이스
# =============================================================================


@pytest.mark.django_db
class TestPointServiceNotificationsEdgeCases:
    """알림 발송 - 엣지 케이스"""

    @patch("shopping.tasks.send_email_notification")
    def test_skip_notification_for_zero_remaining_points(self, mock_send_email):
        """0포인트 남은 경우 알림 미발송"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 만료 예정이지만 전액 사용된 포인트
        point_history = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        point_history.metadata["used_amount"] = 100  # 전액 사용
        point_history.save(update_fields=["metadata"])

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 0
        mock_send_email.assert_not_called()

    @patch("shopping.tasks.send_email_notification")
    def test_notification_only_for_remaining_points(self, mock_send_email):
        """남은 포인트에 대해서만 알림 발송"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 일부 사용된 포인트 (100P 중 30P 사용)
        point_history = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        point_history.metadata["used_amount"] = 30
        point_history.save(update_fields=["metadata"])

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 1
        mock_send_email.assert_called_once()

        # 알림 내용에 남은 70P가 포함되어야 함
        call_args = mock_send_email.call_args
        assert "70" in call_args[0][1]  # subject에 70 포함

    @patch("shopping.tasks.send_email_notification")
    def test_notification_groups_multiple_expiring_points(self, mock_send_email):
        """여러 만료 예정 포인트 그룹화하여 1회 알림"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 여러 만료 예정 포인트
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=3),
        )
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=5),
        )
        PointHistoryFactory.earn(
            user=user,
            points=300,
            expires_at=timezone.now() + timedelta(days=7),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert
        assert count == 1  # 사용자당 1회만 발송
        mock_send_email.assert_called_once()
        assert "600" in mock_send_email.call_args[0][1]  # 총 600P


# =============================================================================
# 포인트 사용 응답 구조 상세 테스트
# =============================================================================


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

        # Assert
        assert result["success"] is True
        assert result["message"] == "500 포인트를 사용했습니다."
        assert len(result["used_details"]) == 1

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

        # Assert
        assert result["success"] is True
        assert len(result["used_details"]) == 2

        # 첫 번째: 200P 전액
        assert result["used_details"][0]["history_id"] == point1.id
        assert result["used_details"][0]["amount"] == 200

        # 두 번째: 200P 일부
        assert result["used_details"][1]["history_id"] == point2.id
        assert result["used_details"][1]["amount"] == 200


# =============================================================================
# 포인트 이력 추적 테스트
# =============================================================================


@pytest.mark.django_db
class TestPointServiceHistoryTracking:
    """포인트 이력 추적 상세 테스트"""

    def test_use_points_creates_history_with_negative_points(self):
        """포인트 사용 시 음수 이력 생성"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()
        PointHistoryFactory.earn(user=user, points=1000, balance=user.points)

        # Act
        service.use_points_fifo(user, 300, type="use")

        # Assert
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

        # Assert
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

        # Assert
        point.refresh_from_db()
        assert point.metadata.get("used_amount") == 450
        assert len(point.metadata.get("usage_history", [])) == 3

    def test_add_points_with_default_description(self):
        """포인트 추가 시 기본 설명 생성"""
        # Arrange
        user = UserFactory.with_points(0)

        # Act
        PointService.add_points(user, 500, type="earn")

        # Assert
        history = PointHistory.objects.filter(user=user, type="earn").first()
        assert "500" in history.description
        assert "추가" in history.description

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

        # Assert
        use_history = PointHistory.objects.filter(user=user, type="use").first()
        assert use_history.order == order
        # 커스텀 메타데이터가 사용 이력에 포함되어야 함
        assert use_history.metadata.get("promotion_id") == 123
        assert use_history.metadata.get("campaign") == "winter_sale"
        # used_details도 함께 저장되어야 함
        assert "used_details" in use_history.metadata


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

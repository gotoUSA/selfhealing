"""
PointService 단위 테스트 - 만료 처리

포인트 만료 조회, 만료 처리, 만료 예정 알림 발송을 테스트합니다.
"""

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# 만료 포인트 조회 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceExpiredPoints:
    """만료 포인트 조회 테스트"""

    def test_get_expired_points_found(self):
        """만료된 포인트 조회 성공"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 어제 만료된 포인트 생성
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

        # 이미 만료 처리된 포인트 (metadata에 expired=True)
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

        # Assert - 새로 만료된 것만 조회
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


# ==========================================
# 만료 예정 포인트 조회 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceExpiringPointsSoon:
    """만료 예정 포인트 조회 테스트"""

    def test_get_expiring_points_soon_default_7days(self):
        """7일 이내 만료 예정 포인트 조회 (기본값)"""
        # Arrange
        user = UserFactory()
        service = PointService()

        # 5일 후 만료 (조회 대상)
        expiring_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # 10일 후 만료 (조회 안 됨)
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

        # 20일 후 만료
        expiring_point = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=20),
        )

        # Act - 30일 이내로 조회
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

        # Assert - 알림 안 보낸 것만 조회
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


# ==========================================
# 만료 처리 테스트
# ==========================================


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

        # Assert - 처리 건수
        assert count == 1

        # Assert - 사용자 포인트 감소
        user.refresh_from_db()
        assert user.points == 900

        # Assert - 만료 이력 생성
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None
        assert expire_history.points == -100

        # Assert - 원본 이력에 만료 표시
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

        # 부분 사용된 포인트 (100P 중 30P 사용됨)
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

        # Assert - 남은 포인트만 만료됨
        user.refresh_from_db()
        assert user.points == 930  # 1000 - (100 - 30)

        # Assert - 만료 이력에 70P 기록
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history.points == -70

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

        # Assert - 처리 안 됨
        assert count == 0
        user.refresh_from_db()
        assert user.points == 1000

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

        # Assert - 원본 이력 메타데이터 확인
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True
        assert "expired_at" in expired_point.metadata
        assert expired_point.metadata.get("expired_amount") == 100

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
        PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        count = service.expire_points()

        # Assert - 에러 발생해도 나머지는 처리
        assert count == 2


# ==========================================
# 만료 처리 메타데이터 상세 검증
# ==========================================


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

        # Assert - 만료 이력의 메타데이터에 원본 ID 기록
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None
        assert expire_history.metadata.get("original_history_id") == expired_point.id

    def test_expire_metadata_contains_original_points(self):
        """만료 이력에 원본 적립 포인트 기록"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # Act
        service.expire_points()

        # Assert - 원본 적립 포인트 기록
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

        # Assert - 실제 만료된 금액 (1000 - 300 = 700)
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history.metadata.get("expired_amount") == 700

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

        # Assert - 원본 이력 메타데이터 업데이트
        expired_point.refresh_from_db()
        assert expired_point.metadata.get("expired") is True
        assert "expired_at" in expired_point.metadata
        assert expired_point.metadata.get("expired_amount") == 1000


# ==========================================
# 알림 발송 테스트
# ==========================================


@pytest.mark.django_db
class TestPointServiceNotifications:
    """
    알림 발송 테스트

    send_email_notification은 외부 이메일 서비스 호출이므로 mock 유지.
    실제 DB 상태 변화(metadata)로 로직 검증.
    """

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_success(self, mock_send_email):
        """만료 예정 알림 발송 성공 - DB 상태 검증"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 만료 예정 포인트 생성
        point_history = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert - 실제 DB 상태 변화 검증
        assert count == 1
        point_history.refresh_from_db()
        assert point_history.metadata.get("expiry_notified") is True
        assert "notified_at" in point_history.metadata

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_multiple_users(self, mock_send_email):
        """여러 사용자에게 알림 발송 - 각 사용자별 metadata 검증"""
        # Arrange
        service = PointService()

        user1 = UserFactory(email="user1@example.com")
        user2 = UserFactory(email="user2@example.com")

        ph1 = PointHistoryFactory.earn(
            user=user1,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        ph2 = PointHistoryFactory.earn(
            user=user2,
            points=200,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert - 실제 DB 상태 변화 검증
        assert count == 2
        ph1.refresh_from_db()
        ph2.refresh_from_db()
        assert ph1.metadata.get("expiry_notified") is True
        assert ph2.metadata.get("expiry_notified") is True

    @patch("shopping.tasks.send_email_notification")
    def test_send_expiry_notifications_grouping(self, mock_send_email):
        """사용자별 그룹화 확인 - 여러 포인트도 1회 알림"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 같은 사용자의 여러 만료 예정 포인트
        ph1 = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )
        ph2 = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=6),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert - 사용자별 1회만 카운트, 모든 포인트 이력에 notified 표시
        assert count == 1
        ph1.refresh_from_db()
        ph2.refresh_from_db()
        assert ph1.metadata.get("expiry_notified") is True
        assert ph2.metadata.get("expiry_notified") is True

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
        """알림 발송 실패 시 에러 처리 - metadata 미업데이트 확인"""
        # Arrange
        caplog.set_level(logging.ERROR, logger="shopping.services.point_service")
        user = UserFactory(email="test@example.com")
        service = PointService()

        point_history = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=5),
        )

        # 이메일 발송 실패 시뮬레이션
        mock_send_email.side_effect = Exception("Email send failed")

        # Act
        count = service.send_expiry_notifications()

        # Assert - 실패 시 metadata 업데이트 안 됨 확인
        assert count == 0
        point_history.refresh_from_db()
        assert point_history.metadata.get("expiry_notified") is not True

        # Assert - 로그 기록 확인
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

        # Assert - 메시지 내용 확인
        assert "testuser" in message
        assert "300" in message
        assert "100" in message
        assert "200" in message
        assert "만료" in message


# ==========================================
# 알림 발송 엣지 케이스
# ==========================================


@pytest.mark.django_db
class TestPointServiceNotificationsEdgeCases:
    """
    알림 발송 - 엣지 케이스

    외부 이메일 발송만 mock, 실제 DB 상태 변화로 검증
    """

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

        # Assert - DB 상태 검증
        assert count == 0
        point_history.refresh_from_db()
        assert point_history.metadata.get("expiry_notified") is not True

    @patch("shopping.tasks.send_email_notification")
    def test_notification_only_for_remaining_points(self, mock_send_email):
        """남은 포인트에 대해서만 알림 발송 - DB 상태 검증"""
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

        # Assert - DB 상태 검증
        assert count == 1
        point_history.refresh_from_db()
        assert point_history.metadata.get("expiry_notified") is True

    @patch("shopping.tasks.send_email_notification")
    def test_notification_groups_multiple_expiring_points(self, mock_send_email):
        """여러 만료 예정 포인트 그룹화하여 1회 알림 - 모든 이력에 notified 표시"""
        # Arrange
        user = UserFactory(email="test@example.com")
        service = PointService()

        # 여러 만료 예정 포인트
        ph1 = PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() + timedelta(days=3),
        )
        ph2 = PointHistoryFactory.earn(
            user=user,
            points=200,
            expires_at=timezone.now() + timedelta(days=5),
        )
        ph3 = PointHistoryFactory.earn(
            user=user,
            points=300,
            expires_at=timezone.now() + timedelta(days=7),
        )

        # Act
        count = service.send_expiry_notifications()

        # Assert - DB 상태 검증: 모든 이력에 notified 표시
        assert count == 1
        for ph in [ph1, ph2, ph3]:
            ph.refresh_from_db()
            assert ph.metadata.get("expiry_notified") is True

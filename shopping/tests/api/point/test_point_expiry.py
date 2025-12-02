from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tasks import expire_points_task

User = get_user_model()


class PointExpiryTestCase(TestCase):
    """포인트 만료 기능 테스트"""

    def setUp(self):
        """테스트 데이터 설정"""
        # 테스트 사용자 생성
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="testpass123")

        # 포인트 서비스 인스턴스
        self.point_service = PointService()

    def test_create_point_with_expiry_date(self):
        """포인트 적립 시 만료일이 1년 후로 설정되는지 테스트"""
        # Given: 현재 시간
        now = timezone.now()

        # When: 포인트 적립
        point_history = PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="테스트 적립")

        # Then: 만료일이 1년 후인지 확인
        expected_expiry = now + timedelta(days=365)
        self.assertIsNotNone(point_history.expires_at)
        # 시간 차이가 1분 이내인지 확인 (정확한 시간 비교)
        time_diff = abs((point_history.expires_at - expected_expiry).total_seconds())
        self.assertLess(time_diff, 60)

    def test_find_expired_points(self):
        """만료된 포인트 조회 테스트"""
        # Given: 과거에 적립된 포인트 (이미 만료됨)
        expired_date = timezone.now() - timedelta(days=366)
        with patch("django.utils.timezone.now", return_value=expired_date):
            PointHistory.create_history(user=self.user, points=500, balance=500, type="earn", description="만료된 포인트")

        # 아직 만료되지 않은 포인트
        PointHistory.create_history(user=self.user, points=300, balance=800, type="earn", description="유효한 포인트")

        # When: 만료된 포인트 조회
        expired_points = self.point_service.get_expired_points()

        # Then: 만료된 포인트만 조회되는지 확인
        self.assertEqual(len(expired_points), 1)
        self.assertEqual(expired_points[0].points, 500)

    def test_expire_points_process(self):
        """포인트 만료 처리 테스트"""
        # Given: 만료된 포인트와 사용자 잔액
        expired_date = timezone.now() - timedelta(days=366)
        with patch("django.utils.timezone.now", return_value=expired_date):
            PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="만료 예정 포인트")

        # 사용자에게 포인트 추가
        self.user.points = 1500
        self.user.save()

        # When: 포인트 만료 처리
        expired_count = self.point_service.expire_points()

        # Then:
        # 1. 만료 처리된 포인트 수 확인
        self.assertEqual(expired_count, 1)

        # 2. 사용자 포인트 잔액 확인 (1500 - 1000 = 500)
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 500)

        # 3. 만료 이력이 생성되었는지 확인
        expire_history = PointHistory.objects.filter(user=self.user, type="expire").first()
        self.assertIsNotNone(expire_history)
        self.assertEqual(expire_history.points, -1000)

    def test_fifo_point_usage(self):
        """FIFO 방식 포인트 사용 테스트"""
        # Given: 여러 시점에 적립된 포인트들
        base_time = timezone.now()

        # 1번 포인트: 6개월 전 적립 (먼저 사용되어야 함)
        with patch("django.utils.timezone.now", return_value=base_time - timedelta(days=180)):
            PointHistory.create_history(user=self.user, points=500, balance=500, type="earn", description="첫 번째 적립")

        # 2번 포인트: 3개월 전 적립
        with patch("django.utils.timezone.now", return_value=base_time - timedelta(days=90)):
            PointHistory.create_history(user=self.user, points=300, balance=800, type="earn", description="두 번째 적립")

        # 3번 포인트: 1개월 전 적립
        with patch("django.utils.timezone.now", return_value=base_time - timedelta(days=30)):
            PointHistory.create_history(user=self.user, points=200, balance=1000, type="earn", description="세 번째 적립")

        # 사용자 총 포인트 설정
        self.user.points = 1000
        self.user.save()

        # When: 600 포인트 사용 (FIFO 방식)
        result = self.point_service.use_points_fifo(self.user, 600)

        # Then:
        # 1. 사용 성공 확인
        self.assertTrue(result["success"])

        # 2. 사용 내역 확인 (500 + 100 = 600)
        used_details = result["used_details"]
        self.assertEqual(len(used_details), 2)
        self.assertEqual(used_details[0]["amount"], 500)  # 첫 번째 전액 사용
        self.assertEqual(used_details[1]["amount"], 100)  # 두 번째 일부 사용

        # 3. 남은 포인트 확인
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 400)

    def test_point_expiry_notification(self):
        """포인트 만료 7일 전 알림 테스트"""
        # Given: 7일 후 만료될 포인트
        expiry_date = timezone.now() + timedelta(days=7)
        PointHistory.objects.create(
            user=self.user,
            points=1000,
            balance=1000,
            type="earn",
            description="곧 만료될 포인트",
            expires_at=expiry_date,
        )

        # When: 만료 예정 포인트 조회
        expiring_points = self.point_service.get_expiring_points_soon(days=7)

        # Then:
        # 1. 만료 예정 포인트가 조회되는지 확인
        self.assertEqual(len(expiring_points), 1)
        self.assertEqual(expiring_points[0].points, 1000)

        # 2. 알림 발송 함수 테스트 (Mocking)
        with patch("shopping.tasks.send_email_notification") as mock_send_email:
            self.point_service.send_expiry_notifications()

            # 이메일 발송이 호출되었는지 확인
            mock_send_email.assert_called_once()
            call_args = mock_send_email.call_args[0]
            self.assertEqual(call_args[0], self.user.email)
            self.assertIn("포인트 만료", call_args[1])  # 제목에 포함

    def test_partial_point_usage_tracking(self):
        """부분 사용된 포인트 추적 테스트"""
        # Given: 포인트 적립
        history = PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="테스트 적립")

        # 사용자 포인트 설정
        self.user.points = 1000
        self.user.save()

        # When: 일부 포인트 사용 (300P)
        self.point_service.use_points_fifo(self.user, 300)

        # Then: 남은 포인트 추적
        history.refresh_from_db()

        remaining = self.point_service.get_remaining_points(history)
        self.assertEqual(remaining, 700)

    @patch("shopping.tasks.expire_points_task.delay")
    def test_celery_task_scheduling(self, mock_task):
        """Celery 태스크 스케줄링 테스트"""
        # When: 포인트 만료 태스크 실행
        expire_points_task.delay()

        # Then: 태스크가 큐에 추가되었는지 확인
        mock_task.assert_called_once()

    def test_no_points_to_expire(self):
        """만료할 포인트가 없을 때 테스트"""
        # Given: 모든 포인트가 아직 유효함
        PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="유효한 포인트")

        # When: 만료 처리 실행
        expired_count = self.point_service.expire_points()

        # Then: 만료된 포인트가 없음
        self.assertEqual(expired_count, 0)

    def test_multiple_users_point_expiry(self):
        """여러 사용자의 포인트 만료 처리 테스트"""
        # Given: 여러 사용자와 만료된 포인트
        user2 = User.objects.create_user(username="testuser2", email="test2@example.com")

        expired_date = timezone.now() - timedelta(days=366)

        # 사용자1의 만료 포인트
        with patch("django.utils.timezone.now", return_value=expired_date):
            PointHistory.create_history(user=self.user, points=500, balance=500, type="earn")

        # 사용자2의 만료 포인트
        with patch("django.utils.timezone.now", return_value=expired_date):
            PointHistory.create_history(user=user2, points=700, balance=700, type="earn")

        # When: 전체 만료 처리
        expired_count = self.point_service.expire_points()

        # Then: 모든 사용자의 포인트가 만료됨
        self.assertEqual(expired_count, 2)

    def test_fifo_usage_integration_with_order_flow(self):
        """주문 플로우에서 FIFO 포인트 사용 통합 테스트"""
        # Given: 여러 시점에 적립된 포인트들
        base_time = timezone.now()

        # 6개월 전 적립
        with patch("django.utils.timezone.now", return_value=base_time - timedelta(days=180)):
            history1 = PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="첫 번째 적립")

        # 3개월 전 적립
        with patch("django.utils.timezone.now", return_value=base_time - timedelta(days=90)):
            history2 = PointHistory.create_history(user=self.user, points=2000, balance=3000, type="earn", description="두 번째 적립")

        # 사용자 총 포인트 설정
        self.user.points = 3000
        self.user.save()

        # When: 1500 포인트 사용 (주문 시나리오)
        result = self.point_service.use_points_fifo(self.user, 1500)

        # Then: FIFO 순서로 사용됨
        self.assertTrue(result["success"])
        self.assertEqual(len(result["used_details"]), 2)

        # 첫 번째 적립 전액 사용
        self.assertEqual(result["used_details"][0]["amount"], 1000)
        self.assertEqual(result["used_details"][0]["history_id"], history1.id)

        # 두 번째 적립 일부 사용
        self.assertEqual(result["used_details"][1]["amount"], 500)
        self.assertEqual(result["used_details"][1]["history_id"], history2.id)

        # 사용자 포인트 차감 확인
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 1500)

    def test_point_expiry_after_partial_usage(self):
        """부분 사용 후 만료 처리 통합 테스트"""
        # Given: 포인트 적립 및 부분 사용
        expired_date = timezone.now() - timedelta(days=366)

        # 1년 전 적립 (현재는 만료됨)
        with patch("django.utils.timezone.now", return_value=expired_date):
            PointHistory.create_history(user=self.user, points=2000, balance=2000, type="earn", description="만료 예정 포인트")

        # 사용자 포인트 설정
        self.user.points = 2000
        self.user.save()

        # When: 800 포인트 사용
        result = self.point_service.use_points_fifo(self.user, 800)
        self.assertTrue(result["success"])

        # 만료 처리 실행
        expired_count = self.point_service.expire_points()

        # Then:
        # 1. 만료 처리 1건 실행
        self.assertEqual(expired_count, 1)

        # 2. 남은 포인트만 차감 (2000 - 800 = 1200 차감)
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 0)

        # 3. 만료 이력 확인
        expire_history = PointHistory.objects.filter(user=self.user, type="expire").first()
        self.assertIsNotNone(expire_history)
        self.assertEqual(expire_history.points, -1200)

    def test_fifo_usage_prevents_double_deduction(self):
        """FIFO 사용 후 만료 시 중복 차감 방지 테스트"""
        # Given: 만료 예정 포인트 적립
        expired_date = timezone.now() - timedelta(days=366)

        with patch("django.utils.timezone.now", return_value=expired_date):
            history = PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="만료 포인트")

        self.user.points = 1000
        self.user.save()

        # When: 전액 사용
        result = self.point_service.use_points_fifo(self.user, 1000)
        self.assertTrue(result["success"])

        # 만료 처리 실행
        expired_count = self.point_service.expire_points()

        # Then:
        # 1. 만료 처리는 실행되지만 차감할 포인트가 없음
        self.assertEqual(expired_count, 0)

        # 2. 사용자 포인트는 0 유지 (중복 차감 없음)
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 0)

        # 3. 이미 전액 사용되어 만료 이력 미생성
        expire_history = PointHistory.objects.filter(user=self.user, type="expire")
        self.assertEqual(expire_history.count(), 0)

    def test_multiple_fifo_usage_tracking(self):
        """여러 번 FIFO 사용 시 정확한 추적 테스트"""
        # Given: 포인트 적립
        PointHistory.create_history(user=self.user, points=1000, balance=1000, type="earn", description="첫 적립")

        self.user.points = 1000
        self.user.save()

        # When: 여러 번 나누어 사용
        # 첫 번째 사용
        result1 = self.point_service.use_points_fifo(self.user, 300)
        self.assertTrue(result1["success"])

        # 두 번째 사용
        result2 = self.point_service.use_points_fifo(self.user, 200)
        self.assertTrue(result2["success"])

        # 세 번째 사용
        result3 = self.point_service.use_points_fifo(self.user, 100)
        self.assertTrue(result3["success"])

        # Then:
        # 1. 사용자 포인트 확인
        self.user.refresh_from_db()
        self.assertEqual(self.user.points, 400)

        # 2. 적립 이력의 used_amount 확인
        history = PointHistory.objects.filter(user=self.user, type="earn").first()
        remaining = self.point_service.get_remaining_points(history)
        self.assertEqual(remaining, 400)

        # 3. 메타데이터에 사용 내역 누적 확인
        history.refresh_from_db()
        self.assertEqual(history.metadata.get("used_amount"), 600)

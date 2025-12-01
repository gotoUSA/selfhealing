"""
포인트 만료 기능 테스트용 관리 명령어
python manage.py test_point_expiry
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService

User = get_user_model()


class Command(BaseCommand):
    help = "포인트 만료 기능 테스트"

    def add_arguments(self, parser):
        parser.add_argument("--create-test-data", action="store_true", help="테스트 데이터 생성")
        parser.add_argument("--expire", action="store_true", help="만료 처리 실행")
        parser.add_argument("--notify", action="store_true", help="만료 예정 알림 발송")
        parser.add_argument("--use-points", type=int, help="포인트 사용 테스트 (금액 지정)")
        parser.add_argument("--username", type=str, default="testuser", help="테스트할 사용자명")

    def handle(self, *args, **options):
        service = PointService()

        if options["create_test_data"]:
            self.create_test_data(options["username"])

        if options["expire"]:
            self.test_expire_points(service)

        if options["notify"]:
            self.test_notifications(service)

        if options["use_points"]:
            self.test_use_points(service, options["username"], options["use_points"])

    def create_test_data(self, username):
        """테스트 데이터 생성"""
        self.stdout.write("테스트 데이터 생성 중...")

        # 사용자 생성 또는 조회
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@test.com",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        if created:
            user.set_password("testpass123")
            user.save()
            self.stdout.write(self.style.SUCCESS(f"사용자 생성: {username}"))

        # 다양한 시점의 포인트 생성
        now = timezone.now()

        # 1. 이미 만료된 포인트 (400일 전 적립, 이미 만료)
        PointHistory.create_history(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="[테스트] 400일 전 적립 (만료됨)",
            expires_at=now - timedelta(days=35),  # 이미 만료
        )

        # 2. 오늘 만료되는 포인트
        PointHistory.create_history(
            user=user,
            points=2000,
            balance=3000,
            type="earn",
            description="[테스트] 365일 전 적립 (오늘 만료)",
            expires_at=now,  # 오늘 만료
        )

        # 3. 7일 후 만료 예정
        PointHistory.create_history(
            user=user,
            points=3000,
            balance=6000,
            type="earn",
            description="[테스트] 358일 전 적립 (7일 후 만료)",
            expires_at=now + timedelta(days=7),
        )

        # 4. 한 달 후 만료
        PointHistory.create_history(
            user=user,
            points=4000,
            balance=10000,
            type="earn",
            description="[테스트] 335일 전 적립 (30일 후 만료)",
            expires_at=now + timedelta(days=30),
        )

        # 5. 최근 적립 (만료까지 충분)
        PointHistory.create_history(
            user=user,
            points=5000,
            balance=15000,
            type="earn",
            description="[테스트] 오늘 적립 (1년 후 만료)",
            expires_at=now + timedelta(days=365),
        )

        # 사용자 총 포인트 업데이트
        total_points = 1000 + 2000 + 3000 + 4000 + 5000
        user.points = total_points
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"\n테스트 데이터 생성 완료!"
                f"\n- 사용자: {user.username}"
                f"\n- 총 포인트: {total_points:,}"
                f"\n- 만료된 포인트: 1,000"
                f"\n- 오늘 만료: 2,000"
                f"\n- 7일 후 만료: 3,000"
                f"\n- 30일 후 만료: 4,000"
                f"\n- 1년 후 만료: 5,000"
            )
        )

    def test_expire_points(self, service):
        """포인트 만료 처리 테스트"""
        self.stdout.write("\n포인트 만료 처리 테스트...")

        # 만료 대상 조회
        expired_points = service.get_expired_points()

        if expired_points:
            self.stdout.write(f"만료 대상: {len(expired_points)}건")
            for point in expired_points:
                remaining = service.get_remaining_points(point)
                self.stdout.write(f"- {point.user.username}: {remaining:,}P " f"(적립일: {point.created_at.date()})")

            # 만료 처리 실행
            expired_count = service.expire_points()
            self.stdout.write(self.style.SUCCESS(f"\n만료 처리 완료: {expired_count}건"))
        else:
            self.stdout.write("만료할 포인트가 없습니다.")

    def test_notifications(self, service):
        """만료 예정 알림 테스트"""
        self.stdout.write("\n만료 예정 알림 테스트...")

        # 7일 이내 만료 예정 조회
        expiring_points = service.get_expiring_points_soon(days=7)

        if expiring_points:
            self.stdout.write(f"만료 예정 (7일 이내): {len(expiring_points)}건")
            for point in expiring_points:
                remaining = service.get_remaining_points(point)
                days_left = (point.expires_at - timezone.now()).days
                self.stdout.write(f"- {point.user.username}: {remaining:,}P " f"({days_left}일 남음)")

            # 알림 발송
            notification_count = service.send_expiry_notifications()
            self.stdout.write(self.style.SUCCESS(f"\n알림 발송 완료: {notification_count}명"))
        else:
            self.stdout.write("만료 예정 포인트가 없습니다.")

    def test_use_points(self, service, username, amount):
        """포인트 사용 테스트 (FIFO)"""
        self.stdout.write(f"\n포인트 사용 테스트: {amount:,}P")

        try:
            user = User.objects.get(username=username)
            self.stdout.write(f"현재 포인트: {user.points:,}P")

            # FIFO 방식 사용
            result = service.use_points_fifo(user, amount)

            if result["success"]:
                self.stdout.write(self.style.SUCCESS(f"\n{result['message']}"))
                self.stdout.write("\n사용 내역 (FIFO):")
                for detail in result["used_details"]:
                    self.stdout.write(f"- History #{detail['history_id']}: " f"{detail['amount']:,}P 사용")

                user.refresh_from_db()
                self.stdout.write(f"\n남은 포인트: {user.points:,}P")
            else:
                self.stdout.write(self.style.ERROR(f"\n실패: {result['message']}"))

        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"사용자를 찾을 수 없음: {username}"))

"""
부하 테스트용 사용자 생성 Management Command

사용법:
    python manage.py create_load_test_users --count 1000
    python manage.py create_load_test_users --count 100 --points 10000
    python manage.py create_load_test_users --clear  # 기존 사용자 삭제 후 생성
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = "부하 테스트용 사용자 생성 (load_test_user_1 ~ load_test_user_N)"

    def add_arguments(self, parser):
        """커맨드 옵션 추가"""
        parser.add_argument(
            "--count",
            type=int,
            default=100,
            help="생성할 사용자 수 (기본값: 100)",
        )
        parser.add_argument(
            "--points",
            type=int,
            default=50000,
            help="각 사용자에게 지급할 초기 포인트 (기본값: 50000)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="기존 load_test_user_* 사용자를 모두 삭제하고 새로 생성",
        )

    def handle(self, *args, **options):
        """메인 실행 함수"""
        count = options["count"]
        initial_points = options["points"]
        clear = options["clear"]

        # 기존 사용자 삭제
        if clear:
            deleted_count = User.objects.filter(username__startswith="load_test_user_").count()
            if deleted_count > 0:
                User.objects.filter(username__startswith="load_test_user_").delete()
                self.stdout.write(self.style.WARNING(f"기존 부하 테스트 사용자 {deleted_count}명 삭제됨"))

        # 사용자 생성
        self.stdout.write(self.style.SUCCESS(f"\n부하 테스트 사용자 {count}명 생성 시작..."))
        self.stdout.write(f"- 각 사용자 초기 포인트: {initial_points:,}원")

        created_users = []
        skipped_users = []

        with transaction.atomic():
            for i in range(0, count):  # 0부터 시작 (0-999 범위)
                username = f"load_test_user_{i}"

                # 이미 존재하는 사용자는 건너뛰기
                if User.objects.filter(username=username).exists():
                    skipped_users.append(username)
                    continue

                # 사용자 생성
                user = User.objects.create_user(
                    username=username,
                    email=f"load_test_{i}@example.com",
                    password="testpass123",
                    first_name="부하테스트",
                    last_name=f"사용자{i}",
                    is_email_verified=True,  # 이메일 인증 완료 상태로 생성
                    points=initial_points,  # 포인트 직접 설정
                )

                # load_test_user_0은 admin 권한 부여 (Stage 10 Self-Healing API 테스트용)
                if i == 0:
                    user.is_staff = True
                    user.save()

                created_users.append(username)

                # 진행 상황 표시 (100명 단위)
                if i % 100 == 0:
                    self.stdout.write(f"  진행 중... {i}/{count}명 생성 완료")

        # 결과 출력
        self.stdout.write(self.style.SUCCESS(f"\n✓ 부하 테스트 사용자 생성 완료"))
        self.stdout.write(f"  - 새로 생성: {len(created_users)}명")
        if skipped_users:
            self.stdout.write(f"  - 이미 존재 (건너뜀): {len(skipped_users)}명")

        self.stdout.write(
            self.style.SUCCESS(
                f"""
부하 테스트 실행 방법:
    locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000

로그인 정보:
    - 사용자명: load_test_user_0 ~ load_test_user_{count - 1}
    - 비밀번호: testpass123
    - 초기 포인트: {initial_points:,}원
"""
            )
        )

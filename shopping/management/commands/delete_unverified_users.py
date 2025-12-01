from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from shopping.models.user import User


class Command(BaseCommand):
    help = "미인증 계정을 삭제합니다 (기본: 7일 경과)"

    def add_arguments(self, parser):
        """커맨드 인자 정의"""
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="삭제할 계정의 경과 일수 (기본: 7일)",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제로 삭제하지 않고 확인만 합니다",
        )

        parser.add_argument(
            "--verbose",
            action="store_true",
            help="상세 정보를 출력합니다",
        )

    def handle(self, *args, **options):
        """커맨드 실행"""
        days = options["days"]
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        # 삭제 기준 날짜 계산
        cutoff_date = timezone.now() - timedelta(days=days)

        self.stdout.write(self.style.WARNING(f'\n{"=" * 60}'))
        self.stdout.write(self.style.WARNING("미인증 계정 삭제 작업 시작"))
        self.stdout.write(self.style.WARNING(f'{"=" * 60}\n'))

        self.stdout.write(f'삭제 기준일: {cutoff_date.strftime("%Y-%m-%d %H:%M:%S")}')
        self.stdout.write(f"경과 일수: {days}일")
        self.stdout.write(f'Dry Run 모드: {"예" if dry_run else "아니오"}\n')

        try:
            # 미인증 사용자 조회
            unverified_users = User.objects.filter(
                is_email_verified=False,
                date_joined__lt=cutoff_date,
            ).select_related()

            total_count = unverified_users.count()

            if total_count == 0:
                self.stdout.write(self.style.SUCCESS("✅ 삭제할 미인증 계정이 없습니다."))
                return

            self.stdout.write(self.style.WARNING(f"📊 총 {total_count}개의 미인증 계정을 발견했습니다.\n"))

            # 주문 이력 확인
            users_to_delete = []
            users_to_keep = []

            for user in unverified_users:
                # 주문이 있으면 유지
                if hasattr(user, "orders") and user.orders.exists():
                    users_to_keep.append(
                        {
                            "email": user.email,
                            "joined": user.date_joined,
                            "order_count": user.orders.count(),
                        }
                    )
                    continue

                users_to_delete.append(
                    {
                        "email": user.email,
                        "joined": user.date_joined,
                        "username": user.username,
                    }
                )

            # 결과 출력
            delete_count = len(users_to_delete)
            keep_count = len(users_to_keep)

            self.stdout.write(f"삭제 대상: {delete_count}개")
            self.stdout.write(f"유지 대상: {keep_count}개 (주문 이력 있음)\n")

            # 상세 정보 출력
            if verbose and users_to_delete:
                self.stdout.write(self.style.WARNING("삭제 대상 목록:"))
                for i, user_info in enumerate(users_to_delete, 1):
                    self.stdout.write(f"  {i}. {user_info['email']} " f"(가입일: {user_info['joined'].strftime('%Y-%m-%d')})")
                self.stdout.write("")

            if verbose and users_to_keep:
                self.stdout.write(self.style.SUCCESS("유지 대상 목록:"))
                for i, user_info in enumerate(users_to_keep, 1):
                    self.stdout.write(f"  {i}. {user_info['email']} " f"(주문: {user_info['order_count']}건)")
                self.stdout.write("")

            # Dry run 모드면 여기서 종료
            if dry_run:
                self.stdout.write(self.style.SUCCESS("\n✅ Dry Run 모드: 실제 삭제는 수행하지 않았습니다."))
                return

            # 실제 삭제 확인
            if delete_count > 0:
                confirm = input(f"\n⚠️  정말로 {delete_count}개의 계정을 삭제하시겠습니까? (yes/no): ")

                if confirm.lower() != "yes":
                    self.stdout.write(self.style.WARNING("\n❌ 삭제가 취소되었습니다."))
                    return

                # 트랜잭션으로 안전하게 삭제
                emails_to_delete = [u["email"] for u in users_to_delete]

                with transaction.atomic():
                    deleted_result = User.objects.filter(
                        is_email_verified=False,
                        email__in=emails_to_delete,
                    ).delete()

                    # 삭제 결과 로깅
                    self.stdout.write(f"삭제된 사용자 수: {deleted_result[0]}")

                self.stdout.write(self.style.SUCCESS(f"\n✅ {delete_count}개의 미인증 계정이 삭제되었습니다."))

                # 삭제된 계정 로그
                if verbose:
                    self.stdout.write("\n삭제된 계정:")
                    for user_info in users_to_delete:
                        self.stdout.write(f"  - {user_info['email']}")

            # 유지된 계정 안내
            if keep_count > 0:
                self.stdout.write(self.style.SUCCESS(f"\n📌 {keep_count}개의 계정은 주문 이력이 있어 유지되었습니다."))

            self.stdout.write(self.style.SUCCESS(f'\n{"=" * 60}'))
            self.stdout.write(self.style.SUCCESS("작업 완료"))
            self.stdout.write(self.style.SUCCESS(f'{"=" * 60}\n'))

        except Exception as e:
            raise CommandError(f"오류 발생: {str(e)}")

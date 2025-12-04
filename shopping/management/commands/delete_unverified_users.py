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

        parser.add_argument(
            "--force",
            action="store_true",
            help="확인 없이 즉시 삭제합니다 (CI/CD용)",
        )

    def handle(self, *args, **options):
        """커맨드 실행"""
        self._print_header(options)

        try:
            users = self._get_unverified_users(options["days"])

            if not users.exists():
                self.stdout.write(self.style.SUCCESS("✅ 삭제할 미인증 계정이 없습니다."))
                return

            self.stdout.write(self.style.WARNING(f"📊 총 {users.count()}개의 미인증 계정을 발견했습니다.\n"))

            to_delete, to_keep = self._categorize_users(users)
            self._print_summary(to_delete, to_keep, options["verbose"])

            if options["dry_run"]:
                self.stdout.write(self.style.SUCCESS("\n✅ Dry Run 모드: 실제 삭제는 수행하지 않았습니다."))
                return

            self._confirm_and_delete(to_delete, options["verbose"], options["force"])
            self._print_footer(len(to_keep))

        except Exception as e:
            raise CommandError(f"오류 발생: {str(e)}")

    def _print_header(self, options):
        """헤더 및 설정 정보 출력"""
        cutoff_date = timezone.now() - timedelta(days=options["days"])

        self.stdout.write(self.style.WARNING(f'\n{"=" * 60}'))
        self.stdout.write(self.style.WARNING("미인증 계정 삭제 작업 시작"))
        self.stdout.write(self.style.WARNING(f'{"=" * 60}\n'))

        self.stdout.write(f'삭제 기준일: {cutoff_date.strftime("%Y-%m-%d %H:%M:%S")}')
        self.stdout.write(f"경과 일수: {options['days']}일")
        self.stdout.write(f'Dry Run 모드: {"예" if options["dry_run"] else "아니오"}\n')

    def _get_unverified_users(self, days):
        """미인증 사용자 조회"""
        cutoff_date = timezone.now() - timedelta(days=days)
        return User.objects.filter(
            is_email_verified=False,
            date_joined__lt=cutoff_date,
        ).select_related()

    def _categorize_users(self, users):
        """사용자를 삭제/유지 대상으로 분류"""
        to_delete = []
        to_keep = []

        for user in users:
            if hasattr(user, "orders") and user.orders.exists():
                to_keep.append({
                    "email": user.email,
                    "joined": user.date_joined,
                    "order_count": user.orders.count(),
                })
            else:
                to_delete.append({
                    "email": user.email,
                    "joined": user.date_joined,
                    "username": user.username,
                })

        return to_delete, to_keep

    def _print_summary(self, to_delete, to_keep, verbose):
        """요약 및 상세 정보 출력"""
        self.stdout.write(f"삭제 대상: {len(to_delete)}개")
        self.stdout.write(f"유지 대상: {len(to_keep)}개 (주문 이력 있음)\n")

        if not verbose:
            return

        if to_delete:
            self.stdout.write(self.style.WARNING("삭제 대상 목록:"))
            for i, user_info in enumerate(to_delete, 1):
                self.stdout.write(
                    f"  {i}. {user_info['email']} "
                    f"(가입일: {user_info['joined'].strftime('%Y-%m-%d')})"
                )
            self.stdout.write("")

        if to_keep:
            self.stdout.write(self.style.SUCCESS("유지 대상 목록:"))
            for i, user_info in enumerate(to_keep, 1):
                self.stdout.write(
                    f"  {i}. {user_info['email']} "
                    f"(주문: {user_info['order_count']}건)"
                )
            self.stdout.write("")

    def _confirm_and_delete(self, to_delete, verbose, force=False):
        """사용자 확인 후 삭제 수행"""
        if not to_delete:
            return

        if not force and not self._get_user_confirmation(len(to_delete)):
            self.stdout.write(self.style.WARNING("\n❌ 삭제가 취소되었습니다."))
            return

        self._execute_delete(to_delete, verbose)

    def _get_user_confirmation(self, count):
        """사용자 확인 입력 받기"""
        response = input(f"\n⚠️  정말로 {count}개의 계정을 삭제하시겠습니까? (yes/no): ")
        return response.lower() == "yes"

    def _execute_delete(self, to_delete, verbose):
        """실제 삭제 수행"""
        emails_to_delete = [u["email"] for u in to_delete]

        with transaction.atomic():
            deleted_result = User.objects.filter(
                is_email_verified=False,
                email__in=emails_to_delete,
            ).delete()
            self.stdout.write(f"삭제된 사용자 수: {deleted_result[0]}")

        self.stdout.write(self.style.SUCCESS(f"\n✅ {len(to_delete)}개의 미인증 계정이 삭제되었습니다."))

        if verbose:
            self.stdout.write("\n삭제된 계정:")
            for user_info in to_delete:
                self.stdout.write(f"  - {user_info['email']}")

    def _print_footer(self, keep_count):
        """작업 완료 메시지 출력"""
        if keep_count > 0:
            self.stdout.write(self.style.SUCCESS(f"\n📌 {keep_count}개의 계정은 주문 이력이 있어 유지되었습니다."))

        self.stdout.write(self.style.SUCCESS(f'\n{"=" * 60}'))
        self.stdout.write(self.style.SUCCESS("작업 완료"))
        self.stdout.write(self.style.SUCCESS(f'{"=" * 60}\n'))

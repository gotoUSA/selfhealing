"""
정리 태스크 테스트

Celery 비동기 작업의 데이터 정리 기능 검증:
- delete_unverified_users_task: 미인증 계정 삭제
- cleanup_old_email_logs_task: 오래된 이메일 로그 정리
- cleanup_used_tokens_task: 사용된 토큰 정리
- cleanup_expired_tokens_task: 만료된 토큰 정리
"""

import pytest

from shopping.models.email_verification import EmailLog, EmailVerificationToken
from shopping.models.user import User
from shopping.tasks.cleanup_tasks import (
    cleanup_expired_tokens_task,
    cleanup_old_email_logs_task,
    cleanup_used_tokens_task,
    delete_unverified_users_task,
)
from shopping.tests.factories import (
    EmailLogFactory,
    EmailVerificationTokenFactory,
    UserFactory,
)


# ==========================================
# Fixtures
# ==========================================


@pytest.fixture
def unverified_users(db):
    """
    미인증 사용자 목록 생성 (Factory 사용)

    - old_unverified: 8일 전 가입, 미인증 (삭제 대상)
    - recent_unverified: 5일 전 가입, 미인증 (유지 대상)
    - verified: 10일 전 가입, 인증됨 (유지 대상)
    """
    return {
        "old_unverified": UserFactory.old_unverified(days_ago=8),
        "recent_unverified": UserFactory.recent_unverified(days_ago=5),
        "verified": UserFactory.old_verified(days_ago=10),
    }


@pytest.fixture
def email_logs(db):
    """
    이메일 로그 목록 생성 (Factory 사용)

    - old_sent: 100일 전, sent 상태 (삭제 대상)
    - old_verified: 100일 전, verified 상태 (삭제 대상)
    - old_pending: 100일 전, pending 상태 (유지 대상 - pending)
    - recent_sent: 50일 전, sent 상태 (유지 대상 - 90일 미만)
    """
    user = UserFactory()
    return {
        "old_sent": EmailLogFactory.old_sent(user=user, days_ago=100),
        "old_verified": EmailLogFactory.old_verified(user=user, days_ago=100),
        "old_pending": EmailLogFactory.old_pending(user=user, days_ago=100),
        "recent_sent": EmailLogFactory.old_sent(user=user, days_ago=50),
    }


@pytest.fixture
def verification_tokens(db):
    """
    인증 토큰 목록 생성 (Factory 사용)

    - old_used: 40일 전 사용됨 (삭제 대상)
    - recent_used: 20일 전 사용됨 (유지 대상 - 30일 미만)
    - unused: 미사용 (유지 대상)
    """
    user = UserFactory.unverified()
    return {
        "old_used": EmailVerificationTokenFactory.old_used(user=user, days_ago=40),
        "recent_used": EmailVerificationTokenFactory.recent_used(user=user, days_ago=20),
        "unused": EmailVerificationTokenFactory(user=user),
    }


@pytest.fixture
def expired_tokens(db):
    """
    만료 토큰 목록 생성 (Factory 사용)

    - expired: 25시간 전 생성, 미사용 (삭제 대상 - 만료됨)
    - valid: 20시간 전 생성, 미사용 (유지 대상 - 아직 유효)
    - used: 30시간 전 생성, 사용됨 (유지 대상 - 이미 사용)
    """
    user = UserFactory.unverified()
    return {
        "expired": EmailVerificationTokenFactory.expired(user=user, hours_ago=25),
        "valid": EmailVerificationTokenFactory.expired(user=user, hours_ago=20),
        "used": EmailVerificationTokenFactory.used(user=user),
    }


# ==========================================
# 미인증 계정 삭제 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestDeleteUnverifiedUsersTask:
    """미인증 계정 삭제 태스크 테스트"""

    def test_delete_unverified_users_default(self, unverified_users):
        """기본 설정(7일)으로 미인증 계정 삭제"""
        # Arrange
        # unverified_users fixture에서 3명의 사용자 생성됨
        # - old_unverified: 8일 전, 미인증 → 삭제 대상
        # - recent_unverified: 5일 전, 미인증 → 유지
        # - verified: 10일 전, 인증됨 → 유지
        old_email = unverified_users["old_unverified"].email
        recent_email = unverified_users["recent_unverified"].email
        verified_email = unverified_users["verified"].email

        # Act
        result = delete_unverified_users_task()

        # Assert - 태스크 결과 확인
        assert result["success"] is True
        assert result["deleted_count"] == 1

        # Assert - 8일 전 미인증 사용자만 삭제되었는지 확인
        assert not User.objects.filter(email=old_email).exists()

        # Assert - 나머지는 유지되었는지 확인
        assert User.objects.filter(email=recent_email).exists()
        assert User.objects.filter(email=verified_email).exists()

    def test_delete_unverified_users_custom_days(self, unverified_users):
        """커스텀 일수(5일)로 삭제"""
        # Arrange
        # 5일로 설정하면 8일, 5일 전 미인증 사용자 모두 삭제됨
        old_email = unverified_users["old_unverified"].email
        recent_email = unverified_users["recent_unverified"].email
        verified_email = unverified_users["verified"].email

        # Act
        result = delete_unverified_users_task(days=5)

        # Assert - 5일 이상 된 미인증 사용자 2명 삭제
        assert result["deleted_count"] == 2
        assert not User.objects.filter(email=old_email).exists()
        assert not User.objects.filter(email=recent_email).exists()

        # Assert - 인증된 사용자는 유지
        assert User.objects.filter(email=verified_email).exists()

    def test_delete_unverified_users_none_to_delete(self, unverified_users):
        """삭제할 계정이 없는 경우"""
        # Arrange
        # 모든 사용자를 인증 처리
        User.objects.filter(is_email_verified=False).update(is_email_verified=True)

        # Act
        result = delete_unverified_users_task()

        # Assert - 삭제된 계정 0개
        assert result["success"] is True
        assert result["deleted_count"] == 0


# ==========================================
# 오래된 이메일 로그 정리 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestCleanupOldEmailLogsTask:
    """오래된 이메일 로그 정리 태스크 테스트"""

    def test_cleanup_old_email_logs_default(self, email_logs):
        """기본 설정(90일)으로 오래된 로그 삭제"""
        # Arrange
        # email_logs fixture에서 4개의 로그 생성됨
        # - old_sent: 100일 전, sent → 삭제 대상
        # - old_verified: 100일 전, verified → 삭제 대상
        # - old_pending: 100일 전, pending → 유지 (pending 상태)
        # - recent_sent: 50일 전, sent → 유지 (90일 미만)

        # Act
        result = cleanup_old_email_logs_task()

        # Assert - 태스크 결과 확인
        assert result["success"] is True
        assert result["deleted_count"] == 2

        # Assert - sent, verified 상태의 오래된 로그만 삭제
        assert not EmailLog.objects.filter(id=email_logs["old_sent"].id).exists()
        assert not EmailLog.objects.filter(id=email_logs["old_verified"].id).exists()

        # Assert - pending과 최근 로그는 유지
        assert EmailLog.objects.filter(id=email_logs["old_pending"].id).exists()
        assert EmailLog.objects.filter(id=email_logs["recent_sent"].id).exists()

    def test_cleanup_old_email_logs_custom_days(self, email_logs):
        """커스텀 일수(60일)로 삭제"""
        # Arrange
        # 60일로 설정하면 100일 전 sent, verified 2개 삭제

        # Act
        result = cleanup_old_email_logs_task(days=60)

        # Assert
        assert result["success"] is True
        assert result["deleted_count"] == 2


# ==========================================
# 사용된 토큰 정리 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestCleanupUsedTokensTask:
    """사용된 토큰 정리 태스크 테스트"""

    def test_cleanup_used_tokens_default(self, verification_tokens):
        """기본 설정(30일)으로 사용된 토큰 삭제"""
        # Arrange
        # verification_tokens fixture에서 3개의 토큰 생성됨
        # - old_used: 40일 전 사용 → 삭제 대상
        # - recent_used: 20일 전 사용 → 유지 (30일 미만)
        # - unused: 미사용 → 유지

        # Act
        result = cleanup_used_tokens_task()

        # Assert - 태스크 결과 확인
        assert result["success"] is True
        assert result["deleted_count"] == 1

        # Assert - 40일 전 사용된 토큰만 삭제
        assert not EmailVerificationToken.objects.filter(id=verification_tokens["old_used"].id).exists()

        # Assert - 나머지는 유지
        assert EmailVerificationToken.objects.filter(id=verification_tokens["recent_used"].id).exists()
        assert EmailVerificationToken.objects.filter(id=verification_tokens["unused"].id).exists()


# ==========================================
# 만료된 토큰 정리 태스크 테스트
# ==========================================


@pytest.mark.django_db
class TestCleanupExpiredTokensTask:
    """만료된 토큰 정리 태스크 테스트"""

    def test_cleanup_expired_tokens(self, expired_tokens):
        """만료된 미사용 토큰 삭제"""
        # Arrange
        # expired_tokens fixture에서 3개의 토큰 생성됨
        # - expired: 25시간 전 생성, 미사용 → 삭제 대상 (24시간 초과)
        # - valid: 20시간 전 생성, 미사용 → 유지 (24시간 미만)
        # - used: 사용됨 → 유지 (이미 사용)

        # Act
        result = cleanup_expired_tokens_task()

        # Assert - 태스크 결과 확인
        assert result["success"] is True
        assert result["deleted_count"] == 1

        # Assert - 만료된 미사용 토큰만 삭제
        assert not EmailVerificationToken.objects.filter(id=expired_tokens["expired"].id).exists()

        # Assert - 유효한 토큰과 사용된 토큰은 유지
        assert EmailVerificationToken.objects.filter(id=expired_tokens["valid"].id).exists()
        assert EmailVerificationToken.objects.filter(id=expired_tokens["used"].id).exists()


# ==========================================
# 정리 태스크 통합 테스트
# ==========================================


@pytest.mark.django_db
class TestCleanupTaskIntegration:
    """정리 태스크 통합 테스트"""

    def test_full_cleanup_workflow(self, db):
        """전체 정리 워크플로우 (Factory 사용)"""
        # Arrange - 오래된 미인증 사용자 + 연관 데이터 생성
        old_user = UserFactory.old_unverified(days_ago=10)
        token = EmailVerificationTokenFactory.expired(user=old_user, hours_ago=240)  # 10일
        EmailLogFactory.old_sent(user=old_user, token=token, days_ago=10)

        users_before = User.objects.count()

        # Act - 미인증 계정 삭제 (CASCADE로 토큰, 로그도 삭제)
        delete_result = delete_unverified_users_task(days=7)

        # Assert - 태스크 성공
        assert delete_result["success"] is True

        # Assert - 사용자 삭제되면 연관 데이터도 삭제됨 (CASCADE)
        assert User.objects.count() < users_before

"""
포인트 만료 처리 동시성 테스트

Purpose:
    포인트 만료 스케줄러와 사용자의 포인트 사용 간 race condition을 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 만료/사용 동시 처리 시 중복 차감 방지
        - FIFO 순서 보장
        - 음수 잔액 방지
    B. Exception Cases (예외 케이스):
        - 이미 사용된 포인트 만료 처리
        - 부분 사용된 포인트 만료 처리
        - 이미 만료 표시된 포인트 재처리 방지

Concurrency Control:
    - F() 객체 - 원자적 포인트 업데이트
    - select_for_update - 행 단위 락
    - metadata.expired 플래그 - 중복 만료 방지
"""

import threading
import time
from datetime import timedelta
from typing import Any

from django.db import connection
from django.utils import timezone

import pytest

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import PointHistoryFactory, UserFactory


# =============================================================================
# 헬퍼 함수
# =============================================================================


def close_db_connection():
    """스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수"""
    connection.close()


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestExpiryUsageRaceConditionInvariant:
    """
    핵심 불변 조건: 만료 처리와 포인트 사용 간 경합

    Purpose:
        동시 만료/사용 처리 시 데이터 정합성 보장
    Type:
        Core invariant test (must never fail)
    """

    def test_concurrent_expiry_and_usage_no_double_deduction(self):
        """
        Purpose:
            만료 처리와 사용이 동시에 발생해도 중복 차감 없음
        Scenario:
            2000P 만료 + 500P 사용 동시 시도
        Expected:
            최종 잔액 0P (순서 무관하게 정합성 유지)
        Concurrency Control:
            select_for_update + metadata.expired 플래그
        """
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()

        # 이미 만료된 포인트 생성
        PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                result = service.use_points_fifo(user, 500)
                with lock:
                    results.append({"action": "use", "success": result["success"]})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "error": str(e)})
            finally:
                close_db_connection()

        def expire_points_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})
            finally:
                close_db_connection()

        # Act
        t1 = threading.Thread(target=use_points_thread)
        t2 = threading.Thread(target=expire_points_thread)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert
        use_result = next((r for r in results if r.get("action") == "use"), None)
        expire_result = next((r for r in results if r.get("action") == "expire"), None)

        assert use_result is not None, "포인트 사용 결과 없음"
        assert expire_result is not None, "만료 처리 결과 없음"
        assert use_result.get("success") is True, f"포인트 사용 실패: {use_result}"

        # 최종 잔액 검증 (순서 무관하게 0P)
        user.refresh_from_db()
        assert user.points == 0, f"최종 잔액 0P. 실제: {user.points}"

    def test_concurrent_multiple_users_expiry_and_usage(self):
        """
        Purpose:
            여러 사용자의 만료/사용 동시 처리 시 각 사용자별 정합성
        Scenario:
            3명 사용자, 각 1000P 만료 + 300P 사용 동시 시도
        Expected:
            각 사용자 최종 잔액 0P
        """
        # Arrange
        users = []
        for _ in range(3):
            user = UserFactory.with_points(1000)
            users.append(user)
            PointHistoryFactory.earn(
                user=user,
                points=1000,
                balance=user.points,
                expires_at=timezone.now() - timedelta(days=1),
            )

        service = PointService()
        results = []
        lock = threading.Lock()

        def use_points_for_user(u):
            try:
                result = service.use_points_fifo(u, 300)
                with lock:
                    results.append({"user": u.username, "action": "use", "success": result["success"]})
            except Exception as e:
                with lock:
                    results.append({"user": u.username, "action": "use", "error": str(e)})
            finally:
                close_db_connection()

        def expire_all_points():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire_all", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire_all", "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points_for_user, args=(u,)) for u in users]
        threads.append(threading.Thread(target=expire_all_points))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        use_results = [r for r in results if r.get("action") == "use"]
        expire_result = next((r for r in results if r.get("action") == "expire_all"), None)

        assert len(use_results) == 3, "3명 모두 사용 시도"
        assert expire_result is not None, "만료 처리 실행"

        for user in users:
            user.refresh_from_db()
            assert user.points == 0, f"{user.username} 최종 잔액 0P. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestFIFOConcurrencyInvariant:
    """
    핵심 불변 조건: FIFO 순서 보장

    Purpose:
        동시 사용 시에도 오래된 포인트(만료 임박)부터 차감
    Type:
        Core invariant test (must never fail)
    """

    def test_fifo_order_preserved_on_concurrent_use(self):
        """
        Purpose:
            동시 사용 시 FIFO 순서 보장
        Scenario:
            3개 포인트 (6개월/9개월/1년 후 만료), 2개 스레드 100P/200P 동시 사용
        Expected:
            가장 오래된 포인트에서만 300P 차감
        """
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()
        now = timezone.now()

        # 만료일 기준 정렬된 포인트들
        old_point = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=500,
            expires_at=now + timedelta(days=180),
        )
        mid_point = PointHistoryFactory.earn(
            user=user,
            points=300,
            balance=800,
            expires_at=now + timedelta(days=270),
        )
        new_point = PointHistoryFactory.earn(
            user=user,
            points=200,
            balance=1000,
            expires_at=now + timedelta(days=365),
        )

        user.points = 1000
        user.save()

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        t1 = threading.Thread(target=use_points_thread, args=(100,))
        t2 = threading.Thread(target=use_points_thread, args=(200,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == 2, f"2개 모두 성공. 성공: {success_count}"

        user.refresh_from_db()
        assert user.points == 700, f"최종 잔액 700P. 실제: {user.points}"

        # FIFO 순서 확인
        old_point.refresh_from_db()
        mid_point.refresh_from_db()
        new_point.refresh_from_db()

        total_used_from_old = old_point.metadata.get("used_amount", 0)
        total_used_from_mid = mid_point.metadata.get("used_amount", 0)
        total_used_from_new = new_point.metadata.get("used_amount", 0)

        assert total_used_from_old == 300, f"가장 오래된 포인트 300P 사용. 실제: {total_used_from_old}"
        assert total_used_from_mid == 0, f"중간 포인트는 미사용. 실제: {total_used_from_mid}"
        assert total_used_from_new == 0, f"최신 포인트는 미사용. 실제: {total_used_from_new}"

    def test_expiring_soon_used_first_with_10_threads(self):
        """
        Purpose:
            만료 임박 포인트가 먼저 전액 사용됨
        Scenario:
            만료 임박 1000P + 안전 1000P, 10명 동시 100P 사용
        Expected:
            만료 임박 포인트 전액 사용, 안전 포인트 미사용
        """
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()
        now = timezone.now()

        expiring_soon = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=1),
        )
        safe_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=365),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                result = service.use_points_fifo(user, 100)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == 10, f"10명 모두 성공. 성공: {success_count}"

        user.refresh_from_db()
        assert user.points == 1000, f"최종 잔액 1000P. 실제: {user.points}"

        expiring_soon.refresh_from_db()
        safe_point.refresh_from_db()

        assert expiring_soon.metadata.get("used_amount", 0) == 1000, "만료 임박 포인트 전액 사용"
        assert safe_point.metadata.get("used_amount", 0) == 0, "여유 포인트는 미사용"


# =============================================================================
# B. 예외 케이스 테스트 (Exception Cases)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestExpiryExceptionCases:
    """
    예외 케이스: 만료 처리 중 충돌 및 에러

    Purpose:
        비정상 상황에서도 데이터 정합성 유지
    """

    def test_no_expiry_on_already_fully_used_points(self):
        """
        Purpose:
            이미 전액 사용된 포인트는 만료 처리되지 않음
        Scenario:
            1000P 전액 사용 후 만료 처리 3회 동시 시도
        Expected:
            만료 처리 0건, 중복 차감 없음
        Concurrency Control:
            metadata.expired 플래그 확인
        """
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 포인트 전액 사용
        result = service.use_points_fifo(user, 1000)
        assert result["success"]

        results = []
        lock = threading.Lock()

        def expire_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=expire_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_expired = sum(r.get("count", 0) for r in results)
        assert total_expired == 0, f"전액 사용되어 만료 0건. 실제: {total_expired}"

        user.refresh_from_db()
        assert user.points == 0, f"잔액 0P 유지. 실제: {user.points}"

        expire_history = PointHistory.objects.filter(user=user, type="expire")
        assert expire_history.count() == 0, "만료 이력 미생성"

    def test_partial_expiry_on_partially_used_points(self):
        """
        Purpose:
            부분 사용된 포인트는 남은 부분만 만료 처리
        Scenario:
            2000P 중 800P 사용 후 만료 처리 2회 동시 시도
        Expected:
            1건만 만료 처리 (1200P), 최종 잔액 0P
        """
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        result = service.use_points_fifo(user, 800)
        assert result["success"]

        results = []
        lock = threading.Lock()

        def expire_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=expire_thread) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_expired = sum(r.get("count", 0) for r in results)
        assert total_expired == 1, f"1건만 만료 처리. 실제: {total_expired}"

        user.refresh_from_db()
        assert user.points == 0, f"최종 잔액 0P. 실제: {user.points}"

        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None, "만료 이력 생성"
        assert expire_history.points == -1200, f"1200P 만료. 실제: {expire_history.points}"

    def test_no_negative_balance_on_insufficient_points(self):
        """
        Purpose:
            잔액 부족 시에도 음수가 되지 않음
        Scenario:
            500P 잔액에서 5명이 200P씩 사용 시도
        Expected:
            2명만 성공, 최종 잔액 100P
        """
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                result = service.use_points_fifo(user, 200)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        failed_count = sum(1 for r in results if not r.get("success"))

        assert success_count == 2, f"2명만 성공. 성공: {success_count}"
        assert failed_count == 3, f"3명 실패. 실패: {failed_count}"

        user.refresh_from_db()
        assert user.points == 100, f"최종 잔액 100P. 실제: {user.points}"
        assert user.points >= 0, "잔액은 음수가 될 수 없음"

    def test_no_reprocessing_of_already_expired(self):
        """
        Purpose:
            이미 만료 플래그가 설정된 포인트는 재처리되지 않음
        Scenario:
            수동으로 만료 표시된 포인트에 만료 처리 시도
        Expected:
            만료 처리 0건, 잔액 변화 없음
        """
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 수동으로 만료 표시
        expired_point.metadata["expired"] = True
        expired_point.save(update_fields=["metadata"])

        # Act
        count = service.expire_points()

        # Assert
        assert count == 0, f"이미 만료 처리되어 0건. 실제: {count}"

        user.refresh_from_db()
        assert user.points == 1000, f"잔액 유지. 실제: {user.points}"


# =============================================================================
# C. 스케일 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
@pytest.mark.slow
class TestExpiryScaleTest:
    """
    스케일 테스트: 복잡한 경합 상황

    Purpose:
        다수의 동시 작업에서 시스템 안정성 검증
    """

    def test_mixed_operations_15_use_5_expire(self):
        """
        Purpose:
            20개 동시 작업 스트레스 테스트
        Scenario:
            10000P, 15개 사용(각 200P) + 5개 만료 처리 동시 실행
        Expected:
            모든 작업 완료, 잔액 >= 0, 데이터 일관성 유지
        """
        # Arrange
        user = UserFactory.with_points(10000)
        service = PointService()
        now = timezone.now()

        # 5개 포인트 (2개 만료됨, 3개 유효)
        for i in range(5):
            PointHistoryFactory.earn(
                user=user,
                points=1000,
                balance=(i + 1) * 1000,
                expires_at=now - timedelta(days=1) if i < 2 else now + timedelta(days=30),
            )

        results = []
        lock = threading.Lock()

        def use_points_thread():
            try:
                result = service.use_points_fifo(user, 200)
                with lock:
                    results.append({"action": "use", "success": result.get("success")})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "error": str(e)})
            finally:
                close_db_connection()

        def expire_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = []
        for _ in range(15):
            threads.append(threading.Thread(target=use_points_thread))
        for _ in range(5):
            threads.append(threading.Thread(target=expire_thread))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        use_results = [r for r in results if r.get("action") == "use"]
        expire_results = [r for r in results if r.get("action") == "expire"]

        assert len(use_results) == 15, "15개 사용 시도"
        assert len(expire_results) == 5, "5개 만료 처리 시도"

        user.refresh_from_db()
        assert user.points >= 0, f"잔액 0 이상. 실제: {user.points}"

        # 데이터 일관성 확인
        latest_history = PointHistory.objects.filter(user=user).order_by("-created_at").first()
        if latest_history:
            assert latest_history.balance == user.points, (
                f"이력 잔액 일치. 이력: {latest_history.balance}, 실제: {user.points}"
            )

    def test_complex_mix_operations(self):
        """
        Purpose:
            만료 처리 + 여러 사용 + FIFO 순서 복합 시나리오
        Scenario:
            3000P (만료 1000 + 임박 1000 + 안전 1000)
            3개 사용(200/300/500P) + 1개 만료 처리 동시 실행
        Expected:
            잔액 >= 0, F() + select_for_update로 순서 보장
        """
        # Arrange
        user = UserFactory.with_points(3000)
        service = PointService()
        now = timezone.now()

        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now - timedelta(days=1),  # 만료됨
        )
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=1),  # 임박
        )
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=3000,
            expires_at=now + timedelta(days=365),  # 안전
        )

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            time.sleep(0.001 * amount / 100)
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append({"action": "use", "amount": amount, "success": result.get("success")})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "amount": amount, "error": str(e)})
            finally:
                close_db_connection()

        def expire_thread():
            time.sleep(0.002)
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [
            threading.Thread(target=use_points_thread, args=(200,)),
            threading.Thread(target=use_points_thread, args=(300,)),
            threading.Thread(target=use_points_thread, args=(500,)),
            threading.Thread(target=expire_thread),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        use_results = [r for r in results if r.get("action") == "use"]
        expire_result = next((r for r in results if r.get("action") == "expire"), None)

        assert len(use_results) == 3, "3개 사용 시도"
        assert expire_result is not None, "만료 처리 실행"

        user.refresh_from_db()
        assert user.points >= 0, f"잔액 0 이상. 실제: {user.points}"

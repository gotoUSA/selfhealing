"""
PointService 동시성 테스트

Purpose:
    여러 스레드에서 동시에 포인트 추가/차감/만료 처리 시 데이터 정합성 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 포인트 음수 방지
        - 중복 만료 처리 방지
        - FIFO 순서 보장
    B. Parameterized Scenarios:
        - 다양한 사용자 수에 대한 스케일 테스트

Concurrency Control:
    - F() 객체 - 원자적 포인트 업데이트
    - select_for_update - 행 단위 락
    - metadata.expired 플래그 - 중복 만료 방지
"""

import threading
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


def run_concurrent_point_operations(
    operation_func,
    thread_count: int,
    *args,
    **kwargs,
) -> list[dict[str, Any]]:
    """
    동시 포인트 작업 실행 헬퍼

    Args:
        operation_func: 실행할 함수
        thread_count: 스레드 수
        *args, **kwargs: operation_func에 전달할 인자

    Returns:
        각 스레드의 결과 리스트
    """
    results = []
    lock = threading.Lock()

    def wrapper():
        try:
            result = operation_func(*args, **kwargs)
            with lock:
                results.append({"success": result, "error": None})
        except Exception as e:
            with lock:
                results.append({"success": False, "error": str(e)})
        finally:
            close_db_connection()

    threads = [threading.Thread(target=wrapper) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestPointAddConcurrencyInvariant:
    """
    핵심 불변 조건: 포인트 추가 원자성

    Purpose:
        동시 포인트 추가 시 F() 객체를 통한 원자적 업데이트 검증
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        F() atomic update
    """

    def test_concurrent_add_points_atomic(self):
        """
        Purpose:
            5개 스레드가 동시에 100P씩 추가 시 최종 1500P
        Scenario:
            Initial: 1000P, 5 threads add 100P each simultaneously
        Expected:
            Final: 1500P (no lost updates)
        """
        # Arrange
        user = UserFactory.with_points(1000)
        initial_points = user.points

        results = []
        lock = threading.Lock()

        def add_points():
            try:
                success = PointService.add_points(user, 100, type="earn")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=add_points) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        user.refresh_from_db()

        assert success_count == 5, f"5개 모두 성공해야 함. 성공: {success_count}"
        assert user.points == initial_points + (100 * 5), f"최종 포인트는 1500P. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestPointUseConcurrencyInvariant:
    """
    핵심 불변 조건: 포인트 차감 원자성 및 음수 방지

    Purpose:
        동시 포인트 차감 시 음수가 되지 않도록 보장
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        select_for_update + balance check
    """

    def test_points_never_go_negative_with_concurrent_use(self):
        """
        Purpose:
            잔액 250P에서 5명이 100P씩 차감 시도 -> 2명만 성공
        Scenario:
            Initial: 250P, 5 threads try to use 100P each
        Expected:
            2 succeed, 3 fail, final: 50P (never negative)
        """
        # Arrange
        user = UserFactory.with_points(250)

        results = []
        lock = threading.Lock()

        def use_points():
            try:
                success = PointService.use_points(user, 100, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        user.refresh_from_db()

        assert success_count == 2, f"2개만 성공해야 함 (250/100=2). 성공: {success_count}"
        assert user.points == 50, f"최종 포인트는 50P. 실제: {user.points}"
        assert user.points >= 0, "포인트는 절대 음수가 되어서는 안 됨"

    @pytest.mark.parametrize(
        "initial_points,use_amount,thread_count,expected_success",
        [
            (1000, 100, 5, 5),  # 충분한 잔액
            (500, 100, 10, 5),  # 절반만 성공
            (100, 100, 5, 1),  # 1개만 성공
            (50, 100, 3, 0),  # 모두 실패
        ],
    )
    def test_concurrent_use_points_boundary(
        self,
        initial_points: int,
        use_amount: int,
        thread_count: int,
        expected_success: int,
    ):
        """
        Purpose:
            다양한 잔액/차감 조합에서 경계값 테스트
        Scenario:
            Initial: {initial_points}P, {thread_count} threads use {use_amount}P each
        Expected:
            {expected_success} succeed, points >= 0
        """
        # Arrange
        user = UserFactory.with_points(initial_points)

        results = []
        lock = threading.Lock()

        def use_points():
            try:
                success = PointService.use_points(user, use_amount, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        user.refresh_from_db()

        assert success_count == expected_success, f"{expected_success}개 성공 예상. 실제: {success_count}"
        assert user.points >= 0, "포인트는 음수가 되어서는 안 됨"
        assert user.points == initial_points - (expected_success * use_amount), "잔액 계산 정확성"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestPointExpireConcurrencyInvariant:
    """
    핵심 불변 조건: 만료 처리 중복 방지

    Purpose:
        동시 만료 처리 시 1회만 처리되도록 보장
    Type:
        Core invariant test (must never fail)
    Concurrency Control:
        metadata.expired 플래그 + select_for_update
    """

    def test_expire_points_only_once(self):
        """
        Purpose:
            3개 스레드가 동시에 만료 처리 시도 -> 1건만 처리
        Scenario:
            1 expired point record, 3 threads try to expire simultaneously
        Expected:
            Total expired count = 1 (no duplicate processing)
        """
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 만료된 포인트 1건 생성
        PointHistoryFactory.earn(
            user=user,
            points=100,
            expires_at=timezone.now() - timedelta(days=1),
        )

        results = []
        lock = threading.Lock()

        def expire_points():
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
        threads = [threading.Thread(target=expire_points) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_count = sum(r.get("count", 0) for r in results)
        user.refresh_from_db()

        assert total_count == 1, f"총 1건만 만료 처리되어야 함. 실제: {total_count}"
        assert user.points == 900, f"최종 포인트는 900P. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
class TestPointFIFOConcurrencyInvariant:
    """
    핵심 불변 조건: FIFO 순서 보장

    Purpose:
        동시 포인트 사용 시 FIFO(선입선출) 순서가 보장되는지 검증
    Type:
        Core invariant test (must never fail)
    """

    def test_fifo_order_preserved_on_concurrent_use(self):
        """
        Purpose:
            동시 포인트 사용 시 만료 임박 포인트부터 차감
        Scenario:
            2 point records (expire soon vs safe), concurrent usage
        Expected:
            Expiring soon points used first (FIFO preserved)
        """
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()
        now = timezone.now()

        # 만료 임박 포인트 (1일 후)
        expiring_soon = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=1),
        )

        # 여유 있는 포인트 (1년 후)
        safe_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=365),
        )

        results = []
        lock = threading.Lock()

        def use_points(amount: int):
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act - 10명이 동시에 100P씩 사용 (총 1000P)
        threads = [threading.Thread(target=use_points, args=(100,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        user.refresh_from_db()
        expiring_soon.refresh_from_db()
        safe_point.refresh_from_db()

        assert success_count == 10, f"10명 모두 성공. 성공: {success_count}"
        assert user.points == 1000, f"최종 잔액 1000P. 실제: {user.points}"

        # FIFO 확인: 만료 임박 포인트가 먼저 전액 사용됨
        assert expiring_soon.metadata.get("used_amount", 0) == 1000, "만료 임박 포인트 전액 사용"
        assert safe_point.metadata.get("used_amount", 0) == 0, "여유 포인트는 미사용"


# =============================================================================
# B. 스케일 테스트
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.point_race
@pytest.mark.slow
class TestPointConcurrencyScale:
    """
    스케일 검증 테스트

    Purpose:
        중규모 동시 포인트 작업에서 시스템 안정성 검증
    """

    def test_20_concurrent_point_use(self):
        """
        Purpose:
            20명 동시 포인트 사용 - 중간 스케일 검증
        Scenario:
            Initial: 20000P, 20 threads use 1000P each
        Expected:
            All succeed, final: 0P
        """
        # Arrange
        user = UserFactory.with_points(20_000)

        results = []
        lock = threading.Lock()

        def use_points():
            try:
                success = PointService.use_points(user, 1000, type="use")
                with lock:
                    results.append({"success": success})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})
            finally:
                close_db_connection()

        # Act
        threads = [threading.Thread(target=use_points) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        user.refresh_from_db()

        assert success_count == 20, f"20개 모두 성공. 성공: {success_count}"
        assert user.points == 0, f"최종 포인트는 0P. 실제: {user.points}"

        # 이력 검증
        histories = PointHistory.objects.filter(user=user, type="use")
        assert histories.count() == 20, "이력 20개 생성"
        assert all(h.points == -1000 for h in histories), "각 이력은 -1000P"

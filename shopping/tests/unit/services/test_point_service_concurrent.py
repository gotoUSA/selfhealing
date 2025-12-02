"""
PointService 단위 테스트 - 동시성

여러 스레드에서 동시에 포인트 추가/차감/만료 처리 시
데이터 정합성이 유지되는지 검증합니다.

F() 객체와 select_for_update를 통한 동시성 제어 테스트.
"""

import threading
from datetime import timedelta

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import (
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# 포인트 추가 동시성 테스트
# ==========================================


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

        # Act - 5개 스레드 동시 실행
        threads = [threading.Thread(target=add_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모든 스레드 성공
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        # Assert - 최종 포인트 정확성
        user.refresh_from_db()
        assert user.points == 1000 + (100 * 5)


# ==========================================
# 포인트 차감 동시성 테스트
# ==========================================


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

        # Act - 5개 스레드 동시 실행
        threads = [threading.Thread(target=use_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모든 스레드 성공 (충분한 잔액)
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        # Assert - 최종 포인트 정확성
        user.refresh_from_db()
        assert user.points == 1000 - (100 * 5)

    def test_use_points_concurrency_insufficient(self):
        """잔액 부족 시 동시 차감 (일부만 성공)"""
        # Arrange - 250포인트로 100씩 5번 차감 시도
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

        # Act - 5개 스레드 동시 실행
        threads = [threading.Thread(target=use_points_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 2개만 성공 (250 / 100 = 2)
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 2

        # Assert - 최종 포인트 정확성
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

        # Act - 20개 스레드 동시 실행
        threads = [threading.Thread(target=use_points_thread) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모든 스레드 성공
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 20

        # Assert - 최종 포인트 0
        user.refresh_from_db()
        assert user.points == 0

        # Assert - 이력 20개 생성
        histories = PointHistory.objects.filter(user=user, type="use").order_by("created_at")
        assert histories.count() == 20
        assert all(h.points == -1000 for h in histories)


# ==========================================
# 만료 처리 동시성 테스트
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestPointServiceExpirePointsConcurrency:
    """만료 처리 동시성 테스트"""

    def test_expire_points_concurrency(self):
        """여러 스레드에서 동시 만료 처리 (중복 방지 검증)"""
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

        def expire_points_thread():
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 3개 스레드 동시 실행
        threads = [threading.Thread(target=expire_points_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 총 1건만 처리됨 (중복 방지)
        total_count = sum(r.get("count", 0) for r in results)
        assert total_count == 1

        # Assert - 최종 포인트 정확성
        user.refresh_from_db()
        assert user.points == 900


# ==========================================
# FIFO 사용 동시성 테스트
# ==========================================


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

        # Act - 5개 스레드 동시 실행
        threads = [threading.Thread(target=use_fifo_thread) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - 모든 스레드 성공
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        # Assert - 최종 포인트 정확성
        user.refresh_from_db()
        assert user.points == 500

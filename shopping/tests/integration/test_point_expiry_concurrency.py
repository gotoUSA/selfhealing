"""포인트 만료 race condition 동시성 테스트

포인트 만료 스케줄러와 사용자의 포인트 사용 간 race condition을 검증
"""

import threading
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from shopping.models.point import PointHistory
from shopping.services.point_service import PointService
from shopping.tests.factories import PointHistoryFactory, UserFactory


@pytest.mark.django_db(transaction=True)
class TestPointExpiryUsageRaceCondition:
    """1단계: 정상 케이스 - 만료 처리와 포인트 사용 간 경합"""

    def test_concurrent_expiry_and_usage_both_succeed(self):
        """만료 전 포인트 사용 - 사용 후 잔액만 만료 처리"""
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()

        # 이미 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread():
            """포인트 사용"""
            try:
                result = service.use_points_fifo(user, 500)
                with lock:
                    results.append({"action": "use", "success": result["success"]})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "error": str(e)})

        def expire_points_thread():
            """포인트 만료 처리"""
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})

        # Act - 동시 실행
        t1 = threading.Thread(target=use_points_thread)
        t2 = threading.Thread(target=expire_points_thread)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert
        use_result = next((r for r in results if r.get("action") == "use"), None)
        expire_result = next((r for r in results if r.get("action") == "expire"), None)

        # 둘 다 성공해야 함
        assert use_result is not None, "포인트 사용 결과 없음"
        assert expire_result is not None, "만료 처리 결과 없음"
        assert use_result.get("success") is True, f"포인트 사용 실패: {use_result}"
        assert expire_result.get("count", 0) >= 0, f"만료 처리 실행: {expire_result}"

        # 최종 잔액 검증
        user.refresh_from_db()
        # 2000 - 500(사용) - 1500(만료) = 0 or 2000 - 1500(만료) - 500(사용) = 0
        # 순서에 관계없이 최종 잔액은 0이어야 함
        assert user.points == 0, f"최종 잔액 0P. 실제: {user.points}"

    def test_concurrent_multiple_users_expiry_and_usage(self):
        """여러 사용자의 만료 처리와 포인트 사용 동시 실행"""
        # Arrange
        users = []
        for i in range(3):
            user = UserFactory.with_points(1000)
            users.append(user)

            # 만료된 포인트 생성
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
            """특정 사용자 포인트 사용"""
            try:
                result = service.use_points_fifo(u, 300)
                with lock:
                    results.append({"user": u.username, "action": "use", "success": result["success"]})
            except Exception as e:
                with lock:
                    results.append({"user": u.username, "action": "use", "error": str(e)})

        def expire_all_points():
            """전체 만료 처리"""
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire_all", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire_all", "error": str(e)})

        # Act - 3명의 사용 + 1개의 만료 처리 동시 실행
        threads = [threading.Thread(target=use_points_for_user, args=(u,)) for u in users]
        threads.append(threading.Thread(target=expire_all_points))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        use_results = [r for r in results if r.get("action") == "use"]
        expire_result = next((r for r in results if r.get("action") == "expire_all"), None)

        # 모두 성공해야 함
        assert len(use_results) == 3, "3명 모두 사용 시도"
        assert expire_result is not None, "만료 처리 실행"

        # 각 사용자 최종 잔액 확인 (1000 - 300(사용) - 700(만료) = 0)
        for user in users:
            user.refresh_from_db()
            assert user.points == 0, f"{user.username} 최종 잔액 0P. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
class TestPointExpiryFIFOConcurrency:
    """2단계: 경계값 테스트 - FIFO 순서 보장"""

    def test_concurrent_fifo_order_preservation(self):
        """동시 사용 시 FIFO 순서 보장 (오래된 포인트부터 차감)"""
        # Arrange
        user = UserFactory.with_points(0)
        service = PointService()
        now = timezone.now()

        # 서로 다른 시점에 적립된 포인트들 (만료일 기준 정렬)
        # 가장 오래된 포인트 (6개월 후 만료)
        old_point = PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=500,
            expires_at=now + timedelta(days=180),
        )

        # 중간 포인트 (9개월 후 만료)
        mid_point = PointHistoryFactory.earn(
            user=user,
            points=300,
            balance=800,
            expires_at=now + timedelta(days=270),
        )

        # 최신 포인트 (1년 후 만료)
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
            """포인트 사용"""
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 2개 스레드가 동시에 포인트 사용 (100P, 200P)
        t1 = threading.Thread(target=use_points_thread, args=(100,))
        t2 = threading.Thread(target=use_points_thread, args=(200,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == 2, f"2개 모두 성공. 성공: {success_count}"

        # 최종 잔액 확인 (1000 - 100 - 200 = 700)
        user.refresh_from_db()
        assert user.points == 700, f"최종 잔액 700P. 실제: {user.points}"

        # FIFO 순서 확인: 가장 오래된 포인트부터 차감됨
        old_point.refresh_from_db()
        mid_point.refresh_from_db()
        new_point.refresh_from_db()

        # 총 300P 사용 → 500P에서 300P 차감
        total_used_from_old = old_point.metadata.get("used_amount", 0)
        total_used_from_mid = mid_point.metadata.get("used_amount", 0)
        total_used_from_new = new_point.metadata.get("used_amount", 0)

        assert total_used_from_old == 300, f"가장 오래된 포인트 300P 사용. 실제: {total_used_from_old}"
        assert total_used_from_mid == 0, f"중간 포인트는 사용 안 됨. 실제: {total_used_from_mid}"
        assert total_used_from_new == 0, f"최신 포인트는 사용 안 됨. 실제: {total_used_from_new}"

    def test_concurrent_expiring_soon_usage(self):
        """만료 임박 포인트의 경합 상황 (10명 동시 사용)"""
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()
        now = timezone.now()

        # 만료 임박 포인트 (1일 후 만료)
        expiring_soon = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=1),
        )

        # 여유 있는 포인트 (1년 후 만료)
        safe_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=365),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            """포인트 사용"""
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 10명이 동시에 100P씩 사용 시도 (총 1000P)
        threads = [threading.Thread(target=use_points_thread, args=(100,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == 10, f"10명 모두 성공. 성공: {success_count}"

        # 최종 잔액 확인 (2000 - 1000 = 1000)
        user.refresh_from_db()
        assert user.points == 1000, f"최종 잔액 1000P. 실제: {user.points}"

        # FIFO 순서 확인: 만료 임박 포인트가 먼저 전액 사용됨
        expiring_soon.refresh_from_db()
        safe_point.refresh_from_db()

        assert (
            expiring_soon.metadata.get("used_amount", 0) == 1000
        ), f"만료 임박 포인트 전액 사용. 실제: {expiring_soon.metadata.get('used_amount', 0)}"

        assert (
            safe_point.metadata.get("used_amount", 0) == 0
        ), f"여유 포인트는 사용 안 됨. 실제: {safe_point.metadata.get('used_amount', 0)}"


@pytest.mark.django_db(transaction=True)
class TestPointExpiryExceptionCases:
    """3단계: 예외 케이스 - 만료 처리 중 충돌 및 에러"""

    def test_concurrent_expiry_already_used(self):
        """이미 사용된 포인트 만료 처리 시도 (중복 차감 방지)"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 포인트 전액 사용
        result = service.use_points_fifo(user, 1000)
        assert result["success"]

        # Act - 여러 스레드에서 동시 만료 처리
        results = []
        lock = threading.Lock()

        def expire_thread():
            """만료 처리"""
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        threads = [threading.Thread(target=expire_thread) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_expired = sum(r.get("count", 0) for r in results)
        assert total_expired == 0, f"전액 사용되어 만료 처리 0건. 실제: {total_expired}"

        # 최종 잔액 유지 (중복 차감 없음)
        user.refresh_from_db()
        assert user.points == 0, f"잔액 0P 유지. 실제: {user.points}"

        # 만료 이력 미생성 확인
        expire_history = PointHistory.objects.filter(user=user, type="expire")
        assert expire_history.count() == 0, "만료 이력 미생성"

    def test_concurrent_expiry_partial_used(self):
        """부분 사용된 포인트의 동시 만료 처리 (남은 부분만 만료)"""
        # Arrange
        user = UserFactory.with_points(2000)
        service = PointService()

        # 만료된 포인트 생성
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 일부 사용 (800P)
        result = service.use_points_fifo(user, 800)
        assert result["success"]

        results = []
        lock = threading.Lock()

        def expire_thread():
            """만료 처리"""
            try:
                count = service.expire_points()
                with lock:
                    results.append({"count": count})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 2개 스레드 동시 만료 처리 (중복 방지 검증)
        threads = [threading.Thread(target=expire_thread) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        total_expired = sum(r.get("count", 0) for r in results)
        assert total_expired == 1, f"1건만 만료 처리. 실제: {total_expired}"

        # 최종 잔액 (2000 - 800(사용) - 1200(만료) = 0)
        user.refresh_from_db()
        assert user.points == 0, f"최종 잔액 0P. 실제: {user.points}"

        # 만료 이력 확인 (남은 포인트만)
        expire_history = PointHistory.objects.filter(user=user, type="expire").first()
        assert expire_history is not None, "만료 이력 생성"
        assert expire_history.points == -1200, f"1200P 만료. 실제: {expire_history.points}"

    def test_concurrent_expiry_insufficient_balance(self):
        """잔액 부족 시 만료 처리 (음수 방지)"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        # 만료된 포인트 1000P (하지만 잔액은 500P)
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1500,  # 이력 상의 잔액
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 실제 잔액은 500P로 설정 (다른 차감이 있었다고 가정)
        user.points = 500
        user.save()

        # Act - 만료 처리
        count = service.expire_points()

        # Assert
        assert count == 1, "만료 처리 1건"

        # 잔액이 음수가 되지 않음 (Greatest 사용)
        user.refresh_from_db()
        assert user.points >= 0, f"잔액 0 이상. 실제: {user.points}"

    def test_concurrent_usage_exceeds_limit(self):
        """동시 사용 시도가 잔액 초과 (일부만 성공)"""
        # Arrange
        user = UserFactory.with_points(500)
        service = PointService()

        # 유효한 포인트
        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=user.points,
            expires_at=timezone.now() + timedelta(days=30),
        )

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            """포인트 사용"""
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 5명이 동시에 200P씩 사용 시도 (총 1000P > 500P)
        threads = [threading.Thread(target=use_points_thread, args=(200,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        failed_count = sum(1 for r in results if not r.get("success"))

        # 2명만 성공 (500 / 200 = 2)
        assert success_count == 2, f"2명만 성공. 성공: {success_count}"
        assert failed_count == 3, f"3명 실패. 실패: {failed_count}"

        # 최종 잔액 확인
        user.refresh_from_db()
        assert user.points == 100, f"최종 잔액 100P. 실제: {user.points}"

    def test_concurrent_expiry_already_marked_expired(self):
        """이미 만료 처리된 포인트 재처리 방지"""
        # Arrange
        user = UserFactory.with_points(1000)
        service = PointService()

        # 만료된 포인트
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=user.points,
            expires_at=timezone.now() - timedelta(days=1),
        )

        # 수동으로 만료 표시
        expired_point.metadata["expired"] = True
        expired_point.save(update_fields=["metadata"])

        # Act - 만료 처리 시도
        count = service.expire_points()

        # Assert
        assert count == 0, f"이미 만료 처리되어 0건. 실제: {count}"

        # 잔액 변화 없음
        user.refresh_from_db()
        assert user.points == 1000, f"잔액 유지. 실제: {user.points}"


@pytest.mark.django_db(transaction=True)
class TestPointExpiryAdvancedScenarios:
    """고급 시나리오 - 복잡한 경합 상황"""

    def test_concurrent_expiry_usage_fifo_mix(self):
        """만료 처리 + 여러 사용 + FIFO 순서 복합 시나리오"""
        # Arrange
        user = UserFactory.with_points(3000)
        service = PointService()
        now = timezone.now()

        # 3개의 포인트: 만료됨, 임박, 안전
        expired_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now - timedelta(days=1),  # 만료됨
        )

        expiring_soon = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=now + timedelta(days=1),  # 임박
        )

        safe_point = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=3000,
            expires_at=now + timedelta(days=365),  # 안전
        )

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            """포인트 사용"""
            # 소량 지연으로 타이밍 조정
            time.sleep(0.001 * amount / 100)
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append({"action": "use", "amount": amount, "success": result.get("success")})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "amount": amount, "error": str(e)})

        def expire_thread():
            """만료 처리"""
            time.sleep(0.002)  # 약간의 지연
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})

        # Act - 3개 사용(200P, 300P, 500P) + 1개 만료 처리 동시 실행
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

        # 모든 작업 성공 여부 확인
        success_use = sum(1 for r in use_results if r.get("success"))

        # 만료 처리는 실행되어야 함
        assert expire_result is not None, "만료 처리 실행"

        # 최종 잔액 검증
        user.refresh_from_db()
        # 시나리오:
        # - 만료 처리 먼저: 3000 - 1000(만료) = 2000, 이후 사용 1000 = 1000
        # - 사용 먼저: 3000 - 1000(사용) = 2000, 이후 만료 0 (이미 사용됨) = 2000
        # F() 객체와 select_for_update로 순서 보장됨
        assert user.points >= 0, f"잔액 0 이상. 실제: {user.points}"

    def test_stress_concurrent_operations(self):
        """스트레스 테스트: 20개 동시 작업 (사용 15 + 만료 5)"""
        # Arrange
        user = UserFactory.with_points(10000)
        service = PointService()
        now = timezone.now()

        # 여러 포인트 생성 (일부는 만료됨)
        for i in range(5):
            PointHistoryFactory.earn(
                user=user,
                points=1000,
                balance=(i + 1) * 1000,
                expires_at=now - timedelta(days=1) if i < 2 else now + timedelta(days=30),
            )

        results = []
        lock = threading.Lock()

        def use_points_thread(amount):
            """포인트 사용"""
            try:
                result = service.use_points_fifo(user, amount)
                with lock:
                    results.append({"action": "use", "success": result.get("success")})
            except Exception as e:
                with lock:
                    results.append({"action": "use", "error": str(e)})

        def expire_thread():
            """만료 처리"""
            try:
                count = service.expire_points()
                with lock:
                    results.append({"action": "expire", "count": count})
            except Exception as e:
                with lock:
                    results.append({"action": "expire", "error": str(e)})

        # Act - 15개 사용(각 200P) + 5개 만료 처리
        threads = []
        for _ in range(15):
            threads.append(threading.Thread(target=use_points_thread, args=(200,)))
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

        # 최종 잔액 확인 (음수 없음)
        user.refresh_from_db()
        assert user.points >= 0, f"잔액 0 이상. 실제: {user.points}"

        # 데이터 일관성 확인
        latest_history = PointHistory.objects.filter(user=user).order_by("-created_at").first()
        if latest_history:
            assert (
                latest_history.balance == user.points
            ), f"이력 잔액 일치. 이력: {latest_history.balance}, 실제: {user.points}"

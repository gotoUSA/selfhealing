"""
point_views.py 테스트

테스트 범위:
- MyPointView: 내 포인트 정보 조회
- PointHistoryListView: 포인트 이력 목록 (필터링, 페이지네이션)
- PointCheckView: 포인트 사용 가능 여부 확인
- ExpiringPointsView: 만료 예정 포인트 조회
- point_statistics: 포인트 통계 정보
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# MyPointView 테스트
# ==========================================


@pytest.mark.django_db
class TestMyPointView:
    """내 포인트 정보 조회 테스트"""

    def test_returns_point_info_and_recent_histories(self, api_client):
        """포인트 정보와 최근 이력 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user)
        PointHistoryFactory.earn(user=user, points=1000, balance=1000, order=order)
        PointHistoryFactory.use(user=user, points=-500, balance=500)
        api_client.force_authenticate(user=user)

        url = reverse("my_points")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "point_info" in response.data
        assert "recent_histories" in response.data
        assert response.data["point_info"]["points"] == 5000
        assert len(response.data["recent_histories"]) == 2

    def test_returns_max_5_recent_histories(self, api_client):
        """이력이 5개 초과해도 최근 5개만 반환"""
        # Arrange
        user = UserFactory.with_points(10000)
        for i in range(7):
            PointHistoryFactory.earn(user=user, points=100, balance=100 * (i + 1))
        api_client.force_authenticate(user=user)

        url = reverse("my_points")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["recent_histories"]) == 5

    def test_unauthenticated_returns_403(self, api_client):
        """미인증 사용자 403 반환"""
        # Arrange
        url = reverse("my_points")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ==========================================
# PointHistoryListView 테스트
# ==========================================


@pytest.mark.django_db
class TestPointHistoryListView:
    """포인트 이력 목록 조회 테스트"""

    def test_returns_paginated_history_with_summary(self, api_client):
        """페이지네이션된 이력과 summary 반환"""
        # Arrange
        user = UserFactory.with_points(1500)
        PointHistoryFactory.earn(user=user, points=2000, balance=2000)
        PointHistoryFactory.use(user=user, points=-500, balance=1500)
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "count" in response.data
        assert "page" in response.data
        assert "summary" in response.data
        assert "results" in response.data
        assert response.data["summary"]["current_points"] == 1500
        assert response.data["summary"]["total_earned"] == 2000
        assert response.data["summary"]["total_used"] == 500

    def test_filter_by_type_earned_and_used(self, api_client):
        """earned/used 타입으로 필터링"""
        # Arrange
        user = UserFactory.with_points(1000)
        PointHistoryFactory.earn(user=user, points=1000, balance=1000)
        PointHistoryFactory.earn(user=user, points=500, balance=1500)
        PointHistoryFactory.use(user=user, points=-200, balance=1300)
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act - earned 필터
        response_earned = api_client.get(url, {"type": "earned"})

        # Assert
        assert response_earned.status_code == status.HTTP_200_OK
        assert response_earned.data["count"] == 2
        assert all(h["points"] > 0 for h in response_earned.data["results"])

        # Act - used 필터
        response_used = api_client.get(url, {"type": "used"})

        # Assert
        assert response_used.status_code == status.HTTP_200_OK
        assert response_used.data["count"] == 1
        assert all(h["points"] < 0 for h in response_used.data["results"])

    def test_filter_by_date_range(self, api_client):
        """날짜 범위로 필터링"""
        # Arrange
        user = UserFactory.with_points(3000)
        now = timezone.now()

        history1 = PointHistoryFactory.earn(user=user, points=1000, balance=1000)
        history1.created_at = now - timedelta(days=14)
        history1.save(update_fields=["created_at"])

        history2 = PointHistoryFactory.earn(user=user, points=1000, balance=2000)
        history2.created_at = now - timedelta(days=5)
        history2.save(update_fields=["created_at"])

        history3 = PointHistoryFactory.earn(user=user, points=1000, balance=3000)
        # history3은 현재 시간 (기본값)

        api_client.force_authenticate(user=user)
        url = reverse("point_history")

        # Act - 5~10일 전 범위 (YYYY-MM-DD format)
        start_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        end_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        response = api_client.get(
            url,
            {
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_custom_page_and_page_size(self, api_client):
        """page와 page_size 파라미터 동작 확인"""
        # Arrange
        user = UserFactory.with_points(5000)
        for i in range(15):
            PointHistoryFactory.earn(user=user, points=100, balance=100 * (i + 1))
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act
        response = api_client.get(url, {"page": 2, "page_size": 5})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["page"] == 2
        assert response.data["page_size"] == 5
        assert len(response.data["results"]) == 5

    def test_unauthenticated_returns_403(self, api_client):
        """미인증 사용자 403 반환"""
        # Arrange
        url = reverse("point_history")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_start_date_format_returns_400(self, api_client):
        """잘못된 start_date 형식 - 400 에러 (DateParseError)"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act - 잘못된 날짜 형식 (YYYY-MM-DD가 아님)
        response = api_client.get(url, {"start_date": "2025/01/15"})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "start_date" in response.data["error"]

    def test_invalid_end_date_format_returns_400(self, api_client):
        """잘못된 end_date 형식 - 400 에러 (DateParseError)"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act - 잘못된 날짜 형식
        response = api_client.get(url, {"end_date": "15-01-2025"})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "end_date" in response.data["error"]

    def test_invalid_date_text_returns_400(self, api_client):
        """문자열 날짜 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_history")

        # Act - 텍스트 입력
        response = api_client.get(url, {"start_date": "invalid-date"})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data


# ==========================================
# PointCheckView 테스트
# ==========================================


@pytest.mark.django_db
class TestPointCheckView:
    """포인트 사용 가능 여부 확인 테스트"""

    def test_can_use_points_success(self, api_client):
        """사용 가능한 포인트 확인 시 can_use=True 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 3000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_use"] is True
        assert response.data["available_points"] == 5000
        assert response.data["max_usable"] == 5000
        assert "3000" in response.data["message"]

    def test_insufficient_points_returns_cannot_use(self, api_client):
        """보유 포인트 부족 시 can_use=False 반환"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 5000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_use"] is False
        assert "부족" in response.data["message"]

    def test_minimum_100_points_required(self, api_client):
        """100 포인트 미만 사용 시 can_use=False 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 50}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_use"] is False
        assert "100" in response.data["message"]

    def test_unauthenticated_returns_403(self, api_client):
        """미인증 사용자 403 반환"""
        # Arrange
        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 500}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_order_amount_returns_400(self, api_client):
        """order_amount 누락 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"use_points": 500}  # order_amount 누락

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "order_amount" in response.data

    def test_missing_use_points_returns_400(self, api_client):
        """use_points 누락 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000}  # use_points 누락

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "use_points" in response.data

    def test_invalid_order_amount_type_returns_400(self, api_client):
        """order_amount가 숫자가 아닌 경우 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": "not_a_number", "use_points": 500}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_exceeds_order_amount_returns_cannot_use(self, api_client):
        """주문 금액 초과 사용 시 can_use=False 반환"""
        # Arrange
        user = UserFactory.with_points(50000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 15000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_use"] is False
        assert "초과" in response.data["message"]

    def test_zero_points_returns_can_use_true(self, api_client):
        """0 포인트 사용 시 can_use=True 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_check")
        data = {"order_amount": 10000, "use_points": 0}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_use"] is True
        assert "사용하지 않습니다" in response.data["message"]


# ==========================================
# ExpiringPointsView 테스트
# ==========================================


@pytest.mark.django_db
class TestExpiringPointsView:
    """만료 예정 포인트 조회 테스트"""

    def test_returns_expiring_points_with_monthly_summary(self, api_client):
        """만료 예정 포인트와 월별 요약 반환"""
        # Arrange
        user = UserFactory.with_points(3000)
        now = timezone.now()

        # 15일 후 만료 포인트
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=15),
        )
        # 45일 후 만료 포인트 (기본 30일 범위 밖)
        PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=3000,
            expires_at=now + timedelta(days=45),
        )

        api_client.force_authenticate(user=user)
        url = reverse("expiring_points")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "total_expiring" in response.data
        assert "monthly_summary" in response.data
        assert "histories" in response.data
        assert response.data["days"] == 30
        assert response.data["total_expiring"] == 1000

    def test_custom_days_parameter(self, api_client):
        """days 파라미터로 조회 기간 변경"""
        # Arrange
        user = UserFactory.with_points(3000)
        now = timezone.now()

        # 10일 후 만료
        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=500,
            expires_at=now + timedelta(days=10),
        )
        # 50일 후 만료
        PointHistoryFactory.earn(
            user=user,
            points=1500,
            balance=2000,
            expires_at=now + timedelta(days=50),
        )

        api_client.force_authenticate(user=user)
        url = reverse("expiring_points")

        # Act - 7일 이내
        response_7 = api_client.get(url, {"days": 7})

        # Assert
        assert response_7.status_code == status.HTTP_200_OK
        assert response_7.data["days"] == 7
        assert response_7.data["total_expiring"] == 0

        # Act - 60일 이내
        response_60 = api_client.get(url, {"days": 60})

        # Assert
        assert response_60.status_code == status.HTTP_200_OK
        assert response_60.data["days"] == 60
        assert response_60.data["total_expiring"] == 2000

    def test_unauthenticated_returns_403(self, api_client):
        """미인증 사용자 403 반환"""
        # Arrange
        url = reverse("expiring_points")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ==========================================
# point_statistics 테스트
# ==========================================


@pytest.mark.django_db
class TestPointStatistics:
    """포인트 통계 정보 테스트"""

    def test_returns_complete_statistics_structure(self, api_client):
        """전체 통계 구조 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        PointHistoryFactory.earn(user=user, points=3000, balance=3000)
        PointHistoryFactory.use(user=user, points=-1000, balance=2000)
        api_client.force_authenticate(user=user)

        url = reverse("point_statistics")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "current_points" in response.data
        assert "this_month" in response.data
        assert "all_time" in response.data
        assert "by_type" in response.data
        assert response.data["current_points"] == 5000

    def test_this_month_and_all_time_accurate(self, api_client):
        """이번달/전체 통계 정확성 검증"""
        # Arrange
        user = UserFactory.with_points(4000)
        now = timezone.now()
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # 지난달 이력
        last_month_history = PointHistoryFactory.earn(user=user, points=2000, balance=2000)
        last_month_history.created_at = this_month_start - timedelta(days=15)
        last_month_history.save(update_fields=["created_at"])

        # 이번달 이력
        this_month_earn = PointHistoryFactory.earn(user=user, points=3000, balance=5000)
        this_month_earn.created_at = this_month_start + timedelta(days=5)
        this_month_earn.save(update_fields=["created_at"])

        this_month_use = PointHistoryFactory.use(user=user, points=-1000, balance=4000)
        this_month_use.created_at = this_month_start + timedelta(days=6)
        this_month_use.save(update_fields=["created_at"])

        api_client.force_authenticate(user=user)
        url = reverse("point_statistics")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        # 이번달: 적립 3000, 사용 1000
        assert response.data["this_month"]["earned"] == 3000
        assert response.data["this_month"]["used"] == 1000
        # 전체: 적립 5000, 사용 1000
        assert response.data["all_time"]["earned"] == 5000
        assert response.data["all_time"]["used"] == 1000

    def test_unauthenticated_returns_403(self, api_client):
        """미인증 사용자 403 반환"""
        # Arrange
        url = reverse("point_statistics")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

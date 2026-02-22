"""
포인트 조회 관련 비즈니스 로직
읽기 전용 쿼리: 필터링, 통계, 집계
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone

from shopping.models.point import PointHistory

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


class DateParseError(ValueError):
    """날짜 파싱 실패 예외"""

    pass


@dataclass
class PointHistoryFilter:
    """포인트 이력 필터 조건"""

    type_filter: Optional[str] = None  # earned, used, 또는 특정 type
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    page: int = 1
    page_size: int = 20

    @classmethod
    def parse_date(cls, date_str: Optional[str], field_name: str) -> Optional[date]:
        """
        날짜 문자열을 파싱하여 date 객체로 변환

        Args:
            date_str: 날짜 문자열 (YYYY-MM-DD 형식)
            field_name: 필드명 (에러 메시지용)

        Returns:
            date 객체 또는 None

        Raises:
            DateParseError: 잘못된 날짜 형식
        """
        if not date_str:
            return None

        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise DateParseError(f"'{field_name}'의 날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")


@dataclass
class PaginatedResult:
    """페이지네이션된 결과"""

    items: QuerySet
    total_count: int
    page: int
    page_size: int


@dataclass
class HistorySummary:
    """포인트 이력 요약"""

    current_points: int
    total_earned: int
    total_used: int


@dataclass
class MonthlyExpiringSummary:
    """월별 만료 예정 요약"""

    month: str
    points: int
    count: int


@dataclass
class PointStatistics:
    """포인트 통계"""

    current_points: int
    this_month: dict[str, int]
    all_time: dict[str, int]
    by_type: list[dict[str, Any]]


class PointQueryService:
    """
    포인트 조회 서비스 클래스

    읽기 전용 쿼리를 담당합니다:
    - 이력 필터링 및 페이지네이션
    - 요약 통계 계산
    - 만료 예정 포인트 조회
    - 종합 통계
    """

    @staticmethod
    def get_filtered_history(
        user: AbstractBaseUser,
        filter_params: PointHistoryFilter,
    ) -> PaginatedResult:
        """
        필터링된 포인트 이력 조회

        Args:
            user: 사용자
            filter_params: 필터 조건

        Returns:
            PaginatedResult: 페이지네이션된 결과
        """
        # 기본 쿼리셋 - N+1 방지를 위해 select_related 사용
        queryset = PointHistory.objects.filter(user=user).select_related("order")

        # 타입 필터
        if filter_params.type_filter:
            if filter_params.type_filter == "earned":
                queryset = queryset.filter(points__gt=0)
            elif filter_params.type_filter == "used":
                queryset = queryset.filter(points__lt=0)
            else:
                queryset = queryset.filter(type=filter_params.type_filter)

        # 날짜 필터 (date를 timezone-aware datetime으로 변환)
        if filter_params.start_date:
            start_datetime = timezone.make_aware(datetime.combine(filter_params.start_date, datetime.min.time()))
            queryset = queryset.filter(created_at__gte=start_datetime)
        if filter_params.end_date:
            end_datetime = timezone.make_aware(datetime.combine(filter_params.end_date, datetime.max.time()))
            queryset = queryset.filter(created_at__lte=end_datetime)

        # 정렬
        queryset = queryset.order_by("-created_at")

        # 페이지네이션
        total_count = queryset.count()
        start = (filter_params.page - 1) * filter_params.page_size
        end = start + filter_params.page_size

        return PaginatedResult(
            items=queryset[start:end],
            total_count=total_count,
            page=filter_params.page,
            page_size=filter_params.page_size,
        )

    @staticmethod
    def get_history_summary(
        user: AbstractBaseUser,
        queryset: Optional[QuerySet] = None,
    ) -> HistorySummary:
        """
        포인트 이력 요약 정보 계산

        Args:
            user: 사용자
            queryset: 필터링된 쿼리셋 (없으면 전체 이력 사용)

        Returns:
            HistorySummary: 요약 정보
        """
        if queryset is None:
            queryset = PointHistory.objects.filter(user=user)

        # 단일 쿼리로 적립/사용 합계 계산 (DB 쿼리 최적화)
        aggregates = queryset.aggregate(
            total_earned=Sum("points", filter=Q(points__gt=0)),
            total_used=Sum("points", filter=Q(points__lt=0)),
        )

        return HistorySummary(
            current_points=user.points,
            total_earned=aggregates["total_earned"] or 0,
            total_used=abs(aggregates["total_used"] or 0),
        )

    @staticmethod
    def get_monthly_expiring_summary(
        user: AbstractBaseUser,
        days: int = 30,
    ) -> tuple[list[MonthlyExpiringSummary], QuerySet, int]:
        """
        월별 만료 예정 포인트 요약

        Args:
            user: 사용자
            days: 조회 기간 (일)

        Returns:
            tuple: (월별 요약 리스트, 만료 예정 이력 쿼리셋, 총 만료 예정 포인트)
        """
        from datetime import timedelta

        expire_date = timezone.now() + timedelta(days=days)

        # 만료 예정 포인트 이력 - N+1 방지
        expiring_histories = (
            PointHistory.objects.filter(
                user=user,
                type="earn",
                points__gt=0,
                expires_at__lte=expire_date,
                expires_at__gt=timezone.now(),
            )
            .select_related("order")
            .order_by("expires_at")
        )

        # 월별 그룹화 - Python에서 처리 (복잡한 DB 함수 대신)
        monthly_data: dict[str, MonthlyExpiringSummary] = {}

        for history in expiring_histories:
            month_key = history.expires_at.strftime("%Y-%m")
            if month_key not in monthly_data:
                monthly_data[month_key] = MonthlyExpiringSummary(
                    month=month_key,
                    points=0,
                    count=0,
                )
            monthly_data[month_key].points += history.points
            monthly_data[month_key].count += 1

        # 총 만료 예정 포인트 - Manager 메서드 활용
        total_expiring = PointHistory.objects.get_expiring_soon(user, days=days)

        return (
            list(monthly_data.values()),
            expiring_histories,
            total_expiring,
        )

    @staticmethod
    def get_point_statistics(user: AbstractBaseUser) -> PointStatistics:
        """
        포인트 종합 통계 조회

        Args:
            user: 사용자

        Returns:
            PointStatistics: 종합 통계
        """
        # 이번달 시작일
        this_month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # 이번달 적립/사용 - Manager 메서드 활용
        this_month_stats = PointHistory.objects.get_month_statistics(user, this_month_start)

        # 전체 통계 - Manager 메서드 활용
        total_earned = PointHistory.objects.get_total_earned(user)
        total_used = PointHistory.objects.get_total_used(user)

        # 타입별 통계 - 단일 쿼리로 집계
        type_stats = list(
            PointHistory.objects.filter(user=user).values("type").annotate(count=Count("id"), total=Sum("points"))
        )

        return PointStatistics(
            current_points=user.points,
            this_month=this_month_stats,
            all_time={"earned": total_earned, "used": total_used},
            by_type=type_stats,
        )

    @staticmethod
    def get_recent_histories(
        user: AbstractBaseUser,
        limit: int = 5,
    ) -> QuerySet:
        """
        최근 포인트 이력 조회

        Args:
            user: 사용자
            limit: 조회 개수

        Returns:
            QuerySet: 최근 이력
        """
        return PointHistory.objects.filter(user=user).select_related("order").order_by("-created_at")[:limit]

    @staticmethod
    def build_filter_from_request(request) -> PointHistoryFilter:
        """
        Request 객체에서 필터 조건 추출
        (Presentation Layer 역할 - Request 파싱)

        Args:
            request: DRF Request 객체

        Returns:
            PointHistoryFilter: 필터 조건

        Raises:
            DateParseError: 날짜 형식이 잘못된 경우
        """
        return PointHistoryFilter(
            type_filter=request.GET.get("type"),
            start_date=PointHistoryFilter.parse_date(request.GET.get("start_date"), "start_date"),
            end_date=PointHistoryFilter.parse_date(request.GET.get("end_date"), "end_date"),
            page=int(request.GET.get("page", 1)),
            page_size=int(request.GET.get("page_size", 20)),
        )

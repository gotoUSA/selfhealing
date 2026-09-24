from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import permissions, serializers as drf_serializers, status

from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers.point_serializers import (
    PointCheckSerializer,
    PointHistorySerializer,
    UserPointSerializer,
)
from ..services.point_query_service import DateParseError, PointQueryService

logger = logging.getLogger(__name__)


# ===== Swagger 문서화용 응답 Serializers =====


class MyPointResponseSerializer(drf_serializers.Serializer):
    """내 포인트 정보 응답"""

    point_info = UserPointSerializer()
    recent_histories = PointHistorySerializer(many=True)


class PointHistorySummarySerializer(drf_serializers.Serializer):
    """포인트 이력 요약"""

    current_points = drf_serializers.IntegerField()
    total_earned = drf_serializers.IntegerField()
    total_used = drf_serializers.IntegerField()


class PointHistoryListResponseSerializer(drf_serializers.Serializer):
    """포인트 이력 목록 응답"""

    count = drf_serializers.IntegerField()
    page = drf_serializers.IntegerField()
    page_size = drf_serializers.IntegerField()
    summary = PointHistorySummarySerializer()
    results = PointHistorySerializer(many=True)


class PointCheckResponseSerializer(drf_serializers.Serializer):
    """포인트 사용 가능 여부 응답"""

    available_points = drf_serializers.IntegerField()
    can_use = drf_serializers.BooleanField()
    max_usable = drf_serializers.IntegerField()
    message = drf_serializers.CharField()


class MonthlyExpiringSummarySerializer(drf_serializers.Serializer):
    """월별 만료 예정 요약"""

    month = drf_serializers.CharField()
    points = drf_serializers.IntegerField()
    count = drf_serializers.IntegerField()


class ExpiringPointsResponseSerializer(drf_serializers.Serializer):
    """만료 예정 포인트 응답"""

    total_expiring = drf_serializers.IntegerField()
    days = drf_serializers.IntegerField()
    monthly_summary = MonthlyExpiringSummarySerializer(many=True)
    histories = PointHistorySerializer(many=True)


class PointStatisticsThisMonthSerializer(drf_serializers.Serializer):
    """이번 달 포인트 통계"""

    earned = drf_serializers.IntegerField()
    used = drf_serializers.IntegerField()


class PointStatisticsAllTimeSerializer(drf_serializers.Serializer):
    """전체 포인트 통계"""

    total_earned = drf_serializers.IntegerField()
    total_used = drf_serializers.IntegerField()


class PointStatisticsResponseSerializer(drf_serializers.Serializer):
    """포인트 통계 응답"""

    current_points = drf_serializers.IntegerField()
    this_month = PointStatisticsThisMonthSerializer()
    all_time = PointStatisticsAllTimeSerializer()
    by_type = drf_serializers.DictField()


class PointErrorResponseSerializer(drf_serializers.Serializer):
    """포인트 에러 응답"""

    success = drf_serializers.BooleanField(default=False)
    error_code = drf_serializers.CharField()
    message = drf_serializers.CharField()
    errors = drf_serializers.DictField(required=False)


@extend_schema(tags=["Points"])
class MyPointView(APIView):
    """내 포인트 정보 조회 API"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        responses={200: MyPointResponseSerializer},
        summary="내 포인트 현황을 조회한다.",
        description="""처리 내용:
- 현재 포인트 정보를 반환한다.
- 최근 포인트 이력 5건을 포함한다.""",
    )
    def get(self, request: Request) -> Response:
        user = request.user
        serializer = UserPointSerializer(user)

        # 최근 포인트 이력 5개 - Service 레이어 활용
        recent_histories = PointQueryService.get_recent_histories(user, limit=5)
        recent_serializer = PointHistorySerializer(recent_histories, many=True)

        return Response({"point_info": serializer.data, "recent_histories": recent_serializer.data})


@extend_schema(tags=["Points"])
class PointHistoryListView(APIView):
    """포인트 이력 목록 조회"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="type", description="포인트 유형 필터 (earn, use, expire 등)", required=False, type=str),
            OpenApiParameter(name="start_date", description="시작일 (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="end_date", description="종료일 (YYYY-MM-DD)", required=False, type=str),
            OpenApiParameter(name="page", description="페이지 번호", required=False, type=int),
            OpenApiParameter(name="page_size", description="페이지 크기", required=False, type=int),
        ],
        responses={
            200: PointHistoryListResponseSerializer,
            400: PointErrorResponseSerializer,
        },
        summary="포인트 이력 목록을 조회한다.",
        description="""처리 내용:
- 필터링된 포인트 이력을 페이지네이션하여 반환한다.
- 유형, 기간 필터를 적용한다.
- 요약 정보를 함께 반환한다.""",
    )
    def get(self, request: Request) -> Response:
        user = request.user

        # Request에서 필터 조건 추출 (날짜 검증 포함)
        try:
            filter_params = PointQueryService.build_filter_from_request(request)
        except DateParseError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 필터링된 이력 조회
        paginated_result = PointQueryService.get_filtered_history(user, filter_params)

        # 요약 정보 계산 (필터링된 쿼리셋 기반)
        # 참고: 전체 이력 기준 요약이 필요하면 queryset=None 전달
        summary = PointQueryService.get_history_summary(user)

        serializer = PointHistorySerializer(paginated_result.items, many=True)

        return Response(
            {
                "count": paginated_result.total_count,
                "page": paginated_result.page,
                "page_size": paginated_result.page_size,
                "summary": {
                    "current_points": summary.current_points,
                    "total_earned": summary.total_earned,
                    "total_used": summary.total_used,
                },
                "results": serializer.data,
            }
        )


@extend_schema(tags=["Points"])
class PointCheckView(APIView):
    """포인트 사용 가능 여부 확인"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=PointCheckSerializer,
        responses={
            200: PointCheckResponseSerializer,
            400: PointErrorResponseSerializer,
        },
        summary="포인트 사용 가능 여부를 확인한다.",
        description="""처리 내용:
- 주문 금액에 따른 포인트 사용 가능 여부를 확인한다.
- 보유 포인트와 최대 사용 가능 포인트를 반환한다.""",
    )
    def post(self, request: Request) -> Response:
        serializer = PointCheckSerializer(data=request.data, context={"request": request})

        if serializer.is_valid():
            return Response(serializer.validated_data["result"])

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Points"])
class ExpiringPointsView(APIView):
    """만료 예정 포인트 조회"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(name="days", description="조회 기간 (기본: 30일)", required=False, type=int),
        ],
        responses={200: ExpiringPointsResponseSerializer},
        summary="만료 예정 포인트를 조회한다.",
        description="""처리 내용:
- 지정된 기간 내 만료 예정인 포인트를 조회한다.
- 월별 만료 예정 요약을 반환한다.
- 만료 예정 포인트 이력을 포함한다.""",
    )
    def get(self, request: Request) -> Response:
        user = request.user
        days = int(request.GET.get("days", 30))  # 기본 30일

        # Service 레이어에서 월별 만료 예정 요약 조회
        monthly_summary, expiring_histories, total_expiring = PointQueryService.get_monthly_expiring_summary(user, days=days)

        serializer = PointHistorySerializer(expiring_histories, many=True)

        return Response(
            {
                "total_expiring": total_expiring,
                "days": days,
                "monthly_summary": [
                    {"month": item.month, "points": item.points, "count": item.count} for item in monthly_summary
                ],
                "histories": serializer.data,
            }
        )


@extend_schema(
    responses={200: PointStatisticsResponseSerializer},
    summary="포인트 통계 정보를 조회한다.",
    description="""처리 내용:
- 포인트 종합 통계를 반환한다.
- 현재 포인트, 이번 달/전체 적립/사용 통계를 포함한다.
- 유형별 통계를 포함한다.""",
    tags=["Points"],
)
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def point_statistics(request: Request) -> Response:
    user = request.user

    # Service 레이어에서 종합 통계 조회
    stats = PointQueryService.get_point_statistics(user)

    return Response(
        {
            "current_points": stats.current_points,
            "this_month": stats.this_month,
            "all_time": stats.all_time,
            "by_type": stats.by_type,
        }
    )

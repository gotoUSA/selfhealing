from __future__ import annotations


from django.utils import timezone

from rest_framework import serializers

from ..models.notification import Notification


class NotificationListSerializer(serializers.ModelSerializer):
    """
    알림 목록 조회용 경량 Serializer

    리스트 뷰에서 사용되며, 불필요한 message 필드를 제외하여
    네트워크 트래픽과 응답 시간을 최적화합니다.
    """

    # 알림 타입을 한글로 표시
    notification_type_display = serializers.CharField(source="get_notification_type_display", read_only=True)

    # 생성된 시간을 상대적으로 표시
    created_at_display = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "notification_type_display",
            "title",
            "link",
            "is_read",
            "created_at",
            "created_at_display",
        ]
        read_only_fields = ["created_at"]

    def get_created_at_display(self, obj: Notification) -> str:
        """
        생성 시간을 상대적으로 표시

        예: "방금 전", "5분 전", "1시간 전", "3일 전"
        """
        now = timezone.now()
        diff = now - obj.created_at

        seconds = diff.total_seconds()

        if seconds < 60:
            return "방금 전"
        elif seconds < 3600:  # 1시간
            minutes = int(seconds / 60)
            return f"{minutes}분 전"
        elif seconds < 86400:  # 1일
            hours = int(seconds / 3600)
            return f"{hours}시간 전"
        elif seconds < 604800:  # 7일
            days = int(seconds / 86400)
            return f"{days}일 전"
        else:
            # 7일 이상이면 날짜로 표시
            return obj.created_at.strftime("%Y-%m-%d")


class NotificationSerializer(serializers.ModelSerializer):
    """
    알림 상세 조회용 Serializer

    알림 상세 정보 표시에 사용되며, message 필드를 포함합니다.
    """

    # 알림 타입을 한글로 표시
    notification_type_display = serializers.CharField(source="get_notification_type_display", read_only=True)

    # 생성된 시간을 상대적으로 표시 (예: "5분 전", "2시간 전")
    created_at_display = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "notification_type_display",
            "title",
            "message",
            "link",
            "is_read",
            "read_at",
            "created_at",
            "created_at_display",
        ]
        read_only_fields = ["created_at", "read_at"]

    def validate_notification_type(self, value: str) -> str:
        """
        알림 타입 명시적 검증

        유효한 타입 목록:
        - qa_answer: 상품 문의 답변
        - order_status: 주문 상태 변경
        - point_earned: 포인트 적립
        - point_expiring: 포인트 만료 예정
        - review_reply: 리뷰 답글
        - return: 교환/환불
        """
        valid_types = [choice[0] for choice in Notification.TYPE_CHOICES]
        if value not in valid_types:
            raise serializers.ValidationError(
                f"유효하지 않은 알림 타입입니다. 사용 가능한 타입: {', '.join(valid_types)}"
            )
        return value

    def get_created_at_display(self, obj: Notification) -> str:
        """
        생성 시간을 상대적으로 표시

        예: "방금 전", "5분 전", "1시간 전", "3일 전"
        """
        now = timezone.now()
        diff = now - obj.created_at

        seconds = diff.total_seconds()

        if seconds < 60:
            return "방금 전"
        elif seconds < 3600:  # 1시간
            minutes = int(seconds / 60)
            return f"{minutes}분 전"
        elif seconds < 86400:  # 1일
            hours = int(seconds / 3600)
            return f"{hours}시간 전"
        elif seconds < 604800:  # 7일
            days = int(seconds / 86400)
            return f"{days}일 전"
        else:
            # 7일 이상이면 날짜로 표시
            return obj.created_at.strftime("%Y-%m-%d")


class NotificationMarkReadSerializer(serializers.Serializer):
    """
    알림 읽음 처리 Serializer

    여러 알림을 한번에 읽음 처리할 수 있습니다.
    """

    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="읽음 처리할 알림 ID 목록 (빈 리스트면 전체 읽음)",
    )

    def validate_notification_ids(self, value: list[int]) -> list[int]:
        """알림 ID가 실제로 존재하고, 현재 사용자의 것인지 확인"""
        user = self.context["request"].user
        if not value:
            return value

        requested_ids = set(value)  # 중복 제거
        qs = Notification.objects.filter(id__in=requested_ids, user=user)
        found_count = qs.count()
        if found_count != len(requested_ids):
            found_ids = set(qs.values_list("id", flat=True))
            missing = sorted(requested_ids - found_ids)
            raise serializers.ValidationError(f"다음 알림을 찾을 수 없거나 권한이 없습니다: {missing}")
        return value

    def mark_as_read(self) -> int:
        """알림을 읽음 처리"""
        user = self.context["request"].user
        notification_ids = self.validated_data.get("notification_ids", [])

        if notification_ids:
            # 특정 알림들만 읽음 처리
            notifications = Notification.objects.filter(id__in=notification_ids, user=user, is_read=False)
        else:
            # 전체 읽음 처리
            notifications = Notification.objects.filter(user=user, is_read=False)

        # 읽음 처리
        count = notifications.update(is_read=True, read_at=timezone.now())

        return count

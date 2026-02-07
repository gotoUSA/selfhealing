"""
Cross-Cluster 알림 및 정책 동기화.

다중 클러스터 환경에서 설정 변경 알림을 전파하고,
거버넌스 정책을 동기화합니다.

설계 원칙 (업계 표준):
- 설정 자동 전파 ❌ (Blast Radius 위험)
- 알림 + 수동 승인 ✅ (안전)

Components:
    - CrossClusterNotifier: 설정 변경 알림 전파
    - CrossClusterPropagationRequest: 설정 전파 승인 요청 관리
    - GovernancePolicySync: 거버넌스 정책 자동 동기화

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md (Step 5)

Usage:
    from selfhealing.services.canary.cross_cluster import (
        CrossClusterNotifier,
        CrossClusterPropagationRequest,
        GovernancePolicySync,
    )

    # 설정 변경 알림
    notifier = CrossClusterNotifier(
        current_cluster="cluster-a",
        other_clusters=["cluster-b", "cluster-c"],
    )
    notifier.notify_config_change(config_change)

    # 설정 전파 요청
    requester = CrossClusterPropagationRequest()
    request_id = requester.request_propagation(
        source_cluster="cluster-a",
        target_clusters=["cluster-b"],
        change=config_change,
    )

    # 거버넌스 정책 동기화
    policy_sync = GovernancePolicySync()
    policy_sync.sync_policy(governance_policy)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from selfhealing.settings.canary import get_canary_settings
from selfhealing.settings.namespace import get_key_prefix
from selfhealing.settings.slack_channel import get_slack_channel_settings
from selfhealing.utils.time import utc_now

logger = logging.getLogger(__name__)


def _get_default_slack_channel() -> str:
    """Get default Slack channel from settings."""
    return get_slack_channel_settings().default_channel


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ClusterConfigChange:
    """
    설정 변경 정보.

    Attributes:
        config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
        previous_value: 이전 설정값
        new_value: 새 설정값
        changed_by: 변경자
        changed_at: 변경 시간
        rollout_id: 관련 Canary Rollout ID (있는 경우)
        reason: 변경 사유
    """

    config_type: str
    previous_value: dict[str, Any]
    new_value: dict[str, Any]
    changed_by: str = ""
    changed_at: datetime = field(default_factory=utc_now)
    rollout_id: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "config_type": self.config_type,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "changed_by": self.changed_by,
            "changed_at": self.changed_at.isoformat(),
            "rollout_id": self.rollout_id,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClusterConfigChange:
        """딕셔너리에서 생성."""
        changed_at = data.get("changed_at")
        if isinstance(changed_at, str):
            changed_at = datetime.fromisoformat(changed_at)
        elif changed_at is None:
            changed_at = utc_now()

        return cls(
            config_type=data["config_type"],
            previous_value=data.get("previous_value", {}),
            new_value=data.get("new_value", {}),
            changed_by=data.get("changed_by", ""),
            changed_at=changed_at,
            rollout_id=data.get("rollout_id"),
            reason=data.get("reason", ""),
        )


class PropagationRequestStatus(str, Enum):
    """설정 전파 요청 상태."""

    PENDING_APPROVAL = "pending_approval"  # 승인 대기
    APPROVED = "approved"  # 승인됨
    REJECTED = "rejected"  # 거절됨
    EXPIRED = "expired"  # 만료됨
    APPLIED = "applied"  # 적용 완료


@dataclass
class PropagationRequest:
    """
    설정 전파 요청.

    다른 클러스터에서 승인 시 설정이 적용됩니다.
    """

    request_id: str
    source_cluster: str
    target_cluster: str
    config_change: ClusterConfigChange
    status: PropagationRequestStatus = PropagationRequestStatus.PENDING_APPROVAL
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "request_id": self.request_id,
            "source_cluster": self.source_cluster,
            "target_cluster": self.target_cluster,
            "config_change": self.config_change.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_by": self.rejected_by,
            "rejected_at": self.rejected_at.isoformat() if self.rejected_at else None,
            "reject_reason": self.reject_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropagationRequest:
        """딕셔너리에서 생성."""
        return cls(
            request_id=data["request_id"],
            source_cluster=data["source_cluster"],
            target_cluster=data["target_cluster"],
            config_change=ClusterConfigChange.from_dict(data["config_change"]),
            status=PropagationRequestStatus(data.get("status", "pending_approval")),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=(datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None),
            approved_by=data.get("approved_by"),
            approved_at=(datetime.fromisoformat(data["approved_at"]) if data.get("approved_at") else None),
            rejected_by=data.get("rejected_by"),
            rejected_at=(datetime.fromisoformat(data["rejected_at"]) if data.get("rejected_at") else None),
            reject_reason=data.get("reject_reason"),
        )


@dataclass
class GovernancePolicy:
    """
    거버넌스 정책.

    설정값의 허용 범위를 정의합니다.
    이 정책은 자동 동기화됩니다 (실제 설정값 아님).

    Attributes:
        policy_id: 정책 ID
        config_type: 설정 유형
        rules: 정책 규칙 목록
            예: {"max": 5, "min": 1, "field": "retry_max_attempts"}
        version: 정책 버전
        created_by: 생성자
        created_at: 생성 시간
    """

    policy_id: str
    config_type: str
    rules: list[dict[str, Any]] = field(default_factory=list)
    version: int = 1
    created_by: str = ""
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "policy_id": self.policy_id,
            "config_type": self.config_type,
            "rules": self.rules,
            "version": self.version,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernancePolicy:
        """딕셔너리에서 생성."""
        return cls(
            policy_id=data["policy_id"],
            config_type=data["config_type"],
            rules=data.get("rules", []),
            version=data.get("version", 1),
            created_by=data.get("created_by", ""),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    def validate_config(self, config: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        설정값이 정책을 준수하는지 검증.

        Args:
            config: 검증할 설정값

        Returns:
            (준수 여부, 위반 메시지 목록)
        """
        violations = []

        for rule in self.rules:
            field_name = rule.get("field")
            if not field_name or field_name not in config:
                continue

            value = config[field_name]

            # 최대값 검사
            if "max" in rule and value > rule["max"]:
                violations.append(f"{field_name}={value} exceeds max={rule['max']}")

            # 최소값 검사
            if "min" in rule and value < rule["min"]:
                violations.append(f"{field_name}={value} below min={rule['min']}")

            # 허용값 목록 검사
            if "allowed" in rule and value not in rule["allowed"]:
                violations.append(f"{field_name}={value} not in allowed={rule['allowed']}")

        return len(violations) == 0, violations


# =============================================================================
# Notification Backends
# =============================================================================


class NotificationBackend:
    """알림 백엔드 인터페이스."""

    def send(
        self,
        channel: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        알림 전송.

        Args:
            channel: 알림 채널 (예: Slack 채널명)
            message: 알림 메시지
            metadata: 추가 메타데이터

        Returns:
            전송 성공 여부
        """
        raise NotImplementedError


class LoggingNotificationBackend(NotificationBackend):
    """
    로깅 기반 알림 백엔드 (기본값).

    실제 외부 알림 대신 로그에 기록합니다.
    테스트 및 개발 환경에서 사용.
    """

    def send(
        self,
        channel: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """로그로 알림 기록."""
        logger.info(f"[CrossClusterNotification] channel={channel}, " f"message={message[:100]}..., metadata={metadata}")
        return True


class SlackNotificationBackend(NotificationBackend):
    """
    Slack 알림 백엔드.

    환경변수 SLACK_WEBHOOK_URL이 설정된 경우 사용.
    """

    def __init__(self, webhook_url: str | None = None):
        """
        SlackNotificationBackend 초기화.

        Args:
            webhook_url: Slack Webhook URL (없으면 환경변수에서 가져옴)
        """
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")

    def send(
        self,
        channel: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Slack으로 알림 전송."""
        if not self.webhook_url:
            logger.warning("[SlackNotification] SLACK_WEBHOOK_URL not configured")
            return False

        try:
            import requests

            payload = {
                "channel": channel,
                "text": message,
                "username": "SelfHealing Canary",
                "icon_emoji": ":canary:",
            }

            if metadata:
                attachments = [{"fields": [{"title": k, "value": str(v), "short": True} for k, v in metadata.items()]}]
                payload["attachments"] = attachments

            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            logger.debug(f"[SlackNotification] Sent to {channel}")
            return True

        except Exception as e:
            logger.warning(f"[SlackNotification] Failed: {e}")
            return False


# =============================================================================
# CrossClusterNotifier
# =============================================================================


class CrossClusterNotifier:
    """
    크로스 클러스터 알림.

    설정 변경 시 다른 클러스터 담당자에게 알림을 전송합니다.
    자동 적용하지 않고, 정보만 공유합니다.

    Attributes:
        current_cluster: 현재 클러스터 ID
        other_clusters: 알림 대상 클러스터 목록
        notification_backend: 알림 전송 백엔드

    Example:
        notifier = CrossClusterNotifier(
            current_cluster="cluster-a",
            other_clusters=["cluster-b", "cluster-c"],
        )

        notifier.notify_config_change(ClusterConfigChange(
            config_type="circuit_breaker",
            previous_value={"failure_threshold": 5},
            new_value={"failure_threshold": 3},
            changed_by="admin@example.com",
        ))
    """

    @property
    def DEFAULT_CHANNEL(self) -> str:
        """Get default channel from SlackChannelSettings."""
        return _get_default_slack_channel()

    def __init__(
        self,
        current_cluster: str | None = None,
        other_clusters: list[str] | None = None,
        notification_backend: NotificationBackend | None = None,
        default_channel: str | None = None,
    ):
        """
        CrossClusterNotifier 초기화.

        Args:
            current_cluster: 현재 클러스터 ID (환경변수 SELFHEALING_NAMESPACE 기본값)
            other_clusters: 알림 대상 클러스터 목록
            notification_backend: 알림 전송 백엔드 (기본: 로깅)
            default_channel: 기본 알림 채널
        """
        self.current_cluster = current_cluster or os.environ.get("SELFHEALING_NAMESPACE", "default")
        self.other_clusters = other_clusters or []
        self.notification_backend = notification_backend or self._create_default_backend()
        self.default_channel = default_channel or _get_default_slack_channel()

    def _create_default_backend(self) -> NotificationBackend:
        """기본 알림 백엔드 생성."""
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        if webhook_url:
            return SlackNotificationBackend(webhook_url)
        return LoggingNotificationBackend()

    def notify_config_change(
        self,
        change: ClusterConfigChange,
        result: str = "success",
        channel: str | None = None,
    ) -> dict[str, bool]:
        """
        다른 클러스터 담당자에게 설정 변경 알림.

        자동 적용하지 않고, 정보만 공유합니다.

        Args:
            change: 설정 변경 정보
            result: 롤아웃 결과 ("success", "rolled_back" 등)
            channel: 알림 채널 (기본값 사용 시 None)

        Returns:
            클러스터별 알림 성공 여부 딕셔너리
        """
        results = {}
        target_channel = channel or self.default_channel

        for cluster in self.other_clusters:
            message = self._format_change_notification(
                source_cluster=self.current_cluster,
                target_cluster=cluster,
                change=change,
                result=result,
            )

            metadata = {
                "source": self.current_cluster,
                "target": cluster,
                "config_type": change.config_type,
                "rollout_id": change.rollout_id or "N/A",
            }

            success = self.notification_backend.send(
                channel=target_channel,
                message=message,
                metadata=metadata,
            )
            results[cluster] = success

            if success:
                logger.info(f"[CrossClusterNotifier] Notified {cluster} about " f"config change: {change.config_type}")
            else:
                logger.warning(f"[CrossClusterNotifier] Failed to notify {cluster}")

        return results

    def _format_change_notification(
        self,
        source_cluster: str,
        target_cluster: str,
        change: ClusterConfigChange,
        result: str,
    ) -> str:
        """알림 메시지 포맷팅."""
        emoji = "✅" if result == "success" else "⚠️"
        result_text = "성공 (Canary 통과)" if result == "success" else result

        return (
            f"📢 [{source_cluster}] 설정 변경 알림\n"
            f"• 설정: {change.config_type}\n"
            f"• 변경: {change.previous_value} → {change.new_value}\n"
            f"• 결과: {emoji} {result_text}\n"
            f"• 변경자: {change.changed_by}\n"
            f"• 사유: {change.reason or 'N/A'}\n"
            f"• 액션: 필요시 {target_cluster}에서 별도 적용"
        )

    def add_cluster(self, cluster: str) -> None:
        """알림 대상 클러스터 추가."""
        if cluster not in self.other_clusters:
            self.other_clusters.append(cluster)

    def remove_cluster(self, cluster: str) -> None:
        """알림 대상 클러스터 제거."""
        if cluster in self.other_clusters:
            self.other_clusters.remove(cluster)


# =============================================================================
# CrossClusterPropagationRequest
# =============================================================================


class CrossClusterPropagationRequest:
    """
    크로스 클러스터 설정 전파 요청 (수동 승인).

    설정을 다른 클러스터에 전파할 때, 대상 클러스터 담당자의
    승인을 받아야 적용되는 안전한 방식입니다.

    워크플로우:
    1. 요청 생성 (request_propagation)
    2. 대상 클러스터에 승인 요청 알림 전송
    3. 담당자가 approve() 또는 reject() 호출
    4. 승인 시 설정 적용 콜백 실행

    Attributes:
        notification_backend: 알림 전송 백엔드
        default_expiry_hours: 요청 만료 시간 (기본 24시간)
        on_apply: 설정 적용 콜백 함수

    Example:
        requester = CrossClusterPropagationRequest()

        # 요청 생성
        request_id = requester.request_propagation(
            source_cluster="cluster-a",
            target_clusters=["cluster-b"],
            change=config_change,
        )

        # (다른 클러스터에서) 승인
        requester.approve(request_id, approved_by="admin@cluster-b.com")
    """

    # Redis 키 패턴
    REQUEST_KEY = "{prefix}cross_cluster:request:{request_id}"
    PENDING_REQUESTS_KEY = "{prefix}cross_cluster:pending:{cluster}"

    def __init__(
        self,
        redis_client=None,
        notification_backend: NotificationBackend | None = None,
        default_expiry_hours: int = 24,
        on_apply: Callable[[PropagationRequest], bool] | None = None,
        default_channel: str | None = None,
    ):
        """
        CrossClusterPropagationRequest 초기화.

        Args:
            redis_client: Redis 클라이언트 (저장소용)
            notification_backend: 알림 전송 백엔드
            default_expiry_hours: 요청 만료 시간
            on_apply: 설정 적용 콜백 함수
            default_channel: 기본 알림 채널
        """
        self._redis_client = redis_client
        self.notification_backend = notification_backend or LoggingNotificationBackend()
        self.default_expiry_hours = default_expiry_hours
        self.on_apply = on_apply
        self.default_channel = default_channel or _get_default_slack_channel()

        # 메모리 저장소 (Redis 미사용 시 fallback)
        self._memory_store: dict[str, PropagationRequest] = {}

    @property
    def redis_client(self):
        """Redis 클라이언트 (Lazy loading)."""
        if self._redis_client is None:
            try:
                from django.core.cache import caches

                cache = caches.get("default")
                if cache:
                    self._redis_client = cache.client.get_client()
            except Exception:
                pass
        return self._redis_client

    def request_propagation(
        self,
        source_cluster: str,
        target_clusters: list[str],
        change: ClusterConfigChange,
        expiry_hours: int | None = None,
    ) -> str:
        """
        다른 클러스터에 설정 전파 요청 생성.

        Args:
            source_cluster: 요청 원본 클러스터
            target_clusters: 대상 클러스터 목록
            change: 전파할 설정 변경
            expiry_hours: 요청 만료 시간

        Returns:
            요청 ID (대상 클러스터 담당자가 승인해야 적용됨)
        """
        from datetime import timedelta

        request_id = str(uuid.uuid4())[:8]
        expiry = expiry_hours or self.default_expiry_hours
        expires_at = utc_now() + timedelta(hours=expiry)

        for cluster in target_clusters:
            request = PropagationRequest(
                request_id=f"{request_id}-{cluster}",
                source_cluster=source_cluster,
                target_cluster=cluster,
                config_change=change,
                expires_at=expires_at,
            )

            # 저장
            self._save_request(request)
            self._add_to_pending(cluster, request.request_id)

            # 승인 요청 알림
            message = self._format_approval_request(request)
            self.notification_backend.send(
                channel=self.default_channel,
                message=message,
                metadata={
                    "request_id": request.request_id,
                    "source": source_cluster,
                    "target": cluster,
                },
            )

            logger.info(f"[CrossClusterPropagation] Request created: {request.request_id} " f"({source_cluster} → {cluster})")

        return request_id

    def approve(
        self,
        request_id: str,
        approved_by: str,
    ) -> tuple[bool, str | None]:
        """
        설정 전파 요청 승인.

        Args:
            request_id: 요청 ID
            approved_by: 승인자

        Returns:
            (성공 여부, 에러 메시지)
        """
        request = self.get_request(request_id)
        if not request:
            return False, f"Request not found: {request_id}"

        if request.status != PropagationRequestStatus.PENDING_APPROVAL:
            return False, f"Request is not pending: {request.status.value}"

        # 만료 확인
        if request.expires_at and utc_now() > request.expires_at:
            request.status = PropagationRequestStatus.EXPIRED
            self._save_request(request)
            return False, "Request has expired"

        # 승인 처리
        request.status = PropagationRequestStatus.APPROVED
        request.approved_by = approved_by
        request.approved_at = utc_now()
        self._save_request(request)
        self._remove_from_pending(request.target_cluster, request_id)

        # 설정 적용 콜백 실행
        if self.on_apply:
            try:
                applied = self.on_apply(request)
                if applied:
                    request.status = PropagationRequestStatus.APPLIED
                    self._save_request(request)
            except Exception as e:
                logger.exception(f"[CrossClusterPropagation] Apply failed: {e}")

        # 알림 전송
        self.notification_backend.send(
            channel=self.default_channel,
            message=(
                f"✅ 설정 전파 승인됨\n"
                f"• 요청 ID: {request_id}\n"
                f"• 승인자: {approved_by}\n"
                f"• 설정: {request.config_change.config_type}"
            ),
        )

        logger.info(f"[CrossClusterPropagation] Approved: {request_id} by {approved_by}")

        return True, None

    def reject(
        self,
        request_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> tuple[bool, str | None]:
        """
        설정 전파 요청 거절.

        Args:
            request_id: 요청 ID
            rejected_by: 거절자
            reason: 거절 사유

        Returns:
            (성공 여부, 에러 메시지)
        """
        request = self.get_request(request_id)
        if not request:
            return False, f"Request not found: {request_id}"

        if request.status != PropagationRequestStatus.PENDING_APPROVAL:
            return False, f"Request is not pending: {request.status.value}"

        # 거절 처리
        request.status = PropagationRequestStatus.REJECTED
        request.rejected_by = rejected_by
        request.rejected_at = utc_now()
        request.reject_reason = reason
        self._save_request(request)
        self._remove_from_pending(request.target_cluster, request_id)

        # 알림 전송
        self.notification_backend.send(
            channel=self.default_channel,
            message=(
                f"❌ 설정 전파 거절됨\n" f"• 요청 ID: {request_id}\n" f"• 거절자: {rejected_by}\n" f"• 사유: {reason or 'N/A'}"
            ),
        )

        logger.info(f"[CrossClusterPropagation] Rejected: {request_id} by {rejected_by}")

        return True, None

    def get_request(self, request_id: str) -> PropagationRequest | None:
        """요청 조회."""
        # 메모리 저장소에서 조회
        if request_id in self._memory_store:
            return self._memory_store[request_id]

        # Redis에서 조회
        if self.redis_client:
            key = self.REQUEST_KEY.format(
                prefix=get_key_prefix(),
                request_id=request_id,
            )
            try:
                data = self.redis_client.get(key)
                if data:
                    return PropagationRequest.from_dict(json.loads(data))
            except Exception as e:
                logger.warning(f"[CrossClusterPropagation] Redis get failed: {e}")

        return None

    def get_pending_requests(self, cluster: str) -> list[PropagationRequest]:
        """클러스터의 대기 중인 요청 목록 조회."""
        requests = []

        # 메모리 저장소에서 조회
        for req in self._memory_store.values():
            if req.target_cluster == cluster and req.status == PropagationRequestStatus.PENDING_APPROVAL:
                requests.append(req)

        return requests

    def _save_request(self, request: PropagationRequest) -> None:
        """요청 저장."""
        self._memory_store[request.request_id] = request

        if self.redis_client:
            key = self.REQUEST_KEY.format(
                prefix=get_key_prefix(),
                request_id=request.request_id,
            )
            try:
                _settings = get_canary_settings()
                ttl = _settings.propagation_ttl  # Settings에서 TTL 로드 (기본값: 7일)
                self.redis_client.setex(
                    key,
                    ttl,
                    json.dumps(request.to_dict()),
                )
            except Exception as e:
                logger.warning(f"[CrossClusterPropagation] Redis save failed: {e}")

    def _add_to_pending(self, cluster: str, request_id: str) -> None:
        """대기 목록에 추가."""
        if self.redis_client:
            key = self.PENDING_REQUESTS_KEY.format(
                prefix=get_key_prefix(),
                cluster=cluster,
            )
            try:
                self.redis_client.sadd(key, request_id)
            except Exception:
                pass

    def _remove_from_pending(self, cluster: str, request_id: str) -> None:
        """대기 목록에서 제거."""
        if self.redis_client:
            key = self.PENDING_REQUESTS_KEY.format(
                prefix=get_key_prefix(),
                cluster=cluster,
            )
            try:
                self.redis_client.srem(key, request_id)
            except Exception:
                pass

    def _format_approval_request(self, request: PropagationRequest) -> str:
        """승인 요청 메시지 포맷팅."""
        change = request.config_change
        return (
            f"🔔 [{request.target_cluster}] 설정 전파 승인 요청\n"
            f"• 출처: {request.source_cluster}\n"
            f"• 설정: {change.config_type}\n"
            f"• 변경: {change.previous_value} → {change.new_value}\n"
            f"• 요청 ID: {request.request_id}\n"
            f"• 만료: {request.expires_at.isoformat() if request.expires_at else 'N/A'}\n"
            f"• 액션: `/approve {request.request_id}` 또는 `/reject {request.request_id}`"
        )


# =============================================================================
# GovernancePolicySync
# =============================================================================


class GovernancePolicySync:
    """
    거버넌스 정책 동기화 (자동).

    거버넌스 정책만 자동 동기화합니다.
    이는 실제 설정값이 아닌, 설정값의 허용 범위(상한선/하한선)입니다.

    동기화 대상:
    - retry_max_attempts <= 5 (상한선)
    - failure_threshold >= 2 (하한선)

    동기화하지 않는 것:
    - 실제 설정값 (Blast Radius 위험)

    Attributes:
        clusters: 동기화 대상 클러스터 목록
        notification_backend: 알림 전송 백엔드

    Example:
        policy_sync = GovernancePolicySync(
            clusters=["cluster-a", "cluster-b"],
        )

        policy = GovernancePolicy(
            policy_id="circuit-breaker-limits",
            config_type="circuit_breaker",
            rules=[
                {"field": "failure_threshold", "min": 2, "max": 10},
                {"field": "reset_timeout_seconds", "max": 300},
            ],
        )

        policy_sync.sync_policy(policy)
    """

    # Redis 키 패턴
    POLICY_KEY = "{prefix}governance:policy:{config_type}"

    def __init__(
        self,
        clusters: list[str] | None = None,
        redis_client=None,
        notification_backend: NotificationBackend | None = None,
    ):
        """
        GovernancePolicySync 초기화.

        Args:
            clusters: 동기화 대상 클러스터 목록
            redis_client: Redis 클라이언트
            notification_backend: 알림 전송 백엔드
        """
        self.clusters = clusters or []
        self._redis_client = redis_client
        self.notification_backend = notification_backend or LoggingNotificationBackend()

        # 메모리 저장소
        self._policy_store: dict[str, GovernancePolicy] = {}

    @property
    def redis_client(self):
        """Redis 클라이언트 (Lazy loading)."""
        if self._redis_client is None:
            try:
                from django.core.cache import caches

                cache = caches.get("default")
                if cache:
                    self._redis_client = cache.client.get_client()
            except Exception:
                pass
        return self._redis_client

    def sync_policy(self, policy: GovernancePolicy) -> dict[str, bool]:
        """
        거버넌스 정책 동기화.

        모든 대상 클러스터에 정책을 동기화합니다.

        Args:
            policy: 동기화할 거버넌스 정책

        Returns:
            클러스터별 동기화 성공 여부
        """
        results = {}

        # 로컬 저장
        self._save_policy(policy)

        # 알림 전송 (각 클러스터 담당자에게)
        for cluster in self.clusters:
            success = self._notify_policy_update(cluster, policy)
            results[cluster] = success

        logger.info(f"[GovernancePolicySync] Synced policy: {policy.policy_id} " f"to {len(self.clusters)} clusters")

        return results

    def get_policy(self, config_type: str) -> GovernancePolicy | None:
        """정책 조회."""
        # 메모리 저장소에서 조회
        if config_type in self._policy_store:
            return self._policy_store[config_type]

        # Redis에서 조회
        if self.redis_client:
            key = self.POLICY_KEY.format(
                prefix=get_key_prefix(),
                config_type=config_type,
            )
            try:
                data = self.redis_client.get(key)
                if data:
                    return GovernancePolicy.from_dict(json.loads(data))
            except Exception as e:
                logger.warning(f"[GovernancePolicySync] Redis get failed: {e}")

        return None

    def validate_config_against_policy(
        self,
        config_type: str,
        config: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """
        설정값이 거버넌스 정책을 준수하는지 검증.

        Args:
            config_type: 설정 유형
            config: 검증할 설정값

        Returns:
            (준수 여부, 위반 메시지 목록)
        """
        policy = self.get_policy(config_type)
        if not policy:
            return True, []  # 정책 없음 = 통과

        return policy.validate_config(config)

    def _save_policy(self, policy: GovernancePolicy) -> None:
        """정책 저장."""
        self._policy_store[policy.config_type] = policy

        if self.redis_client:
            key = self.POLICY_KEY.format(
                prefix=get_key_prefix(),
                config_type=policy.config_type,
            )
            try:
                self.redis_client.set(key, json.dumps(policy.to_dict()))
            except Exception as e:
                logger.warning(f"[GovernancePolicySync] Redis save failed: {e}")

    def _notify_policy_update(
        self,
        cluster: str,
        policy: GovernancePolicy,
    ) -> bool:
        """정책 업데이트 알림."""
        message = (
            f"📋 [{cluster}] 거버넌스 정책 업데이트\n"
            f"• 정책 ID: {policy.policy_id}\n"
            f"• 설정 유형: {policy.config_type}\n"
            f"• 버전: {policy.version}\n"
            f"• 규칙: {len(policy.rules)}개"
        )

        return self.notification_backend.send(
            channel="#selfhealing-governance",
            message=message,
            metadata={
                "cluster": cluster,
                "policy_id": policy.policy_id,
                "version": policy.version,
            },
        )


# =============================================================================
# Singleton Accessor Functions
# =============================================================================

_cross_cluster_notifier: CrossClusterNotifier | None = None
_propagation_request: CrossClusterPropagationRequest | None = None
_governance_policy_sync: GovernancePolicySync | None = None


def get_cross_cluster_notifier() -> CrossClusterNotifier:
    """CrossClusterNotifier 싱글톤 인스턴스 반환."""
    global _cross_cluster_notifier
    if _cross_cluster_notifier is None:
        _cross_cluster_notifier = CrossClusterNotifier()
    return _cross_cluster_notifier


def get_propagation_request_service() -> CrossClusterPropagationRequest:
    """CrossClusterPropagationRequest 싱글톤 인스턴스 반환."""
    global _propagation_request
    if _propagation_request is None:
        _propagation_request = CrossClusterPropagationRequest()
    return _propagation_request


def get_governance_policy_sync() -> GovernancePolicySync:
    """GovernancePolicySync 싱글톤 인스턴스 반환."""
    global _governance_policy_sync
    if _governance_policy_sync is None:
        _governance_policy_sync = GovernancePolicySync()
    return _governance_policy_sync


def reset_cross_cluster_services() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _cross_cluster_notifier, _propagation_request, _governance_policy_sync
    _cross_cluster_notifier = None
    _propagation_request = None
    _governance_policy_sync = None

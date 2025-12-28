"""
Load Test Reports - Slack Dispatcher (Interface Only).

Slack 웹훅으로 보고서 요약 전송.
⚠️ 이 파일은 인터페이스만 정의합니다. 실제 구현은 추후 진행합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from typing import Any, Optional

from .base import DispatcherInterface


class SlackDispatcher(DispatcherInterface):
    """
    Slack 웹훅으로 보고서 요약 전송.
    
    ⚠️ 인터페이스만 정의됨. 실제 구현 필요.
    """
    
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        channel: Optional[str] = None,
        username: str = "Load Test Bot",
        icon_emoji: str = ":robot_face:",
    ):
        """
        SlackDispatcher 초기화.
        
        Args:
            webhook_url: Slack Incoming Webhook URL
            channel: 전송할 채널 (선택)
            username: 봇 사용자명
            icon_emoji: 봇 아이콘 이모지
        """
        self.webhook_url = webhook_url
        self.channel = channel
        self.username = username
        self.icon_emoji = icon_emoji
    
    def dispatch(self, report: Any, **kwargs) -> bool:
        """
        보고서 요약을 Slack으로 전송.
        
        ⚠️ 미구현. NotImplementedError 발생.
        
        Args:
            report: 전송할 보고서 객체
            **kwargs: 추가 옵션
            
        Raises:
            NotImplementedError: 아직 구현되지 않음
        """
        raise NotImplementedError(
            "SlackDispatcher is not implemented yet. "
            "Please implement dispatch() method with Slack webhook integration."
        )
    
    def is_available(self) -> bool:
        """
        Slack Webhook URL이 설정되어 있으면 사용 가능.
        
        Returns:
            bool: webhook_url이 설정되어 있으면 True
        """
        return self.webhook_url is not None
    
    def _format_summary(self, report: Any) -> dict:
        """
        보고서를 Slack 메시지 형식으로 변환.
        
        Args:
            report: 보고서 객체
            
        Returns:
            dict: Slack 메시지 페이로드
        """
        # TODO: Slack Block Kit 형식으로 변환
        return {
            "text": f"Load Test Report: {report.metrics.stage_id}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"📊 {report.metrics.test_name}",
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Stage:* {report.metrics.stage_id}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Status:* {'✅ PASS' if report.metrics.passed else '❌ FAIL'}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Error Rate:* {report.metrics.error_rate_percent:.2f}%",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*P99:* {report.metrics.p99_response_ms:.0f}ms",
                        },
                    ]
                }
            ]
        }

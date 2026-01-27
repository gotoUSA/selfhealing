"""
Secrets Settings - SecretStr 기반 민감 정보 설정.

Pydantic SecretStr 특징:
- repr(): '**********' 출력
- str(): '**********' 출력  
- get_secret_value(): 실제 값 반환

이점:
- print(settings) 시 자동 마스킹
- JSON 로깅 시 자동 마스킹
- 감사(Audit) 로그 안전
"""


from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SecretsSettings(BaseSettings):
    """
    민감 정보 전용 설정.

    모든 비밀번호, API 키, 토큰은 이 클래스에서 관리합니다.
    SecretStr을 사용하여 로깅 시 자동으로 마스킹됩니다.

    Environment variables:
        SELFHEALING_SECRET_DATABASE_PASSWORD=...
        SELFHEALING_SECRET_REDIS_PASSWORD=...
        SELFHEALING_SECRET_TOSS_SECRET_KEY=...
        SELFHEALING_SECRET_SLACK_WEBHOOK_TOKEN=...
        SELFHEALING_SECRET_ENCRYPTION_KEY=...

    Usage:
        from selfhealing.settings.secrets import get_secrets

        secrets = get_secrets()

        # 안전한 출력 (마스킹됨)
        print(secrets)  # database_password=SecretStr('**********')

        # 실제 값 접근
        actual_password = secrets.database_password.get_secret_value()
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_SECRET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ==========================================================================
    # Database
    # ==========================================================================
    database_password: SecretStr = Field(
        default=SecretStr(""),
        description="Database password (masked in logs)",
    )

    # ==========================================================================
    # Redis
    # ==========================================================================
    redis_password: SecretStr = Field(
        default=SecretStr(""),
        description="Redis password (masked in logs)",
    )

    # ==========================================================================
    # External APIs
    # ==========================================================================
    toss_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description="Toss Payment secret key (masked in logs)",
    )

    slack_webhook_token: SecretStr = Field(
        default=SecretStr(""),
        description="Slack webhook token (masked in logs)",
    )

    slack_bot_token: SecretStr = Field(
        default=SecretStr(""),
        description="Slack Bot OAuth token (masked in logs)",
    )

    pagerduty_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="PagerDuty API key (masked in logs)",
    )

    # ==========================================================================
    # Encryption
    # ==========================================================================
    encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description="Master encryption key for sensitive data (masked in logs)",
    )

    audit_signing_key: SecretStr = Field(
        default=SecretStr(""),
        description="Key for signing audit logs (masked in logs)",
    )

    # ==========================================================================
    # AWS (if used)
    # ==========================================================================
    aws_access_key_id: SecretStr = Field(
        default=SecretStr(""),
        description="AWS Access Key ID (masked in logs)",
    )

    aws_secret_access_key: SecretStr = Field(
        default=SecretStr(""),
        description="AWS Secret Access Key (masked in logs)",
    )

    # ==========================================================================
    # Helper methods
    # ==========================================================================
    def has_database_password(self) -> bool:
        """Database password가 설정되었는지 확인."""
        return bool(self.database_password.get_secret_value())

    def has_redis_password(self) -> bool:
        """Redis password가 설정되었는지 확인."""
        return bool(self.redis_password.get_secret_value())

    def has_toss_secret(self) -> bool:
        """Toss secret key가 설정되었는지 확인."""
        return bool(self.toss_secret_key.get_secret_value())

    def has_slack_webhook(self) -> bool:
        """Slack webhook token이 설정되었는지 확인."""
        return bool(self.slack_webhook_token.get_secret_value())

    def get_masked_summary(self) -> dict:
        """
        모든 시크릿의 마스킹된 요약 반환.

        Returns:
            {field_name: is_set (bool)} 딕셔너리
        """
        return {
            "database_password": self.has_database_password(),
            "redis_password": self.has_redis_password(),
            "toss_secret_key": self.has_toss_secret(),
            "slack_webhook_token": self.has_slack_webhook(),
            "slack_bot_token": bool(self.slack_bot_token.get_secret_value()),
            "pagerduty_api_key": bool(self.pagerduty_api_key.get_secret_value()),
            "encryption_key": bool(self.encryption_key.get_secret_value()),
            "audit_signing_key": bool(self.audit_signing_key.get_secret_value()),
            "aws_access_key_id": bool(self.aws_access_key_id.get_secret_value()),
            "aws_secret_access_key": bool(
                self.aws_secret_access_key.get_secret_value()
            ),
        }


# =============================================================================
# Singleton pattern
# =============================================================================
_secrets: SecretsSettings | None = None


def get_secrets() -> SecretsSettings:
    """
    Get the global SecretsSettings instance.

    Returns:
        SecretsSettings singleton
    """
    global _secrets
    if _secrets is None:
        _secrets = SecretsSettings()
    return _secrets


def reset_secrets() -> None:
    """
    Reset cached secrets (for testing).
    """
    global _secrets
    _secrets = None

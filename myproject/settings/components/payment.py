"""
Payment Configuration (Toss Payments)
토스페이먼츠 관련 모든 설정을 관리합니다.
"""

import os

# ==========================================
# L3 Payment Recovery 설정 (Self-Healing)
# ==========================================
#
# 결제 장애 발생 시 자동 복구를 위한 Retry/Backoff/SLA/Dead-letter 정책
# 현재 Celery + Django ORM 조합으로 구현되어 있으며,
# 추후 Kafka/RabbitMQ 도입 시 쉽게 마이그레이션 가능하도록 추상화되어 있습니다.

PAYMENT_RECOVERY = {
    # Retry 정책 (지수 백오프)
    "RETRY_MAX_ATTEMPTS": int(os.environ.get("PAYMENT_RETRY_MAX_ATTEMPTS", 3)),
    "RETRY_BACKOFF_BASE": int(os.environ.get("PAYMENT_RETRY_BACKOFF_BASE", 4)),  # 4, 16, 64초
    "RETRY_BACKOFF_MAX": int(os.environ.get("PAYMENT_RETRY_BACKOFF_MAX", 180)),  # 최대 3분
    "RETRY_JITTER": os.environ.get("PAYMENT_RETRY_JITTER", "true").lower() == "true",
    # SLA 정책
    "SLA_TIMEOUT_SECONDS": int(os.environ.get("PAYMENT_SLA_TIMEOUT", 300)),  # 5분 내 완료 필요
    "SLA_ABORT_ENABLED": os.environ.get("PAYMENT_SLA_ABORT_ENABLED", "true").lower() == "true",
    # Circuit Breaker (Toggle 기반 - 기본 OFF)
    # 외부 PG 장애 감지 시 운영자가 수동으로 ON
    "CIRCUIT_BREAKER_ENABLED": os.environ.get("PAYMENT_CIRCUIT_BREAKER_ENABLED", "false").lower() == "true",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD": int(os.environ.get("PAYMENT_CB_FAILURE_THRESHOLD", 5)),  # 연속 5회 실패 시 차단
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": int(os.environ.get("PAYMENT_CB_RECOVERY_TIMEOUT", 60)),  # 60초 후 Half-Open
    "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": int(
        os.environ.get("PAYMENT_CB_SUCCESS_THRESHOLD", 2)
    ),  # Half-Open에서 2회 성공 시 Close
    # Dead Letter Queue 정책
    "DLQ_ENABLED": os.environ.get("PAYMENT_DLQ_ENABLED", "true").lower() == "true",
    "DLQ_RETENTION_DAYS": int(os.environ.get("PAYMENT_DLQ_RETENTION_DAYS", 30)),  # 30일 보관
    # 알림 정책
    "NOTIFY_ON_DLQ": os.environ.get("PAYMENT_NOTIFY_ON_DLQ", "true").lower() == "true",
    "NOTIFY_ON_CIRCUIT_OPEN": os.environ.get("PAYMENT_NOTIFY_ON_CIRCUIT_OPEN", "true").lower() == "true",
}

# ==========================================
# 토스페이먼츠 설정
# ==========================================
#
# 토스페이먼츠 대시보드에서 발급받은 키를 환경변수로 설정하세요.
# https://developers.tosspayments.com/my/api-keys
#
# .env 파일 예시:
# TOSS_CLIENT_KEY=test_ck_...  # 클라이언트 키 (프론트엔드용)
# TOSS_SECRET_KEY=test_sk_...  # 시크릿 키 (서버용)

# 토스페이먼츠 API 키
TOSS_CLIENT_KEY = os.environ.get("TOSS_CLIENT_KEY", "")  # 테스트 클라이언트 키
TOSS_SECRET_KEY = os.environ.get("TOSS_SECRET_KEY", "")  # 테스트 시크릿 키

# 토스페이먼츠 API URL
# 테스트: https://api.tosspayments.com
# 운영: https://api.tosspayments.com (동일)
TOSS_BASE_URL = os.environ.get("TOSS_BASE_URL", "https://api.tosspayments.com")

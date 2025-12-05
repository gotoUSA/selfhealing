"""
쇼핑몰 공통 상수 정의

동시성 모니터링, 비즈니스 정책 등 전역적으로 사용되는 상수를 정의합니다.
"""

# ==================== 동시성 모니터링 임계값 ====================
# 락 획득 시간이 이 값을 초과하면 경고 로그 출력
LOCK_CONTENTION_WARNING_THRESHOLD = 1.0  # 초

# 락 획득 시간이 이 값을 초과하면 에러 로그 출력 (Deadlock 의심)
LOCK_CONTENTION_CRITICAL_THRESHOLD = 3.0  # 초


# ==================== Toss 결제 API 에러 코드 분류 ====================
# 재시도해도 의미 없는 오류 (비즈니스 로직 오류, 클라이언트 오류)
TOSS_NON_RETRYABLE_ERRORS = frozenset(
    [
        # 400 Bad Request - 이미 처리됨/중복
        "ALREADY_PROCESSED_PAYMENT",
        "ALREADY_CANCELED_PAYMENT",
        "ALREADY_REFUND_PAYMENT",
        "DUPLICATED_ORDER_ID",
        # 400 Bad Request - 잘못된 요청
        "INVALID_PAYMENT_KEY",
        "INVALID_ORDER_ID",
        "INVALID_AMOUNT",
        "BELOW_MINIMUM_AMOUNT",
        "INVALID_REQUEST",
        "INVALID_API_KEY",
        "UNAPPROVED_ORDER_ID",
        # 400 Bad Request - 카드 문제
        "INVALID_CARD_NUMBER",
        "INVALID_CARD_EXPIRATION",
        "INVALID_STOPPED_CARD",
        "INVALID_CARD_LOST_OR_STOLEN",
        "INVALID_REJECT_CARD",
        # 400 Bad Request - 사용자 취소
        "PAY_PROCESS_CANCELED",
        "PAY_PROCESS_ABORTED",
        # 401 Unauthorized
        "UNAUTHORIZED_KEY",
        # 403 Forbidden - 잔액/한도 문제 (사용자 조치 필요)
        "REJECT_ACCOUNT_PAYMENT",
        "REJECT_CARD_PAYMENT",
        "REJECT_CARD_COMPANY",
        "EXCEED_MAX_AUTH_COUNT",
        "EXCEED_MAX_DAILY_PAYMENT_COUNT",
        "EXCEED_MAX_PAYMENT_AMOUNT",
        "EXCEED_MAX_ONE_DAY_AMOUNT",
        "FORBIDDEN_REQUEST",
        "FDS_ERROR",
        # 404 Not Found
        "NOT_FOUND_PAYMENT",
        "NOT_FOUND_PAYMENT_SESSION",
    ]
)

# 재시도 가능한 오류 (일시적 오류, 서버 오류)
TOSS_RETRYABLE_ERRORS = frozenset(
    [
        # 400 - 일시적 오류
        "PROVIDER_ERROR",
        # 500 Internal Server Error
        "FAILED_INTERNAL_SYSTEM_PROCESSING",
        "FAILED_PAYMENT_INTERNAL_SYSTEM_PROCESSING",
        "COMMON_ERROR",
        "UNKNOWN_PAYMENT_ERROR",
        "FAILED_CARD_COMPANY_RESPONSE",
        # 네트워크 오류 (자체 정의)
        "NETWORK_ERROR",
        "TIMEOUT",
    ]
)

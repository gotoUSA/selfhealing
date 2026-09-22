"""
쇼핑몰 공통 상수 정의

동시성 모니터링, 비즈니스 정책 등 전역적으로 사용되는 상수를 정의합니다.
"""

# ==================== 동시성 모니터링 임계값 ====================
# 락 획득 시간이 이 값을 초과하면 경고 로그 출력
LOCK_CONTENTION_WARNING_THRESHOLD = 1.0  # 초

# 락 획득 시간이 이 값을 초과하면 에러 로그 출력 (Deadlock 의심)
LOCK_CONTENTION_CRITICAL_THRESHOLD = 3.0  # 초


# ==================== 미결제 주문 만료 ====================
# 이 상태의 주문만 만료 대상 (재고를 점유한 채 결제를 기다리는 상태)
ORDER_EXPIRABLE_STATUSES = frozenset(["pending", "confirmed"])

# 결제가 이 상태이면 주문을 만료시키지 않는다
# - in_progress: 승인 API 호출 중 (취소하면 승인된 결제가 고아가 됨)
# - waiting_for_deposit: 가상계좌 입금 대기 (입금 기한은 PG가 관리)
# - done: 승인 완료, 최종 처리(finalize) 대기 중
ORDER_EXPIRY_PROTECTED_PAYMENT_STATUSES = frozenset(["in_progress", "waiting_for_deposit", "done"])

# 만료된 주문에 기록하는 실패 사유
ORDER_EXPIRED_FAILURE_REASON = "결제 시간 초과"


# ==================== Toss 결제 API 에러 코드 분류 ====================
# 승인 요청이 이미 PG 에 닿았을 수 있는 오류 — 롤백 전에 결제 조회 API 로 실제 상태를 확인한다
# (우리 요청은 승인됐는데 응답만 잃은 타임아웃 뒤의 재시도가 이 코드를 받는다)
# - ALREADY_PROCESSED_PAYMENT: "이미 처리된 결제 입니다."
# - DUPLICATED_ORDER_ID: "이미 승인 및 취소가 진행된 중복된 주문번호 입니다."
TOSS_RECONCILE_ERRORS = frozenset(
    [
        "ALREADY_PROCESSED_PAYMENT",
        "DUPLICATED_ORDER_ID",
    ]
)

# 토스 기관 코드 → 이름 (docs.tosspayments.com/codes/org-codes)
# 토스 응답의 card 객체에는 카드사 "이름"이 없다 — issuerCode 두 자리만 온다.
TOSS_CARD_ISSUER_NAMES = {
    "11": "KB국민카드",
    "21": "하나카드",
    "31": "BC카드",
    "41": "신한카드",
    "51": "삼성카드",
    "61": "현대카드",
    "71": "롯데카드",
    "91": "NH농협카드",
}

# 가상계좌 입금 은행 코드 → 이름
TOSS_BANK_NAMES = {
    "04": "KB국민은행",
    "06": "KB국민은행",
    "11": "NH농협은행",
    "20": "우리은행",
    "88": "신한은행",
}


# 토스 Payment 객체의 status 값 (승인 응답 · 조회 응답 · 웹훅 공통)
TOSS_PAYMENT_STATUS_DONE = "DONE"  # 승인 완료
TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT = "WAITING_FOR_DEPOSIT"  # 가상계좌 발급, 입금 대기
TOSS_PAYMENT_STATUS_EXPIRED = "EXPIRED"  # 가상계좌 입금 기한 만료 / 결제 유효시간 만료

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

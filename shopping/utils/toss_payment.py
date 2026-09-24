from __future__ import annotations

import base64
from typing import Any

from django.conf import settings

import requests

from ..constants import TOSS_CONFIRM_TIMEOUT


class TossPaymentClient:
    """
    토스페이먼츠 API 클라이언트

    공식 문서: https://docs.tosspayments.com/reference

    Note:
        외부 호출을 설정값(DEBUG 등)으로 가짜 응답으로 바꾸지 않는다. 개발 환경도 토스 테스트 키로
        실제 API를 부른다 — 가짜 응답은 그 모양이 곧 계약으로 굳어져(취소 응답의 cancels[] 처럼)
        나머지 코드를 잘못된 스키마 위에 세운다.
    """

    def __init__(self) -> None:
        """
        초기화
        settings.py에서 키 정보를 가져옵니다.
        """
        self.secret_key: str = settings.TOSS_SECRET_KEY
        self.client_key: str = settings.TOSS_CLIENT_KEY
        self.base_url: str = settings.TOSS_BASE_URL

        # Basic Auth 헤더 생성
        # 시크릿 키 뒤에 콜론(:)을 붙이고 Base64로 인코딩
        credentials = f"{self.secret_key}:"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        self.headers: dict[str, str] = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
        }

    def confirm_payment(self, payment_key: str, order_id: str, amount: int) -> dict[str, Any]:
        """
        결제 승인 요청

        사용자가 결제창에서 결제를 완료하면 호출합니다.

        Args:
            payment_key: 토스페이먼츠에서 발급한 결제 고유 키
            order_id: 우리 시스템의 주문 ID
            amount: 결제 금액

        Returns:
            토스페이먼츠 응답 데이터

        Raises:
            TossPaymentError: 결제 승인 실패시
        """
        url = f"{self.base_url}/v1/payments/confirm"

        data = {
            "paymentKey": payment_key,
            "orderId": order_id,
            "amount": int(amount),  # Decimal을 int로 변환
        }

        try:
            response = requests.post(
                url,
                json=data,
                headers=self.headers,
                timeout=TOSS_CONFIRM_TIMEOUT,
            )

            # 성공 응답 (200)
            if response.status_code == 200:
                return response.json()

            # 실패 응답
            error_data = response.json()
            raise TossPaymentError(
                code=error_data.get("code", "UNKNOWN"),
                message=error_data.get("message", "결제 승인 실패"),
                status_code=response.status_code,
            )

        except requests.exceptions.RequestException as e:
            raise TossPaymentError(
                code="NETWORK_ERROR",
                message=f"네트워크 오류: {str(e)}",
                status_code=500,
            )

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: int | None = None,
        refund_account: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        결제 취소 요청

        Args:
            payment_key: 토스페이먼츠 결제 키
            cancel_reason: 취소 사유
            cancel_amount: 취소 금액 (None이면 전체 취소)
            refund_account: 환불 계좌 정보 (가상계좌 결제에만 — 토스: 다른 결제수단 취소에는 사용하지 않는다)
            idempotency_key: 멱등키 — 같은 키로 다시 요청하면 토스가 처리 없이 첫 응답을 돌려준다(15일).
                우리 쪽이 환불 뒤 실패해 재시도해도 두 번 환불되지 않게 한다

        Returns:
            토스페이먼츠 응답 데이터

        Raises:
            TossPaymentError: 취소 실패시
        """
        url = f"{self.base_url}/v1/payments/{payment_key}/cancel"

        data = {
            "cancelReason": cancel_reason,
        }

        # 부분 취소 금액 지정 (향후 확장용)
        if cancel_amount is not None:
            data["cancelAmount"] = int(cancel_amount)

        # 가상계좌 환불 계좌 정보 (향후 확장용)
        if refund_account:
            data["refundReceiveAccount"] = refund_account

        try:
            headers = self.headers
            if idempotency_key:
                headers = {**self.headers, "Idempotency-Key": idempotency_key}
            response = requests.post(url, json=data, headers=headers, timeout=30)

            if response.status_code == 200:
                return response.json()

            error_data = response.json()
            raise TossPaymentError(
                code=error_data.get("code", "UNKNOWN"),
                message=error_data.get("message", "결제 취소 실패"),
                status_code=response.status_code,
            )

        except requests.exceptions.RequestException as e:
            raise TossPaymentError(
                code="NETWORK_ERROR",
                message=f"네트워크 오류: {str(e)}",
                status_code=500,
            )

    def get_payment(self, payment_key: str, timeout: float | tuple[float, float] = 30) -> dict[str, Any]:
        """
        결제 정보 조회

        웹훅 진위 확인에도 쓰인다 — 웹훅 본문 대신 이 응답을 신뢰한다.

        Args:
            payment_key: 토스페이먼츠 결제 키
            timeout: 요청 타임아웃(초, 또는 (연결, 읽기)). 웹훅 경로는 토스의 10초 응답 제한보다 짧게,
                승인 태스크 안의 대사는 태스크 시간 제한보다 짧게 준다.

        Returns:
            결제 정보 (Payment 객체)
        """

        url = f"{self.base_url}/v1/payments/{payment_key}"

        try:
            response = requests.get(url, headers=self.headers, timeout=timeout)

            if response.status_code == 200:
                return response.json()

            error_data = response.json()
            raise TossPaymentError(
                code=error_data.get("code", "UNKNOWN"),
                message=error_data.get("message", "결제 조회 실패"),
                status_code=response.status_code,
            )

        except requests.exceptions.RequestException as e:
            raise TossPaymentError(
                code="NETWORK_ERROR",
                message=f"네트워크 오류: {str(e)}",
                status_code=500,
            )

    def create_billing_key(self, customer_key: str, auth_key: str) -> dict[str, Any]:
        """
        빌링키 지급 (정기 결제용)

        향후 구독 서비스 구현시 사용

        Args:
            customer_key: 고객 고유 키
            auth_key: 인증 키

        Returns:
            빌링키 정보
        """
        url = f"{self.base_url}/v1/billing/authorizations/issue"

        data = {
            "customerKey": customer_key,
            "authKey": auth_key,
        }

        try:
            response = requests.post(
                url,
                json=data,
                headers=self.headers,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()

            error_data = response.json()
            raise TossPaymentError(
                code=error_data.get("code", "UNKNOWN"),
                message=error_data.get("message", "빌링키 발급 실패"),
                status_code=response.status_code,
            )

        except requests.exceptions.RequestException as e:
            raise TossPaymentError(
                code="NETWORK_ERROR",
                message=f"네트워크 오류: {str(e)}",
                status_code=500,
            )


class TossPaymentError(Exception):
    """
    토스페이먼츠 API 에러
    """

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code: str = code
        self.message: str = message
        self.status_code: int = status_code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str | int]:
        """에러를 딕셔너리로 변환"""
        return {
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
        }


# 토스페이먼츠 에러 코드 매핑 (주요 에러만)
TOSS_ERROR_MESSAGES: dict[str, str] = {
    "ALREADY_PROCESSED_PAYMENT": "이미 처리된 결제입니다.",
    "PROVIDER_ERROR": "결제 승인에 실패했습니다.",
    "EXCEED_MAX_CARD_INSTALLMENT_PLAN": "설정한 최대 할부 개월 수를 초과했습니다.",
    "INVALID_REQUEST": "잘못된 요청입니다.",
    "INVALID_API_KEY": "API 키가 유효하지 않습니다.",
    "INVALID_AUTHORIZE_AUTH": "인증 정보가 올바르지 않습니다.",
    "INVALID_CARD_EXPIRATION": "카드 유효기간이 올바르지 않습니다.",
    "INVALID_STOPPED_CARD": "정지된 카드입니다.",
    "EXCEED_MAX_DAILY_PAYMENT_COUNT": "일일 결제 한도를 초과했습니다.",
    "NOT_AVAILABLE_BANK": "은행 서비스 시간이 아닙니다.",
    "INVALID_PASSWORD": "결제 비밀번호가 일치하지 않습니다.",
    "INCORRECT_BASIC_AUTH_FORMAT": "Basic 인증 형식이 올바르지 않습니다.",
    "NOT_FOUND_PAYMENT": "결제 정보를 찾을 수 없습니다.",
    "NOT_FOUND_PAYMENT_SESSION": "결제 세션을 찾을 수 없습니다.",
    "FAILED_PAYMENT_INTERNAL_SYSTEM_PROCESSING": "내부 시스템 처리 중 오류가 발생했습니다.",
    "FAILED_INTERNAL_SYSTEM_PROCESSING": "내부 시스템 처리 중 오류가 발생했습니다.",
    "UNKNOWN_PAYMENT_ERROR": "알 수 없는 결제 오류가 발생했습니다.",
}


def get_error_message(code: str) -> str:
    """
    토스페이먼츠 에러 코드를 한글 메시지로 변환
    """
    return TOSS_ERROR_MESSAGES.get(code, "결제 처리 중 오류가 발생했습니다.")

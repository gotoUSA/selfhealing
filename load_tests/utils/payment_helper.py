"""
결제 헬퍼 - 결제 플로우 관련 로직

모든 Stage 시나리오에서 재사용
"""

import time
import random
from typing import Optional, Dict, Any, Tuple

from load_tests.config import ENDPOINTS


class PaymentHelper:
    """결제 플로우 헬퍼"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name

    def create_order(
        self,
        shipping_name: str = "테스트",
        shipping_phone: str = "010-1234-5678",
        shipping_postal_code: str = "12345",
        shipping_address: str = "서울시 강남구 테스트로",
        shipping_address_detail: str = "테스트동 123호",
    ) -> Optional[Dict[str, Any]]:
        """
        주문 생성

        Returns:
            주문 데이터 (order_id, final_amount 포함) 또는 None
        """
        request_name = f"{self.stage_name} POST /api/orders/".strip()

        response = self.client.post(
            ENDPOINTS["orders"],
            json={
                "shipping_name": shipping_name,
                "shipping_phone": shipping_phone,
                "shipping_postal_code": shipping_postal_code,
                "shipping_address": shipping_address,
                "shipping_address_detail": shipping_address_detail,
            },
            name=request_name,
        )

        if response.status_code in [200, 201, 202]:
            return response.json()
        return None

    def generate_payment_key(self, prefix: str = "test") -> str:
        """고유한 payment_key 생성"""
        timestamp = int(time.time() * 1000)
        random_suffix = random.randint(1, 999999)
        return f"{prefix}_{timestamp}_{random_suffix}"

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: int,
        catch_response: bool = False,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        결제 승인

        Args:
            payment_key: PG 결제 키
            order_id: 주문 ID
            amount: 결제 금액
            catch_response: True면 Locust catch_response 사용

        Returns:
            (status_code, response_data) 튜플
        """
        request_name = f"{self.stage_name} POST /api/payments/confirm/".strip()

        if catch_response:
            with self.client.post(
                ENDPOINTS["payment_confirm"],
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": amount,
                },
                name=request_name,
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                    return response.status_code, response.json()
                elif response.status_code == 400:
                    # 비즈니스 에러는 실패로 처리하지 않음
                    response.success()
                    return response.status_code, self._safe_json(response)
                else:
                    response.failure(f"Payment failed: {response.status_code}")
                    return response.status_code, self._safe_json(response)
        else:
            response = self.client.post(
                ENDPOINTS["payment_confirm"],
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": amount,
                },
                name=request_name,
            )
            return response.status_code, self._safe_json(response)

    def cancel_payment(
        self,
        payment_id: int,
        cancel_reason: str = "테스트 취소",
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        결제 취소

        Args:
            payment_id: 결제 ID
            cancel_reason: 취소 사유

        Returns:
            (status_code, response_data) 튜플
        """
        request_name = f"{self.stage_name} POST /api/payments/cancel/".strip()

        response = self.client.post(
            ENDPOINTS["payment_cancel"],
            json={
                "payment_id": payment_id,
                "cancel_reason": cancel_reason,
            },
            name=request_name,
        )

        return response.status_code, self._safe_json(response)

    def get_payment(self, payment_id: int) -> Optional[Dict[str, Any]]:
        """결제 상세 조회"""
        request_name = f"{self.stage_name} GET /api/payments/{{id}}/".strip()

        response = self.client.get(
            ENDPOINTS["payment_detail"].format(id=payment_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """주문 상세 조회"""
        request_name = f"{self.stage_name} GET /api/orders/{{id}}/".strip()

        response = self.client.get(
            ENDPOINTS["order_detail"].format(id=order_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def full_payment_flow(
        self,
        product_ids: list,
        cart_helper,
        shipping_info: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        완전한 결제 플로우 실행

        Args:
            product_ids: 상품 ID 목록
            cart_helper: CartHelper 인스턴스
            shipping_info: 배송 정보 (선택)

        Returns:
            (성공 여부, 결제 결과 데이터) 튜플
        """
        # 1. 장바구니 준비
        if not cart_helper.prepare_cart_for_order(product_ids):
            return False, None

        # 2. 장바구니 확인
        if not cart_helper.has_items():
            return False, None

        # 3. 주문 생성
        shipping = shipping_info or {
            "shipping_name": "테스트",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구 테스트로",
            "shipping_address_detail": "테스트동 123호",
        }

        order_data = self.create_order(**shipping)
        if not order_data:
            return False, None

        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")

        if not order_id or not final_amount:
            return False, None

        # 4. 결제 승인
        payment_key = self.generate_payment_key()
        status_code, payment_data = self.confirm_payment(
            payment_key=payment_key,
            order_id=order_id,
            amount=int(final_amount),
            catch_response=True,
        )

        success = status_code in [200, 201]

        return success, {
            "order_id": order_id,
            "order_data": order_data,
            "payment_key": payment_key,
            "payment_data": payment_data,
            "status_code": status_code,
        }

    def _safe_json(self, response) -> Optional[Dict[str, Any]]:
        """안전한 JSON 파싱"""
        try:
            return response.json()
        except Exception:
            return None

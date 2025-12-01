"""
환불/교환 시나리오 - 고객 및 판매자 행동 패턴

이 파일은 return_exchange_locust.py에서 import하여 사용합니다.
Note: 파일명이 return.py가 아닌 return_exchange.py인 이유는
      'return'이 Python 예약어이기 때문입니다.

독립 실행 방법:
    # 고객 시나리오만 테스트
    locust -f shopping/tests/performance/scenarios/return_exchange.py CustomerReturnUser \\
        --host=http://localhost:8000 --users 100 --spawn-rate 10

    # 판매자 시나리오만 테스트
    locust -f shopping/tests/performance/scenarios/return_exchange.py SellerReturnUser \\
        --host=http://localhost:8000 --users 50 --spawn-rate 5

    # E2E 시나리오 (전체 플로우)
    locust -f shopping/tests/performance/scenarios/return_exchange.py ReturnE2EUser \\
        --host=http://localhost:8000 --users 20 --spawn-rate 2
"""

import random
import time
import logging

from locust import HttpUser, TaskSet, task, between

logger = logging.getLogger(__name__)


# =============================================================================
# 공통 유틸리티
# =============================================================================


class ReturnTestMixin:
    """환불/교환 테스트 공통 기능"""

    def login(self, username: str, password: str = "testpass123") -> bool:
        """로그인 수행"""
        response = self.client.post(
            "/api/auth/login/",
            json={"username": username, "password": password},
            name="/api/auth/login/",
        )

        if response.status_code == 200:
            token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {token}"})
            return True
        else:
            logger.warning(f"로그인 실패: {username}, 상태: {response.status_code}")
            return False

    def get_completed_orders(self) -> list:
        """배송 완료된 주문 목록 조회 (환불/교환 가능한 주문)

        Note: 환불/교환은 배송 완료(delivered) 상태에서만 가능
              (배송 완료 후 7일 이내)
        """
        response = self.client.get(
            "/api/orders/?status=delivered",
            name="/api/orders/ [delivered]",
        )

        if response.status_code == 200:
            data = response.json()
            # 페이지네이션 처리
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data if isinstance(data, list) else []
        return []

    def get_my_returns(self) -> list:
        """내 환불/교환 목록 조회"""
        response = self.client.get(
            "/api/returns/",
            name="/api/returns/ [list]",
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data if isinstance(data, list) else []
        return []

    def get_returns_by_status(self, status: str) -> list:
        """상태별 환불/교환 조회"""
        response = self.client.get(
            f"/api/returns/?status={status}",
            name=f"/api/returns/ [status={status}]",
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data if isinstance(data, list) else []
        return []


# =============================================================================
# 고객 시나리오 (CustomerReturnBehavior)
# =============================================================================


class CustomerReturnBehavior(TaskSet, ReturnTestMixin):
    """
    고객의 환불/교환 행동 패턴

    실제 고객 행동 비율:
    - 환불/교환 목록 조회: 40%
    - 환불 신청: 20%
    - 교환 신청: 10%
    - 송장번호 입력: 15%
    - 상세 조회: 10%
    - 신청 취소: 5%
    """

    # 환불 사유 분포 (실제 통계 기반)
    REFUND_REASONS = [
        ("change_of_mind", 0.4),   # 단순변심 40%
        ("defective", 0.2),        # 상품불량 20%
        ("wrong_product", 0.1),    # 오배송 10%
        ("description_mismatch", 0.15),  # 상세페이지와 다름 15%
        ("size_issue", 0.1),       # 사이즈 문제 10%
        ("other", 0.05),           # 기타 5%
    ]

    def on_start(self):
        """태스크 시작 시 로그인"""
        # 고객 사용자로 로그인 (load_test_user_0 ~ 499)
        user_id = random.randint(0, 499)
        self.username = f"load_test_user_{user_id}"

        if not self.login(self.username):
            logger.error(f"고객 로그인 실패: {self.username}")
            self.interrupt()

        # 초기 데이터 로드
        self.my_orders = self.get_completed_orders()
        self.my_returns = self.get_my_returns()

    def _select_reason(self) -> str:
        """가중치 기반 사유 선택"""
        rand = random.random()
        cumulative = 0
        for reason, weight in self.REFUND_REASONS:
            cumulative += weight
            if rand <= cumulative:
                return reason
        return "change_of_mind"

    @task(40)
    def view_my_returns(self):
        """내 환불/교환 목록 조회"""
        self.my_returns = self.get_my_returns()

    @task(10)
    def view_return_detail(self):
        """환불/교환 상세 조회"""
        if not self.my_returns:
            self.my_returns = self.get_my_returns()

        if self.my_returns:
            return_obj = random.choice(self.my_returns)
            return_id = return_obj.get("id")

            self.client.get(
                f"/api/returns/{return_id}/",
                name="/api/returns/[id]/ [detail]",
            )

    @task(20)
    def create_refund_request(self):
        """환불 신청"""
        # 주문 목록 새로고침 (환불 가능한 주문 찾기)
        self.my_orders = self.get_completed_orders()

        if not self.my_orders:
            logger.debug(f"{self.username}: 환불 가능한 주문 없음")
            return

        order = random.choice(self.my_orders)
        order_id = order.get("id")

        # 주문 상세에서 items 확인
        order_detail = self.client.get(
            f"/api/orders/{order_id}/",
            name="/api/orders/[id]/ [for refund]",
        )

        if order_detail.status_code != 200:
            return

        order_data = order_detail.json()
        items = order_data.get("items", [])

        if not items:
            return

        # 첫 번째 아이템으로 환불 신청
        first_item = items[0]
        reason = self._select_reason()

        with self.client.post(
            "/api/returns/",
            json={
                "order_id": order_id,
                "type": "refund",
                "reason": reason,
                "reason_detail": f"부하테스트 환불 신청 - {reason}",
                "items": [
                    {
                        "order_item_id": first_item.get("id"),
                        "quantity": 1,
                    }
                ],
            },
            name="/api/returns/ [create refund]",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
                # 목록 갱신
                self.my_returns = self.get_my_returns()
            elif response.status_code == 400:
                # 이미 환불 신청된 주문 등 비즈니스 로직 실패
                response.success()  # 예상된 실패
            else:
                response.failure(f"환불 신청 실패: {response.status_code}")

    @task(10)
    def create_exchange_request(self):
        """교환 신청"""
        self.my_orders = self.get_completed_orders()

        if not self.my_orders:
            return

        order = random.choice(self.my_orders)
        order_id = order.get("id")

        # 주문 상세 조회
        order_detail = self.client.get(
            f"/api/orders/{order_id}/",
            name="/api/orders/[id]/ [for exchange]",
        )

        if order_detail.status_code != 200:
            return

        order_data = order_detail.json()
        items = order_data.get("items", [])

        if not items:
            return

        first_item = items[0]
        reason = self._select_reason()

        with self.client.post(
            "/api/returns/",
            json={
                "order_id": order_id,
                "type": "exchange",
                "reason": reason,
                "reason_detail": f"부하테스트 교환 신청 - {reason}",
                "items": [
                    {
                        "order_item_id": first_item.get("id"),
                        "quantity": 1,
                    }
                ],
                # exchange_product_id는 선택사항 (동일 상품 교환)
            },
            name="/api/returns/ [create exchange]",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
                self.my_returns = self.get_my_returns()
            elif response.status_code == 400:
                response.success()  # 예상된 실패
            else:
                response.failure(f"교환 신청 실패: {response.status_code}")

    @task(15)
    def update_tracking_number(self):
        """반품 송장번호 입력 (승인된 건에 대해)"""
        # 승인된 환불/교환 조회
        approved_returns = self.get_returns_by_status("approved")

        if not approved_returns:
            return

        return_obj = random.choice(approved_returns)
        return_id = return_obj.get("id")

        # 택배사 목록
        shipping_companies = ["CJ대한통운", "우체국택배", "한진택배", "롯데택배", "로젠택배"]
        tracking_number = f"{random.randint(100000000000, 999999999999)}"

        with self.client.patch(
            f"/api/returns/{return_id}/",
            json={
                "return_shipping_company": random.choice(shipping_companies),
                "return_tracking_number": tracking_number,
            },
            name="/api/returns/[id]/ [update tracking]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [400, 404]:
                response.success()  # 이미 처리됨 또는 권한 없음
            else:
                response.failure(f"송장번호 입력 실패: {response.status_code}")

    @task(5)
    def cancel_return_request(self):
        """환불/교환 신청 취소 (신청 상태인 건만)"""
        # 신청 상태인 건 조회
        requested_returns = self.get_returns_by_status("requested")

        if not requested_returns:
            return

        return_obj = random.choice(requested_returns)
        return_id = return_obj.get("id")

        with self.client.delete(
            f"/api/returns/{return_id}/",
            name="/api/returns/[id]/ [cancel]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                self.my_returns = self.get_my_returns()
            elif response.status_code in [400, 404]:
                response.success()  # 이미 처리됨
            else:
                response.failure(f"취소 실패: {response.status_code}")


# =============================================================================
# 판매자 시나리오 (SellerReturnBehavior)
# =============================================================================


class SellerReturnBehavior(TaskSet, ReturnTestMixin):
    """
    판매자의 환불/교환 처리 행동 패턴

    실제 판매자 행동 비율:
    - 환불/교환 목록 조회: 30%
    - 승인: 25%
    - 거부: 5%
    - 반품 도착 확인: 20%
    - 완료 처리: 20%
    """

    def on_start(self):
        """태스크 시작 시 판매자로 로그인

        판매자 계정 우선순위:
        1. seller_N 전용 계정 (seller_0 ~ seller_9)
        2. admin 계정 (staff 권한으로 모든 Return 접근 가능)
        3. load_test_user 중 is_seller=True 계정 (500~509 범위)
        """
        # 판매자 사용자로 로그인 시도
        seller_id = random.randint(0, 9)
        self.username = f"seller_{seller_id}"

        if not self.login(self.username):
            # fallback 1: admin 계정 (staff는 모든 Return 접근 가능)
            if not self.login("admin", "admin123"):
                # fallback 2: load_test_user 중 판매자 계정
                # 실제 환경에서 is_seller=True로 설정된 계정 필요
                seller_user_id = 500 + random.randint(0, 9)
                if not self.login(f"load_test_user_{seller_user_id}"):
                    logger.error("판매자 로그인 실패 (모든 fallback 실패)")
                    self.interrupt()

        self.seller_returns = []

    def get_seller_returns(self, status: str = None) -> list:
        """판매자 환불/교환 목록 조회"""
        url = "/api/seller/returns/"
        if status:
            url += f"?status={status}"

        response = self.client.get(
            url,
            name=f"/api/seller/returns/ [status={status or 'all'}]",
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data if isinstance(data, list) else []
        return []

    @task(30)
    def view_seller_returns(self):
        """판매자 환불/교환 목록 조회"""
        self.seller_returns = self.get_seller_returns()

    @task(25)
    def approve_return(self):
        """환불/교환 승인"""
        # 신청 상태인 건 조회
        requested_returns = self.get_seller_returns(status="requested")

        if not requested_returns:
            return

        return_obj = random.choice(requested_returns)
        return_id = return_obj.get("id")

        with self.client.post(
            f"/api/seller/returns/{return_id}/approve/",
            json={"admin_memo": "부하테스트 승인"},
            name="/api/seller/returns/[id]/approve/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [400, 403, 404]:
                response.success()  # 이미 처리됨 또는 권한 없음
            else:
                response.failure(f"승인 실패: {response.status_code}")

    @task(5)
    def reject_return(self):
        """환불/교환 거부"""
        requested_returns = self.get_seller_returns(status="requested")

        if not requested_returns:
            return

        return_obj = random.choice(requested_returns)
        return_id = return_obj.get("id")

        reject_reasons = [
            "반품 기간 초과",
            "상품 훼손",
            "사용 흔적 있음",
            "태그 제거됨",
        ]

        with self.client.post(
            f"/api/seller/returns/{return_id}/reject/",
            json={"rejected_reason": random.choice(reject_reasons)},  # 필드명 수정
            name="/api/seller/returns/[id]/reject/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [400, 403, 404]:
                response.success()
            else:
                response.failure(f"거부 실패: {response.status_code}")

    @task(20)
    def confirm_receive(self):
        """반품 도착 확인"""
        # 배송중(shipping) 상태인 건 조회
        shipping_returns = self.get_seller_returns(status="shipping")

        if not shipping_returns:
            return

        return_obj = random.choice(shipping_returns)
        return_id = return_obj.get("id")

        with self.client.post(
            f"/api/seller/returns/{return_id}/confirm-receive/",
            json={},
            name="/api/seller/returns/[id]/confirm-receive/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [400, 403, 404]:
                response.success()
            else:
                response.failure(f"도착확인 실패: {response.status_code}")

    @task(20)
    def complete_return(self):
        """환불/교환 완료 처리"""
        # 도착완료(received) 상태인 건 조회
        received_returns = self.get_seller_returns(status="received")

        if not received_returns:
            return

        return_obj = random.choice(received_returns)
        return_id = return_obj.get("id")
        return_type = return_obj.get("type", "refund")

        # 교환인 경우 송장번호 필요
        payload = {}
        if return_type == "exchange":
            shipping_companies = ["CJ대한통운", "우체국택배", "한진택배"]
            payload = {
                "exchange_shipping_company": random.choice(shipping_companies),
                "exchange_tracking_number": f"{random.randint(100000000000, 999999999999)}",
            }

        with self.client.post(
            f"/api/seller/returns/{return_id}/complete/",
            json=payload,
            name="/api/seller/returns/[id]/complete/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code in [400, 403, 404]:
                response.success()  # 이미 처리됨 또는 권한/상태 문제
            else:
                response.failure(f"완료처리 실패: {response.status_code}")


# =============================================================================
# E2E 시나리오 (전체 플로우)
# =============================================================================


class ReturnE2EBehavior(TaskSet, ReturnTestMixin):
    """
    환불/교환 E2E 플로우 테스트

    전체 프로세스:
    1. 고객: 주문 생성 → 결제
    2. 고객: 환불/교환 신청
    3. 판매자: 승인
    4. 고객: 반품 송장 입력
    5. 판매자: 도착 확인 → 완료 처리

    주의: 이 시나리오는 실제 주문/결제가 필요하므로
    테스트 데이터가 충분히 준비되어 있어야 합니다.
    """

    def on_start(self):
        """E2E 테스트 초기화"""
        # 고객 계정과 판매자/관리자 계정 모두 준비
        self.customer_id = random.randint(0, 499)
        self.customer_username = f"load_test_user_{self.customer_id}"

        # 판매자/관리자 토큰은 별도 저장
        self.customer_token = None
        self.seller_token = None

        # 고객으로 로그인
        if not self._login_as_customer():
            self.interrupt()

    def _login_as_customer(self) -> bool:
        """고객으로 로그인"""
        response = self.client.post(
            "/api/auth/login/",
            json={"username": self.customer_username, "password": "testpass123"},
            name="/api/auth/login/ [customer]",
        )

        if response.status_code == 200:
            self.customer_token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {self.customer_token}"})
            return True
        return False

    def _login_as_seller(self) -> bool:
        """판매자/관리자로 로그인"""
        # admin 계정 사용 (실제 환경에서는 판매자 계정 사용)
        response = self.client.post(
            "/api/auth/login/",
            json={"username": "admin", "password": "admin123"},
            name="/api/auth/login/ [seller]",
        )

        if response.status_code == 200:
            self.seller_token = response.json().get("access")
            self.client.headers.update({"Authorization": f"Bearer {self.seller_token}"})
            return True
        return False

    def _switch_to_customer(self):
        """고객 토큰으로 전환"""
        if self.customer_token:
            self.client.headers.update({"Authorization": f"Bearer {self.customer_token}"})

    def _switch_to_seller(self):
        """판매자 토큰으로 전환"""
        if self.seller_token:
            self.client.headers.update({"Authorization": f"Bearer {self.seller_token}"})

    @task
    def full_refund_flow(self):
        """
        완전한 환불 플로우

        고객: 주문 확인 → 환불 신청
        판매자: 승인
        고객: 송장 입력 (상태 자동 변경)
        판매자: 도착 확인 → 완료
        """
        # 1. 고객으로 전환
        self._switch_to_customer()

        # 2. 배송 완료된 주문 확인 (환불/교환 가능한 주문)
        orders = self.get_completed_orders()
        if not orders:
            logger.debug("E2E: 환불 가능한 주문 없음 (delivered 상태 주문 필요)")
            return

        # delivered 상태 주문만 필터 (이중 확인)
        valid_orders = [o for o in orders if o.get("status") == "delivered"]
        if not valid_orders:
            logger.debug("E2E: delivered 상태 주문 없음")
            return

        order = random.choice(valid_orders)
        order_id = order.get("id")

        # 3. 주문 상세 조회
        order_response = self.client.get(
            f"/api/orders/{order_id}/",
            name="/api/orders/[id]/ [E2E]",
        )

        if order_response.status_code != 200:
            return

        order_data = order_response.json()
        items = order_data.get("items", [])
        if not items:
            return

        # 4. 환불 신청
        first_item = items[0]
        refund_response = self.client.post(
            "/api/returns/",
            json={
                "order_id": order_id,
                "type": "refund",
                "reason": "change_of_mind",
                "reason_detail": "E2E 테스트 환불 신청",
                "items": [{"order_item_id": first_item.get("id"), "quantity": 1}],
            },
            name="/api/returns/ [E2E create]",
        )

        if refund_response.status_code != 201:
            return

        return_data = refund_response.json()
        return_id = return_data.get("return", {}).get("id")

        if not return_id:
            return

        # 5. 판매자로 전환하여 승인
        if not self._login_as_seller():
            return

        approve_response = self.client.post(
            f"/api/seller/returns/{return_id}/approve/",
            json={"admin_memo": "E2E 테스트 승인"},
            name="/api/seller/returns/[id]/approve/ [E2E]",
        )

        if approve_response.status_code != 200:
            return

        # 6. 고객으로 전환하여 송장번호 입력
        self._switch_to_customer()

        tracking_response = self.client.patch(
            f"/api/returns/{return_id}/",
            json={
                "return_shipping_company": "CJ대한통운",
                "return_tracking_number": f"{random.randint(100000000000, 999999999999)}",
            },
            name="/api/returns/[id]/ [E2E tracking]",
        )

        # 송장 입력 후 상태가 shipping으로 변경되었는지 확인
        if tracking_response.status_code != 200:
            logger.debug(f"E2E: 송장번호 입력 실패 - {tracking_response.status_code}")
            return

        # 송장 입력 성공 → 상태가 'shipping'으로 자동 변경됨
        logger.debug(f"E2E: 송장번호 입력 완료, return_id={return_id}")

        # 잠시 대기 (실제 환경에서는 반품 배송 시간)
        time.sleep(0.5)

        # 7. 판매자로 전환하여 도착 확인
        self._switch_to_seller()

        receive_response = self.client.post(
            f"/api/seller/returns/{return_id}/confirm-receive/",
            json={},
            name="/api/seller/returns/[id]/confirm-receive/ [E2E]",
        )

        if receive_response.status_code != 200:
            return

        # 8. 완료 처리
        complete_response = self.client.post(
            f"/api/seller/returns/{return_id}/complete/",
            json={},
            name="/api/seller/returns/[id]/complete/ [E2E]",
        )

        if complete_response.status_code == 200:
            logger.info(f"E2E 환불 완료: return_id={return_id}")


# =============================================================================
# 독립 실행용 User 클래스
# =============================================================================


class CustomerReturnUser(HttpUser):
    """고객 환불/교환 시나리오 전용 User"""

    tasks = [CustomerReturnBehavior]
    wait_time = between(2, 5)
    host = "http://localhost:8000"


class SellerReturnUser(HttpUser):
    """판매자 환불/교환 처리 시나리오 전용 User"""

    tasks = [SellerReturnBehavior]
    wait_time = between(1, 3)
    host = "http://localhost:8000"


class ReturnE2EUser(HttpUser):
    """E2E 환불 플로우 전용 User"""

    tasks = [ReturnE2EBehavior]
    wait_time = between(3, 8)  # E2E는 더 긴 대기시간
    host = "http://localhost:8000"

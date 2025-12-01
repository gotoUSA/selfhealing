"""
500명 동시 결제 승인 테스트용 Locust 스크립트

사용법:
    locust -f concurrent_payment_locust.py --host=http://localhost:8000 --headless -u 500 -r 50 -t 60s

설명:
    -u 500: 500명의 동시 사용자
    -r 50: 초당 50명씩 동시 시작
    -t 60s: 60초간 실행

전제조건:
    - setup_test_data.py로 테스트 데이터 미리 생성 필요
    - Django 서버가 http://localhost:8000 에서 실행 중이어야 함
"""

import random
import time

from locust import HttpUser, between, events, task


class ConcurrentPaymentUser(HttpUser):
    """500명 동시 결제 승인 시도"""

    wait_time = between(0, 0)  # 대기 없이 즉시 실행

    # 전역 카운터 (모든 사용자가 공유)
    payment_attempts = []
    _user_counter = 0  # 사용자 순차 카운터
    _user_lock = None  # 스레드 안전을 위한 락

    def on_start(self):
        """각 사용자 시작 시 로그인 및 주문/결제 생성"""
        # 스레드 안전한 카운터 증가
        if self.__class__._user_lock is None:
            import threading

            self.__class__._user_lock = threading.Lock()

        with self.__class__._user_lock:
            user_index = self.__class__._user_counter
            self.__class__._user_counter += 1

        # 고유한 사용자 할당 (0~999 순차 사용, 1000명 이상이면 재사용)
        self.user_id = user_index % 1000
        self.username = f"load_test_user_{self.user_id}"
        self.password = "testpass123"

        # 로그인
        login_response = self.client.post(
            "/api/auth/login/",
            json={"username": self.username, "password": self.password},
            name="/api/auth/login/",
        )

        if login_response.status_code != 200:
            self.login_error = f"Login failed: {login_response.status_code}"
            return

        self.access_token = login_response.json().get("access")

        # 장바구니에 상품 추가 (product_id=1 사용, 미리 생성되어 있어야 함)
        cart_response = self.client.post(
            "/api/cart-items/",
            json={"product_id": 1, "quantity": 1},
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/api/cart-items/",
        )

        if cart_response.status_code not in [200, 201]:
            self.cart_error = f"Cart add failed: {cart_response.status_code}"
            return

        # 주문 생성
        order_response = self.client.post(
            "/api/orders/",
            json={
                "shipping_name": f"테스트유저{self.user_id}",
                "shipping_phone": f"010-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}",
                "shipping_postal_code": "12345",
                "shipping_address": "서울시 강남구",
                "shipping_address_detail": "101동",
            },
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/api/orders/",
        )

        if order_response.status_code != 202:
            self.order_error = f"Order create failed: {order_response.status_code} - {order_response.text[:200]}"
            return

        order_data = order_response.json()
        self.order_id = order_data.get("order_id")

        # order_id 검증
        if not self.order_id:
            self.order_error = f"Order ID not found in response: {order_data}"
            return

        # 주문 처리 완료 대기 (상태 확인만)
        max_wait = 20  # 15초 → 20초로 증가
        wait_interval = 0.5
        elapsed = 0
        order_ready = False

        # 초기 대기 (Celery 작업 시작 대기)
        time.sleep(2)

        while elapsed < max_wait:
            # 주문 상태 확인 (GET 요청)
            check_response = self.client.get(
                f"/api/orders/{self.order_id}/",
                headers={"Authorization": f"Bearer {self.access_token}"},
                name="/api/orders/[id]/ (poll)",
            )

            if check_response.status_code == 200:
                order_data = check_response.json()
                order_status = order_data.get("status")

                # "confirmed" 상태가 되면 결제 요청 가능
                if order_status in ["confirmed", "CONFIRMED", "주문확정"]:
                    order_ready = True
                    break
                # 실패 상태면 즉시 종료
                elif order_status in ["failed", "FAILED", "실패"]:
                    self.order_error = f"Order failed: {order_data.get('failure_reason', 'unknown')}"
                    return

            # 대기 후 재시도
            time.sleep(wait_interval)
            elapsed += wait_interval

        if not order_ready:
            self.order_error = f"Order not ready after {max_wait}s - order_id: {self.order_id}"
            return

        # 주문 준비 완료 후 결제 요청 (단 한 번만)
        payment_request_response = self.client.post(
            "/api/payments/request/",
            json={"order_id": self.order_id},
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/api/payments/request/",
        )

        if payment_request_response.status_code != 201:
            error_detail = payment_request_response.text[:200]
            try:
                error_detail = payment_request_response.json()
            except:
                pass
            self.payment_request_error = f"Payment request failed: {payment_request_response.status_code} - {error_detail}"
            return

        payment_data = payment_request_response.json()
        self.payment_id = payment_data.get("payment_id")
        self.payment_order_id = payment_data.get("order_id")  # order.id (정수)
        self.amount = payment_data.get("amount")

        # 디버깅: 결제 요청 응답 확인
        if not self.payment_order_id:
            self.payment_request_error = f"order_id not in response: {payment_data}"
            return

    @task
    def confirm_payment(self):
        """결제 승인 시도 - 한 번만 실행"""
        # 이미 실행했으면 스킵
        if hasattr(self, "_payment_confirmed"):
            return
        self._payment_confirmed = True

        if not hasattr(self, "access_token") or not hasattr(self, "payment_id"):
            # 준비 실패 시 건너뛰기
            result = {
                "username": getattr(self, "username", "unknown"),
                "success": False,
                "error": getattr(self, "register_error", None)
                or getattr(self, "login_error", None)
                or getattr(self, "cart_error", None)
                or getattr(self, "order_error", None)
                or getattr(self, "payment_request_error", None)
                or "Setup failed",
                "timestamp": time.time(),
            }
            self.__class__.payment_attempts.append(result)
            return

        # 결제 승인
        response = self.client.post(
            "/api/payments/confirm/",
            json={
                "order_id": self.payment_order_id,  # order_number -> payment_order_id
                "payment_key": f"test_payment_{self.payment_id}",
                "amount": self.amount,
            },
            headers={"Authorization": f"Bearer {self.access_token}"},
            name="/api/payments/confirm/",
        )

        # 결과 기록
        result = {
            "username": self.username,
            "status_code": response.status_code,
            "timestamp": time.time(),
        }

        if response.status_code == 202:
            result["success"] = True
            result["message"] = "✅ 202 Accepted - 성공"
        else:
            result["success"] = False
            # 상세 에러 정보 출력
            error_detail = response.text[:200]
            try:
                error_json = response.json()
                error_detail = str(error_json)[:200]
            except:
                pass
            result["message"] = f"❌ {response.status_code} - {error_detail}"
            # 디버깅용: 어떤 데이터를 보냈는지도 기록
            result["sent_data"] = {"order_id": self.payment_order_id, "payment_id": self.payment_id, "amount": self.amount}

        self.__class__.payment_attempts.append(result)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력"""
    attempts = ConcurrentPaymentUser.payment_attempts

    print("\n" + "=" * 70)
    print("🔍 500명 동시 결제 승인 테스트 결과")
    print("=" * 70)

    success_count = sum(1 for a in attempts if a.get("success"))
    fail_count = len(attempts) - success_count
    setup_error_count = sum(1 for a in attempts if "error" in a and not a.get("status_code"))

    print(f"\n📊 총 시도: {len(attempts)}명")
    print(f"✅ 성공 (202): {success_count}명")
    print(f"❌ 실패: {fail_count}명")
    print(f"⚠️  준비 실패: {setup_error_count}명")

    # 샘플 출력 (처음 10개)
    print("\n📝 샘플 결과 (처음 10개):")
    for i, attempt in enumerate(attempts[:10], 1):
        status = "✅" if attempt.get("success") else "❌"
        msg = attempt.get("message", attempt.get("error", "Unknown"))
        print(f"{i}. {status} {attempt.get('username', 'unknown')} - {msg}")

    print("\n" + "=" * 70)
    print("🎯 예상 결과:")
    print("  - 성공: 500명 (202 Accepted)")
    print("  - 실패: 0명")
    print("=" * 70)

    # 검증
    if success_count == 500:
        print("\n✅ 테스트 통과! 500명 모두 성공했습니다.")
    elif success_count > 400:
        print(f"\n⚠️  대부분 성공: {success_count}/500 성공 ({success_count/500*100:.1f}%)")
    else:
        print(f"\n❌ 테스트 실패! 성공: {success_count}/500 ({success_count/500*100:.1f}%)")

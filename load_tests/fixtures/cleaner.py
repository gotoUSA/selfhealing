"""
Test Data Cleaner

부하 테스트 후 생성된 테스트 데이터 정리

Usage:
    # 모든 테스트 데이터 정리
    python -m fixtures.cleaner

    # 특정 데이터만 정리
    python -m fixtures.cleaner --orders --carts
"""

import argparse
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class CleanerConfig:
    """정리 설정"""

    base_url: str = "http://localhost:8000"
    admin_username: str = "admin"
    admin_password: str = "admin123!"
    user_prefix: str = "loadtest_user_"


class TestDataCleaner:
    """테스트 데이터 정리기"""

    def __init__(self, config: Optional[CleanerConfig] = None):
        self.config = config or CleanerConfig()
        self.session = requests.Session()
        self.admin_token: Optional[str] = None
        self.stats = {
            "users_deleted": 0,
            "orders_cancelled": 0,
            "carts_cleared": 0,
            "products_deleted": 0,
        }

    def _get_admin_token(self) -> str:
        """관리자 토큰 획득"""
        if self.admin_token:
            return self.admin_token

        response = self.session.post(
            f"{self.config.base_url}/api/v1/accounts/login/",
            json={
                "username": self.config.admin_username,
                "password": self.config.admin_password,
            },
        )

        if response.status_code != 200:
            raise Exception(f"Admin login failed: {response.status_code}")

        data = response.json()
        self.admin_token = data.get("access_token") or data.get("access")
        return self.admin_token

    def clean_orders(self, cancel_pending: bool = True) -> int:
        """
        테스트 주문 정리

        - PENDING 주문은 취소
        - 다른 주문은 유지 (결제 완료/배송 등)
        """
        print("Cleaning test orders...")

        token = self._get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}

        # PENDING 주문 조회
        response = self.session.get(
            f"{self.config.base_url}/api/v1/orders/admin/list/?status=PENDING",
            headers=headers,
        )

        if response.status_code != 200:
            print(f"  Warning: Failed to fetch orders: {response.status_code}")
            return 0

        orders = response.json().get("results", response.json())
        if not isinstance(orders, list):
            orders = []

        cancelled = 0
        for order in orders:
            order_id = order.get("id")

            # loadtest 유저의 주문만 취소
            user = order.get("user", {})
            username = user.get("username", "") if isinstance(user, dict) else ""

            if self.config.user_prefix not in username:
                continue

            if cancel_pending:
                response = self.session.post(
                    f"{self.config.base_url}/api/v1/orders/{order_id}/cancel/",
                    headers=headers,
                )
                if response.status_code in (200, 204):
                    cancelled += 1

        self.stats["orders_cancelled"] = cancelled
        print(f"  Cancelled {cancelled} orders")
        return cancelled

    def clean_carts(self) -> int:
        """테스트 유저의 장바구니 비우기"""
        print("Cleaning test carts...")

        # loadtest 유저로 각각 로그인해서 장바구니 비우기
        # 대량 처리가 필요하면 관리자 API 또는 DB 직접 접근 필요

        cleared = 0
        print("  (Cart cleaning requires individual user login - skipped)")

        self.stats["carts_cleared"] = cleared
        return cleared

    def clean_users(self, delete_users: bool = False) -> int:
        """
        테스트 유저 정리

        WARNING: 유저 삭제는 CASCADE로 연관 데이터도 삭제됨
        """
        if not delete_users:
            print("User deletion skipped (use --delete-users to enable)")
            return 0

        print("Cleaning test users...")

        token = self._get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}

        # loadtest 유저 조회
        response = self.session.get(
            f"{self.config.base_url}/api/v1/accounts/admin/users/?search={self.config.user_prefix}",
            headers=headers,
        )

        if response.status_code != 200:
            print(f"  Warning: Failed to fetch users: {response.status_code}")
            return 0

        users = response.json().get("results", response.json())
        if not isinstance(users, list):
            users = []

        deleted = 0
        for user in users:
            user_id = user.get("id")
            username = user.get("username", "")

            if not username.startswith(self.config.user_prefix):
                continue

            response = self.session.delete(
                f"{self.config.base_url}/api/v1/accounts/admin/users/{user_id}/",
                headers=headers,
            )

            if response.status_code in (200, 204):
                deleted += 1

        self.stats["users_deleted"] = deleted
        print(f"  Deleted {deleted} users")
        return deleted

    def clean_products(self, delete_loadtest_products: bool = False) -> int:
        """LoadTest 상품 정리"""
        if not delete_loadtest_products:
            print("Product deletion skipped (use --delete-products to enable)")
            return 0

        print("Cleaning test products...")

        token = self._get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}

        # LoadTest 상품 조회
        response = self.session.get(
            f"{self.config.base_url}/api/v1/products/?search=LoadTest",
            headers=headers,
        )

        if response.status_code != 200:
            print(f"  Warning: Failed to fetch products: {response.status_code}")
            return 0

        products = response.json().get("results", response.json())
        if not isinstance(products, list):
            products = []

        deleted = 0
        for product in products:
            product_id = product.get("id")
            name = product.get("name", "")

            if not name.startswith("LoadTest"):
                continue

            response = self.session.delete(
                f"{self.config.base_url}/api/v1/products/{product_id}/",
                headers=headers,
            )

            if response.status_code in (200, 204):
                deleted += 1

        self.stats["products_deleted"] = deleted
        print(f"  Deleted {deleted} products")
        return deleted

    def clean_all(
        self,
        delete_users: bool = False,
        delete_products: bool = False,
    ) -> dict:
        """전체 정리"""
        print("=" * 60)
        print("Starting test data cleanup...")
        print("=" * 60)

        self.clean_orders()
        self.clean_carts()
        self.clean_users(delete_users)
        self.clean_products(delete_products)

        print("=" * 60)
        print("Cleanup completed!")
        print(f"  Orders cancelled: {self.stats['orders_cancelled']}")
        print(f"  Carts cleared: {self.stats['carts_cleared']}")
        print(f"  Users deleted: {self.stats['users_deleted']}")
        print(f"  Products deleted: {self.stats['products_deleted']}")
        print("=" * 60)

        return self.stats


def main():
    parser = argparse.ArgumentParser(description="Load Test Data Cleaner")
    parser.add_argument("--host", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--admin-user", default="admin", help="Admin username")
    parser.add_argument("--admin-pass", default="admin123!", help="Admin password")
    parser.add_argument("--orders", action="store_true", help="Clean orders only")
    parser.add_argument("--carts", action="store_true", help="Clean carts only")
    parser.add_argument("--delete-users", action="store_true", help="Delete test users (DESTRUCTIVE)")
    parser.add_argument("--delete-products", action="store_true", help="Delete test products (DESTRUCTIVE)")

    args = parser.parse_args()

    config = CleanerConfig(
        base_url=args.host,
        admin_username=args.admin_user,
        admin_password=args.admin_pass,
    )

    cleaner = TestDataCleaner(config)

    # 특정 타입만 정리
    if args.orders:
        cleaner.clean_orders()
    elif args.carts:
        cleaner.clean_carts()
    else:
        # 전체 정리
        cleaner.clean_all(
            delete_users=args.delete_users,
            delete_products=args.delete_products,
        )


if __name__ == "__main__":
    main()

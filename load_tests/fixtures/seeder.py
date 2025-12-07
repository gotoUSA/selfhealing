"""
Test Data Seeder

부하 테스트에 필요한 테스트 데이터 생성

Usage:
    # 기본 테스트 데이터 생성
    python -m fixtures.seeder
    
    # 커스텀 설정
    python -m fixtures.seeder --users 200 --products 100
"""

import os
import sys
import random
import string
import argparse
from dataclasses import dataclass
from typing import List, Optional

import requests


@dataclass
class SeederConfig:
    """시딩 설정"""
    base_url: str = "http://localhost:8000"
    num_users: int = 100
    num_products: int = 50
    user_password: str = "TestPassword123!"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    
    # 유저 접두사 (load test 유저 식별용)
    user_prefix: str = "loadtest_user_"
    
    # 상품 설정
    product_price_min: int = 1000
    product_price_max: int = 100000
    product_stock_min: int = 10
    product_stock_max: int = 1000


class TestDataSeeder:
    """테스트 데이터 생성기"""
    
    def __init__(self, config: Optional[SeederConfig] = None):
        self.config = config or SeederConfig()
        self.session = requests.Session()
        self.admin_token: Optional[str] = None
        self.created_users: List[dict] = []
        self.created_products: List[dict] = []
    
    def _get_admin_token(self) -> str:
        """관리자 토큰 획득"""
        if self.admin_token:
            return self.admin_token
        
        response = self.session.post(
            f"{self.config.base_url}/api/v1/accounts/login/",
            json={
                "username": self.config.admin_username,
                "password": self.config.admin_password,
            }
        )
        
        if response.status_code != 200:
            raise Exception(f"Admin login failed: {response.status_code}")
        
        data = response.json()
        self.admin_token = data.get("access_token") or data.get("access")
        return self.admin_token
    
    def _random_string(self, length: int = 8) -> str:
        """랜덤 문자열 생성"""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    
    def seed_users(self, count: Optional[int] = None) -> List[dict]:
        """테스트 유저 생성"""
        count = count or self.config.num_users
        
        print(f"Creating {count} test users...")
        
        for i in range(count):
            username = f"{self.config.user_prefix}{i:04d}"
            email = f"{username}@test.local"
            
            response = self.session.post(
                f"{self.config.base_url}/api/v1/accounts/register/",
                json={
                    "username": username,
                    "email": email,
                    "password": self.config.user_password,
                    "password2": self.config.user_password,
                }
            )
            
            if response.status_code in (200, 201):
                user = {
                    "username": username,
                    "email": email,
                    "password": self.config.user_password,
                }
                self.created_users.append(user)
                
                if (i + 1) % 20 == 0:
                    print(f"  Created {i + 1}/{count} users...")
            elif response.status_code == 400:
                # 이미 존재하는 유저는 스킵
                pass
            else:
                print(f"  Warning: Failed to create user {username}: {response.status_code}")
        
        print(f"Created {len(self.created_users)} users")
        return self.created_users
    
    def seed_products(self, count: Optional[int] = None) -> List[dict]:
        """테스트 상품 생성 (관리자 권한 필요)"""
        count = count or self.config.num_products
        
        print(f"Creating {count} test products...")
        
        token = self._get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        categories = ["Electronics", "Clothing", "Books", "Food", "Toys"]
        
        for i in range(count):
            product_data = {
                "name": f"LoadTest Product {i:04d}",
                "description": f"Test product for load testing - {self._random_string(16)}",
                "price": random.randint(
                    self.config.product_price_min, 
                    self.config.product_price_max
                ),
                "stock": random.randint(
                    self.config.product_stock_min,
                    self.config.product_stock_max
                ),
                "category": random.choice(categories),
            }
            
            response = self.session.post(
                f"{self.config.base_url}/api/v1/products/",
                json=product_data,
                headers=headers,
            )
            
            if response.status_code in (200, 201):
                product = response.json()
                self.created_products.append(product)
                
                if (i + 1) % 10 == 0:
                    print(f"  Created {i + 1}/{count} products...")
            else:
                print(f"  Warning: Failed to create product: {response.status_code}")
        
        print(f"Created {len(self.created_products)} products")
        return self.created_products
    
    def seed_points(self, amount: int = 100000) -> None:
        """테스트 유저들에게 포인트 지급"""
        print(f"Granting {amount} points to each user...")
        
        token = self._get_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        for user in self.created_users:
            response = self.session.post(
                f"{self.config.base_url}/api/v1/accounts/admin/grant-points/",
                json={
                    "username": user["username"],
                    "amount": amount,
                    "reason": "Load test initial points",
                },
                headers=headers,
            )
            
            if response.status_code not in (200, 201):
                print(f"  Warning: Failed to grant points to {user['username']}")
        
        print("Points granted")
    
    def seed_all(self) -> dict:
        """전체 시딩"""
        print("=" * 60)
        print("Starting test data seeding...")
        print("=" * 60)
        
        users = self.seed_users()
        products = self.seed_products()
        
        # 포인트 지급 (선택적)
        # self.seed_points()
        
        print("=" * 60)
        print("Seeding completed!")
        print(f"  Users: {len(users)}")
        print(f"  Products: {len(products)}")
        print("=" * 60)
        
        return {
            "users": users,
            "products": products,
        }
    
    def export_users_csv(self, filename: str = "load_test_users.csv") -> None:
        """유저 목록 CSV 내보내기"""
        import csv
        
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["username", "email", "password"])
            writer.writeheader()
            writer.writerows(self.created_users)
        
        print(f"Exported users to {filename}")


def main():
    parser = argparse.ArgumentParser(description="Load Test Data Seeder")
    parser.add_argument("--host", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--users", type=int, default=100, help="Number of users to create")
    parser.add_argument("--products", type=int, default=50, help="Number of products to create")
    parser.add_argument("--admin-user", default="admin", help="Admin username")
    parser.add_argument("--admin-pass", default="admin123", help="Admin password")
    parser.add_argument("--export", action="store_true", help="Export users to CSV")
    
    args = parser.parse_args()
    
    config = SeederConfig(
        base_url=args.host,
        num_users=args.users,
        num_products=args.products,
        admin_username=args.admin_user,
        admin_password=args.admin_pass,
    )
    
    seeder = TestDataSeeder(config)
    seeder.seed_all()
    
    if args.export:
        seeder.export_users_csv()


if __name__ == "__main__":
    main()

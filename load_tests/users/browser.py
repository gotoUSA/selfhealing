"""
브라우저 사용자 (Browser User)

실제 서비스의 60~70%를 차지하는 사용자 유형.
상품 목록/상세 조회만 하고 구매 의도 없음.
"""

import random
from locust import task, tag

from .base import BaseUser
from load_tests.config import ENDPOINTS


class BrowserUser(BaseUser):
    """
    브라우징만 하는 사용자
    
    행동 패턴:
    - 상품 목록 조회 (가장 빈번)
    - 상품 상세 조회
    - 카테고리 조회
    - 상품 검색
    
    로그인: 불필요
    """
    
    @task(10)
    @tag("read", "products")
    def browse_product_list(self):
        """상품 목록 조회"""
        page = random.randint(1, 5)
        ordering = random.choice(["-created_at", "price", "-price", ""])
        
        params = f"?page={page}"
        if ordering:
            params += f"&ordering={ordering}"
        
        self.client.get(
            f"{ENDPOINTS['products']}{params}",
            name="GET /api/products/",
        )
    
    @task(5)
    @tag("read", "products")
    def view_product_detail(self):
        """상품 상세 조회"""
        product_id = self.get_random_product_id()
        if product_id:
            self.client.get(
                ENDPOINTS["product_detail"].format(id=product_id),
                name="GET /api/products/{id}/",
            )
    
    @task(3)
    @tag("read", "products")
    def search_products(self):
        """상품 검색"""
        keywords = ["테스트", "상품", "노트북", "의류", ""]
        keyword = random.choice(keywords)
        
        self.client.get(
            f"{ENDPOINTS['products']}?search={keyword}",
            name="GET /api/products/?search=",
        )
    
    @task(2)
    @tag("read", "categories")
    def view_categories(self):
        """카테고리 목록 조회"""
        self.client.get(
            ENDPOINTS["categories"],
            name="GET /api/categories/",
        )
    
    @task(1)
    @tag("read", "categories")
    def view_category_tree(self):
        """카테고리 트리 조회"""
        self.client.get(
            f"{ENDPOINTS['categories']}tree/",
            name="GET /api/categories/tree/",
        )

"""
상품 헬퍼 - 상품 조회 및 캐싱

모든 Stage 시나리오에서 재사용
"""

import random
from typing import List, Optional, Dict, Any

from load_tests.config import ENDPOINTS


class ProductHelper:
    """상품 조회 및 캐싱 헬퍼"""

    # 클래스 레벨 캐시 (모든 User 인스턴스에서 공유)
    _product_ids_cache: List[int] = []
    _products_cache: List[Dict[str, Any]] = []
    _cache_initialized: bool = False

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name

    @classmethod
    def reset_cache(cls):
        """캐시 초기화 (테스트 간 독립성 필요시)"""
        cls._product_ids_cache = []
        cls._products_cache = []
        cls._cache_initialized = False

    def ensure_products_cached(self, pages: int = 2) -> bool:
        """
        상품 목록 캐싱 (최초 1회만 실행)

        Args:
            pages: 조회할 페이지 수

        Returns:
            캐싱 성공 여부
        """
        if ProductHelper._cache_initialized:
            return True

        return self._fetch_products(pages)

    def _fetch_products(self, pages: int = 2) -> bool:
        """상품 목록 조회 및 캐싱"""
        request_name = f"{self.stage_name} [Setup] Fetch Products".strip()

        for page in range(1, pages + 1):
            with self.client.get(
                f"{ENDPOINTS['products']}?page={page}",
                name=request_name,
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])

                    for product in results:
                        product_id = product.get("id")
                        if product_id and product_id not in ProductHelper._product_ids_cache:
                            ProductHelper._product_ids_cache.append(product_id)
                            ProductHelper._products_cache.append(product)
                    response.success()
                elif response.status_code == 404:
                    # 페이지가 존재하지 않으면 (상품 수가 적으면) 정상 처리
                    response.success()
                    break
                else:
                    response.failure(f"Unexpected status: {response.status_code}")

        ProductHelper._cache_initialized = bool(ProductHelper._product_ids_cache)
        return ProductHelper._cache_initialized

    def get_random_product_id(self) -> Optional[int]:
        """랜덤 상품 ID 반환"""
        if not ProductHelper._product_ids_cache:
            self.ensure_products_cached()

        if ProductHelper._product_ids_cache:
            return random.choice(ProductHelper._product_ids_cache)
        return None

    def get_random_product_ids(self, count: int = 3) -> List[int]:
        """랜덤 상품 ID 목록 반환 (중복 가능)"""
        if not ProductHelper._product_ids_cache:
            self.ensure_products_cached()

        if not ProductHelper._product_ids_cache:
            return []

        return [
            random.choice(ProductHelper._product_ids_cache) for _ in range(min(count, len(ProductHelper._product_ids_cache)))
        ]

    def get_product_detail(self, product_id: int) -> Optional[Dict[str, Any]]:
        """상품 상세 조회"""
        request_name = f"{self.stage_name} GET /api/products/{{id}}/".strip()

        response = self.client.get(
            ENDPOINTS["product_detail"].format(id=product_id),
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    def get_product_stock(self, product_id: int) -> Optional[int]:
        """상품 재고 조회"""
        product = self.get_product_detail(product_id)
        if product:
            return product.get("stock", 0)
        return None

    def browse_products(self, page: int = 1) -> Optional[Dict[str, Any]]:
        """상품 목록 조회 (메트릭 기록용)"""
        request_name = f"{self.stage_name} GET /api/products/".strip()

        with self.client.get(
            f"{ENDPOINTS['products']}?page={page}",
            name=request_name,
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
                return response.json()
            elif response.status_code == 404:
                # 페이지 범위 초과는 정상 처리 (상품 수가 적은 경우)
                response.success()
                return None
            else:
                response.failure(f"Unexpected status: {response.status_code}")
                return None

    def browse_categories(self) -> Optional[Dict[str, Any]]:
        """카테고리 목록 조회"""
        request_name = f"{self.stage_name} GET /api/categories/".strip()

        response = self.client.get(
            ENDPOINTS["categories"],
            name=request_name,
        )

        if response.status_code == 200:
            return response.json()
        return None

    @property
    def cached_product_ids(self) -> List[int]:
        """캐시된 상품 ID 목록"""
        return ProductHelper._product_ids_cache.copy()

    @property
    def has_products(self) -> bool:
        """캐시된 상품이 있는지 확인"""
        return bool(ProductHelper._product_ids_cache)

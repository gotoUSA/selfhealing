"""
기본 사용자 클래스

모든 사용자 타입이 상속받는 베이스 클래스.
공통 기능: 로그인, 상품 ID 캐싱, 헬퍼 메서드
"""

import random
from typing import Optional
from locust import HttpUser, between

from load_tests.config import (
    TEST_USER_COUNT,
    TEST_USER_PREFIX,
    TEST_USER_PASSWORD,
    WAIT_TIME_MIN,
    WAIT_TIME_MAX,
    ENDPOINTS,
)


class BaseUser(HttpUser):
    """
    기본 사용자 클래스

    - 테스트 시작 시 상품 ID 목록 캐싱
    - 필요 시 로그인 처리
    - 공통 헬퍼 메서드 제공
    """

    abstract = True  # 직접 인스턴스화하지 않음
    wait_time = between(WAIT_TIME_MIN, WAIT_TIME_MAX)

    # 클래스 레벨 캐시 (모든 인스턴스가 공유)
    _product_ids_cache: list = []
    _cache_initialized: bool = False

    def on_start(self):
        """테스트 시작 시 초기화"""
        self.is_logged_in = False
        self.user_id = None
        self.access_token = None

        # 상품 ID 캐싱 (첫 사용자만 실행)
        if not BaseUser._cache_initialized:
            self._fetch_product_ids()
            BaseUser._cache_initialized = True

    def _fetch_product_ids(self):
        """상품 ID 목록 조회 및 캐싱"""
        for page in range(1, 4):  # 최대 3페이지
            with self.client.get(
                f"{ENDPOINTS['products']}?page={page}", name="[Setup] Fetch Product IDs", catch_response=True
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    BaseUser._product_ids_cache.extend([p["id"] for p in results])
                    response.success()

                    # 더 이상 페이지가 없으면 중단
                    if not data.get("next"):
                        break
                else:
                    response.failure(f"Failed to fetch products: {response.status_code}")
                    break

    @property
    def product_ids(self) -> list:
        """캐싱된 상품 ID 목록"""
        return BaseUser._product_ids_cache

    def get_random_product_id(self) -> Optional[int]:
        """랜덤 상품 ID 반환"""
        if self.product_ids:
            return random.choice(self.product_ids)
        return None

    def login(self) -> bool:
        """
        테스트 사용자로 로그인

        Returns:
            bool: 로그인 성공 여부
        """
        if self.is_logged_in:
            return True

        user_index = random.randint(0, TEST_USER_COUNT - 1)
        username = f"{TEST_USER_PREFIX}{user_index}"

        with self.client.post(
            ENDPOINTS["login"],
            json={
                "username": username,
                "password": TEST_USER_PASSWORD,
            },
            name="POST /api/auth/login/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access")
                self.user_id = data.get("user", {}).get("id")

                # Authorization 헤더 설정
                self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})

                self.is_logged_in = True
                response.success()
                return True
            else:
                response.failure(f"Login failed: {response.status_code}")
                return False

    def ensure_logged_in(self) -> bool:
        """로그인 상태 보장"""
        if not self.is_logged_in:
            return self.login()
        return True

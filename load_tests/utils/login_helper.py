"""
로그인 헬퍼 - 인증 관련 공통 로직

모든 Stage 시나리오에서 재사용
"""

import random
from typing import Optional

from load_tests.config import (
    TEST_USER_COUNT,
    TEST_USER_PREFIX,
    TEST_USER_PASSWORD,
    ENDPOINTS,
)


class LoginHelper:
    """로그인 및 토큰 관리 헬퍼"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름 (예: "[Stage0]")
        """
        self.client = client
        self.stage_name = stage_name
        self.is_logged_in = False
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.user_id: Optional[int] = None
        self.username: Optional[str] = None

    def login(self, user_index: Optional[int] = None) -> bool:
        """
        테스트 사용자로 로그인

        Args:
            user_index: 특정 사용자 인덱스 (None이면 랜덤)

        Returns:
            로그인 성공 여부
        """
        if self.is_logged_in:
            return True

        if user_index is None:
            user_index = random.randint(0, TEST_USER_COUNT - 1)

        self.username = f"{TEST_USER_PREFIX}{user_index}"

        request_name = f"{self.stage_name} POST /api/auth/login/".strip()

        response = self.client.post(
            ENDPOINTS["login"],
            json={
                "username": self.username,
                "password": TEST_USER_PASSWORD,
            },
            name=request_name,
        )

        if response.status_code == 200:
            data = response.json()
            # 응답 구조: {"token": {"access": "...", "refresh": "..."}, "user": {...}}
            token_data = data.get("token", {})
            self.access_token = token_data.get("access")
            self.refresh_token = token_data.get("refresh")
            self.user_id = data.get("user", {}).get("id")

            if self.access_token:
                self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
                self.is_logged_in = True
                return True

        return False

    def logout(self) -> bool:
        """로그아웃"""
        if not self.is_logged_in:
            return True

        request_name = f"{self.stage_name} POST /api/auth/logout/".strip()

        response = self.client.post(
            ENDPOINTS["logout"],
            name=request_name,
        )

        if response.status_code in [200, 204]:
            self._clear_auth()
            return True

        return False

    def refresh_access_token(self) -> bool:
        """액세스 토큰 갱신"""
        if not self.refresh_token:
            return False

        request_name = f"{self.stage_name} POST /api/auth/token/refresh/".strip()

        response = self.client.post(
            ENDPOINTS["token_refresh"],
            json={"refresh": self.refresh_token},
            name=request_name,
        )

        if response.status_code == 200:
            data = response.json()
            self.access_token = data.get("access")
            if self.access_token:
                self.client.headers.update({"Authorization": f"Bearer {self.access_token}"})
                return True

        return False

    def ensure_logged_in(self, user_index: Optional[int] = None) -> bool:
        """로그인 상태 보장 (필요시 로그인)"""
        if not self.is_logged_in:
            return self.login(user_index)
        return True

    def _clear_auth(self):
        """인증 정보 초기화"""
        self.is_logged_in = False
        self.access_token = None
        self.refresh_token = None
        self.user_id = None
        self.username = None
        if "Authorization" in self.client.headers:
            del self.client.headers["Authorization"]

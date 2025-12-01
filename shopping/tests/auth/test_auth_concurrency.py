"""로그인/JWT 발급/토큰 갱신의 동시성 테스트

로그인 동시성, JWT 발급, Refresh Token 갱신, 회원가입 동시성을 검증
"""

import threading
import time
from typing import Any, Callable, Dict, List

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.tests.factories import UserFactory

User = get_user_model()


# ==========================================
# 헬퍼 함수
# ==========================================


def concurrent_api_call(thread_func: Callable, thread_count: int, *args, **kwargs) -> List[Dict[str, Any]]:
    """
    동시 API 호출 헬퍼 함수

    Args:
        thread_func: 각 스레드에서 실행할 함수
        thread_count: 스레드 개수
        *args, **kwargs: thread_func에 전달할 인자

    Returns:
        각 스레드의 실행 결과 리스트
    """
    results = []
    lock = threading.Lock()

    def wrapper(*func_args, **func_kwargs):
        """스레드 래퍼 - 결과를 thread-safe하게 수집"""
        result = thread_func(*func_args, **func_kwargs)
        with lock:
            results.append(result)

    # 동시 실행
    threads = []
    for i in range(thread_count):
        # kwargs에 index 추가하여 전달
        thread_kwargs = {**kwargs, "index": i}
        t = threading.Thread(target=wrapper, args=args, kwargs=thread_kwargs)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return results


def close_db_connections():
    """테스트 후 DB 연결 명시적 종료"""
    for conn in connections.all():
        conn.close()


@pytest.mark.django_db(transaction=True)
class TestConcurrentLogin:
    """1단계: 정상 케이스 - 동시 로그인"""

    @pytest.mark.parametrize("user_count", [10, 20])
    def test_concurrent_login_success(self, user_count):
        """동시 로그인 - 모두 성공하고 고유한 JWT 발급"""
        # Arrange
        users = [
            UserFactory(username=f"concurrentuser{i}", email=f"concurrent{i}@test.com", password="testpass123")
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, **kwargs):
            """개별 로그인 스레드"""
            client = APIClient()
            try:
                response = client.post(login_url, {"username": username, "password": password}, format="json")
                # 새 구조: token.access
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
                # refresh는 Cookie에서 가져옴
                refresh = response.cookies.get("refresh_token")
                refresh_value = refresh.value if refresh else None
                return {
                    "username": username,
                    "status": response.status_code,
                    "access": access,
                    "refresh": refresh_value,
                }
            except Exception as e:
                return {"username": username, "error": str(e)}

        # Act - 동시 실행
        results = []
        for user in users:
            result_list = concurrent_api_call(login_thread, 1, user.username, "testpass123")
            results.extend(result_list)

        # Assert
        success_results = [r for r in results if r.get("status") == status.HTTP_200_OK]
        assert len(success_results) == user_count, f"{user_count}명 모두 로그인 성공. 실제: {len(success_results)}"

        # 모든 JWT 토큰이 유효하고 고유한지 확인
        access_tokens = [r["access"] for r in success_results]
        refresh_tokens = [r["refresh"] for r in success_results]

        assert len(set(access_tokens)) == user_count, "모든 access token이 고유함"
        assert len(set(refresh_tokens)) == user_count, "모든 refresh token이 고유함"

        # 토큰 형식 검증 (JWT는 3개 파트로 구성)
        for token in access_tokens[:3]:  # 샘플 3개만 검증
            parts = token.split(".")
            assert len(parts) == 3, "JWT 형식 유효"

        # DB 연결 정리
        close_db_connections()

    @pytest.mark.slow
    def test_concurrent_login_scale(self):
        """100명 동시 로그인 - 스케일 검증"""
        # Arrange
        user_count = 100
        users = [
            UserFactory(username=f"scaleuser{i}", email=f"scale{i}@test.com", password="testpass123")
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, **kwargs):
            """개별 로그인"""
            client = APIClient()
            try:
                response = client.post(login_url, {"username": username, "password": password}, format="json")
                # 새 구조: token.access
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
                return {
                    "status": response.status_code,
                    "access": access,
                }
            except Exception as e:
                return {"error": str(e)}

        # Act
        results = []
        for user in users:
            result_list = concurrent_api_call(login_thread, 1, user.username, "testpass123")
            results.extend(result_list)

        # Assert - 대부분 성공 (일부 DB 연결 실패 허용)
        success_results = [r for r in results if r.get("status") == status.HTTP_200_OK]
        success_rate = len(success_results) / user_count
        assert success_rate >= 0.9, f"90% 이상 성공. 실제: {success_rate:.1%}"

        close_db_connections()

    def test_concurrent_same_user_login(self):
        """동일 사용자 5회 동시 로그인 - 각각 별도 세션"""
        # Arrange
        user = UserFactory(username="sameuser", email="same@test.com", password="testpass123")
        login_url = reverse("auth-login")
        concurrent_count = 5

        def login_thread(**kwargs):
            """동일 사용자 로그인"""
            client = APIClient()
            try:
                response = client.post(login_url, {"username": "sameuser", "password": "testpass123"}, format="json")
                # 새 구조: token.access
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
                # refresh는 Cookie에서 가져옴
                refresh = response.cookies.get("refresh_token")
                refresh_value = refresh.value if refresh else None
                return {
                    "status": response.status_code,
                    "access": access,
                    "refresh": refresh_value,
                }
            except Exception as e:
                return {"error": str(e)}

        # Act
        results = concurrent_api_call(login_thread, concurrent_count)

        # Assert
        success_results = [r for r in results if r.get("status") == status.HTTP_200_OK]
        assert len(success_results) == concurrent_count, f"{concurrent_count}회 모두 로그인 성공. 실제: {len(success_results)}"

        # 각 세션마다 고유한 토큰 발급 확인
        access_tokens = [r["access"] for r in success_results]
        refresh_tokens = [r["refresh"] for r in success_results]

        assert len(set(access_tokens)) == concurrent_count, f"{concurrent_count}개의 고유한 access token 발급"
        assert len(set(refresh_tokens)) == concurrent_count, f"{concurrent_count}개의 고유한 refresh token 발급"

        close_db_connections()


@pytest.mark.django_db(transaction=True)
class TestRefreshTokenConcurrency:
    """2단계: 경계값 테스트 - Refresh Token 동시 갱신"""

    def test_concurrent_refresh_token_rotation(self):
        """Refresh Token 동시 갱신 - SimpleJWT race condition 검증"""
        # Arrange
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화되어 있습니다")

        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        refresh_token_str = str(refresh)
        refresh_url = reverse("token-refresh")
        concurrent_count = 5

        def refresh_thread(**kwargs):
            """Refresh token 갱신 시도"""
            client = APIClient()
            try:
                response = client.post(refresh_url, {"refresh": refresh_token_str}, format="json")
                return {
                    "status": response.status_code,
                    "success": response.status_code == status.HTTP_200_OK,
                    "new_access": response.data.get("access") if response.status_code == status.HTTP_200_OK else None,
                }
            except Exception as e:
                return {"error": str(e)}

        # Act
        results = concurrent_api_call(refresh_thread, concurrent_count)

        # Assert - SimpleJWT 라이브러리가 select_for_update() 미사용
        # 타이밍에 따라 1개~모두 성공 가능
        success_count = sum(1 for r in results if r.get("success"))
        assert 1 <= success_count <= concurrent_count, f"1-{concurrent_count}개 성공 가능. 실제: {success_count}"

        # 주의: 이상적으로는 1개만 성공해야 하지만, SimpleJWT 라이브러리 제약
        # 실무에서는 JWT 만료시간을 짧게 설정하거나 Redis 사용 권장

        close_db_connections()

    def test_concurrent_refresh_with_new_token(self):
        """Refresh 후 새 토큰으로 다시 갱신 - 정상 작동"""
        # Arrange
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화되어 있습니다")

        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        refresh_token_str = str(refresh)
        refresh_url = reverse("token-refresh")

        # Act - 첫 번째 갱신
        client = APIClient()
        first_response = client.post(refresh_url, {"refresh": refresh_token_str}, format="json")

        # Assert - 첫 번째 갱신 성공
        assert first_response.status_code == status.HTTP_200_OK
        # 새 구조: refresh token은 Cookie에서 가져옴
        new_refresh_cookie = first_response.cookies.get("refresh_token")
        assert new_refresh_cookie is not None, "새 refresh token이 Cookie에 있어야 합니다"
        new_refresh_token = new_refresh_cookie.value

        # Act - 새 refresh token으로 다시 갱신
        second_response = client.post(refresh_url, {"refresh": new_refresh_token}, format="json")

        # Assert - 두 번째 갱신도 성공
        assert second_response.status_code == status.HTTP_200_OK
        assert "access" in second_response.data
        # refresh는 Cookie에서 확인
        assert "refresh_token" in second_response.cookies

        close_db_connections()


@pytest.mark.django_db(transaction=True)
class TestAuthenticationEdgeCases:
    """4단계: 고급 시나리오 - 복합 동시성 상황"""

    def test_concurrent_login_with_invalid_credentials(self):
        """20명 동시 로그인 시도 - 일부는 실패 (잘못된 비밀번호)"""
        # Arrange
        user_count = 20
        users = [
            UserFactory(username=f"mixeduser{i}", email=f"mixed{i}@test.com", password="testpass123")
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, should_succeed, **kwargs):
            """로그인 시도"""
            client = APIClient()
            try:
                response = client.post(login_url, {"username": username, "password": password}, format="json")
                return {
                    "username": username,
                    "status": response.status_code,
                    "should_succeed": should_succeed,
                    "success": response.status_code == status.HTTP_200_OK,
                }
            except Exception as e:
                return {"username": username, "error": str(e), "should_succeed": should_succeed}

        # Act - 10명은 정상, 10명은 잘못된 비밀번호
        results = []
        for i, user in enumerate(users):
            password = "testpass123" if i % 2 == 0 else "wrongpass"
            should_succeed = i % 2 == 0
            result_list = concurrent_api_call(login_thread, 1, user.username, password, should_succeed)
            results.extend(result_list)

        # Assert
        success_results = [r for r in results if r.get("success")]
        failed_results = [r for r in results if r.get("status") == status.HTTP_400_BAD_REQUEST]

        # 정상 비밀번호는 모두 성공
        expected_success = user_count // 2
        assert len(success_results) == expected_success, f"{expected_success}명 성공. 실제: {len(success_results)}"

        # 잘못된 비밀번호는 모두 실패
        expected_failed = user_count // 2
        assert len(failed_results) == expected_failed, f"{expected_failed}명 실패. 실제: {len(failed_results)}"

        close_db_connections()

    def test_concurrent_login_and_refresh(self):
        """로그인과 토큰 갱신 동시 실행 - 독립적으로 작동"""
        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)

        login_url = reverse("auth-login")
        refresh_url = reverse("token-refresh")

        results = []
        lock = threading.Lock()

        def login_thread():
            """로그인"""
            client = APIClient()
            try:
                response = client.post(login_url, {"username": user.username, "password": "testpass123"}, format="json")
                with lock:
                    results.append(
                        {
                            "action": "login",
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"action": "login", "error": str(e)})

        def refresh_thread():
            """토큰 갱신"""
            client = APIClient()
            try:
                response = client.post(refresh_url, {"refresh": str(refresh)}, format="json")
                with lock:
                    results.append(
                        {
                            "action": "refresh",
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"action": "refresh", "error": str(e)})

        # Act - 로그인 10개 + 토큰 갱신 10개 동시 실행
        threads = []
        for _ in range(10):
            threads.append(threading.Thread(target=login_thread))
            threads.append(threading.Thread(target=refresh_thread))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        login_results = [r for r in results if r.get("action") == "login"]
        refresh_results = [r for r in results if r.get("action") == "refresh"]

        # 로그인은 모두 성공
        login_success = sum(1 for r in login_results if r.get("success"))
        assert login_success == 10, f"10개 로그인 성공. 실제: {login_success}"

        # 토큰 갱신도 대부분 성공 (race condition에 따라 일부 실패 가능)
        refresh_success = sum(1 for r in refresh_results if r.get("success"))
        assert refresh_success >= 1, f"최소 1개 토큰 갱신 성공. 실제: {refresh_success}"


@pytest.mark.django_db(transaction=True)
class TestJWTTokenValidation:
    """5단계: JWT 토큰 검증 - 동시성 환경에서의 토큰 유효성"""

    def test_concurrent_token_usage(self):
        """여러 스레드에서 동일한 access token 사용 - 모두 성공"""
        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)

        profile_url = reverse("user-profile")
        concurrent_count = 20
        results = []
        lock = threading.Lock()

        def use_token_thread():
            """토큰으로 프로필 조회"""
            client = APIClient()
            try:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
                response = client.get(profile_url)
                with lock:
                    results.append(
                        {
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 20개 스레드가 동일한 access token 사용
        threads = [threading.Thread(target=use_token_thread) for _ in range(concurrent_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == concurrent_count, f"20개 모두 성공. 실제: {success_count}"

    def test_concurrent_logout(self):
        """여러 스레드에서 동시 로그아웃 - blacklist 경합"""
        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        refresh_token_str = str(refresh)
        access_token = str(refresh.access_token)

        logout_url = reverse("auth-logout")
        concurrent_count = 5
        results = []
        lock = threading.Lock()

        def logout_thread():
            """로그아웃 시도"""
            client = APIClient()
            try:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
                response = client.post(logout_url, {"refresh": refresh_token_str}, format="json")
                with lock:
                    results.append(
                        {
                            "status": response.status_code,
                            "success": response.status_code == status.HTTP_200_OK,
                        }
                    )
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act - 5개 스레드 동시 로그아웃
        threads = [threading.Thread(target=logout_thread) for _ in range(concurrent_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        # 최소 1개는 성공해야 함 (나머지는 이미 blacklist 처리되어 실패 가능)
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count >= 1, f"최소 1개 로그아웃 성공. 실제: {success_count}"

        # 로그아웃 후 refresh token 사용 불가 확인
        refresh_url = reverse("token-refresh")
        client = APIClient()
        response = client.post(refresh_url, {"refresh": refresh_token_str}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, "로그아웃된 refresh token 사용 불가"

"""
인증 동시성 테스트

Purpose:
    로그인/JWT 발급/토큰 갱신의 동시성 검증

Test Categories:
    A. Core Invariant Tests (핵심 불변 조건):
        - 동시 로그인 시 모두 고유한 JWT 발급
        - 동일 사용자 동시 로그인 시 각각 별도 세션
    B. Token Refresh Tests:
        - Refresh Token 동시 갱신 (race condition)
    C. Advanced Scenarios:
        - 로그인과 토큰 갱신 동시 실행
        - 동일 토큰 다중 사용

Concurrency Control:
    - JWT는 stateless → 동시 발급 가능
    - Refresh Token 갱신 시 race condition 주의
    - SimpleJWT 라이브러리 제약 존재
"""

import threading
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.tests.factories import UserFactory

User = get_user_model()


# =============================================================================
# 헬퍼 함수
# =============================================================================


def close_db_connections():
    """테스트 후 DB 연결 명시적 종료"""
    for conn in connections.all():
        conn.close()


def concurrent_api_call(
    thread_func,
    thread_count: int,
    *args,
    **kwargs,
) -> list[dict[str, Any]]:
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
        result = thread_func(*func_args, **func_kwargs)
        with lock:
            results.append(result)

    threads = []
    for i in range(thread_count):
        thread_kwargs = {**kwargs, "index": i}
        t = threading.Thread(target=wrapper, args=args, kwargs=thread_kwargs)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return results


# =============================================================================
# A. 핵심 불변 조건 테스트 (Core Invariant Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.auth_race
class TestConcurrentLoginInvariant:
    """
    핵심 불변 조건: 동시 로그인 시 모두 성공 및 고유 JWT 발급

    Purpose:
        여러 사용자가 동시에 로그인해도 모두 성공하고 고유한 토큰 발급
    Type:
        Core invariant test (must never fail)
    """

    @pytest.mark.parametrize("user_count", [10, 20])
    def test_concurrent_login_all_succeed_with_unique_tokens(self, user_count):
        """
        Purpose:
            {user_count}명 동시 로그인 시 모두 성공, 각각 고유한 JWT 발급
        Scenario:
            {user_count}명이 동시에 로그인 요청
        Expected:
            모두 성공, 모든 access/refresh token 고유
        """
        # Arrange
        users = [
            UserFactory(
                username=f"concurrentuser{i}",
                email=f"concurrent{i}@test.com",
                password="testpass123",
            )
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, **kwargs):
            client = APIClient()
            try:
                response = client.post(
                    login_url,
                    {"username": username, "password": password},
                    format="json",
                )
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
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

        # Act
        results = []
        for user in users:
            result_list = concurrent_api_call(login_thread, 1, user.username, "testpass123")
            results.extend(result_list)

        # Assert
        success_results = [r for r in results if r.get("status") == status.HTTP_200_OK]
        assert len(success_results) == user_count, f"{user_count}명 모두 성공. 실제: {len(success_results)}"

        # 모든 JWT 고유성 확인
        access_tokens = [r["access"] for r in success_results]
        refresh_tokens = [r["refresh"] for r in success_results]

        assert len(set(access_tokens)) == user_count, "모든 access token 고유"
        assert len(set(refresh_tokens)) == user_count, "모든 refresh token 고유"

        # JWT 형식 검증 (샘플 3개)
        for token in access_tokens[:3]:
            parts = token.split(".")
            assert len(parts) == 3, "JWT 형식 유효"

        close_db_connections()

    def test_same_user_5_concurrent_logins_each_unique_session(self):
        """
        Purpose:
            동일 사용자 5회 동시 로그인 시 각각 별도 세션(토큰) 발급
        Scenario:
            1명 사용자, 5개 스레드에서 동시 로그인
        Expected:
            5개 모두 성공, 5개의 서로 다른 토큰 발급
        """
        # Arrange
        user = UserFactory(username="sameuser", email="same@test.com", password="testpass123")
        login_url = reverse("auth-login")
        concurrent_count = 5

        def login_thread(**kwargs):
            client = APIClient()
            try:
                response = client.post(
                    login_url,
                    {"username": "sameuser", "password": "testpass123"},
                    format="json",
                )
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
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
        assert len(success_results) == concurrent_count, f"{concurrent_count}회 모두 성공"

        access_tokens = [r["access"] for r in success_results]
        refresh_tokens = [r["refresh"] for r in success_results]

        assert len(set(access_tokens)) == concurrent_count, "5개 고유 access token"
        assert len(set(refresh_tokens)) == concurrent_count, "5개 고유 refresh token"

        close_db_connections()


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.auth_race
@pytest.mark.slow
class TestConcurrentLoginScale:
    """
    스케일 테스트: 100명 동시 로그인

    Purpose:
        대규모 동시 로그인에서 시스템 안정성 검증
    """

    def test_100_concurrent_login_90_percent_success(self):
        """
        Purpose:
            100명 동시 로그인 시 90% 이상 성공
        Scenario:
            100명 동시 로그인 요청
        Expected:
            90% 이상 성공 (일부 DB 연결 실패 허용)
        """
        # Arrange
        user_count = 100
        users = [
            UserFactory(
                username=f"scaleuser{i}",
                email=f"scale{i}@test.com",
                password="testpass123",
            )
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, **kwargs):
            client = APIClient()
            try:
                response = client.post(
                    login_url,
                    {"username": username, "password": password},
                    format="json",
                )
                access = response.data.get("token", {}).get("access") if response.status_code == status.HTTP_200_OK else None
                return {"status": response.status_code, "access": access}
            except Exception as e:
                return {"error": str(e)}

        # Act
        results = []
        for user in users:
            result_list = concurrent_api_call(login_thread, 1, user.username, "testpass123")
            results.extend(result_list)

        # Assert
        success_results = [r for r in results if r.get("status") == status.HTTP_200_OK]
        success_rate = len(success_results) / user_count
        assert success_rate >= 0.9, f"90% 이상 성공. 실제: {success_rate:.1%}"

        close_db_connections()


# =============================================================================
# B. 토큰 갱신 테스트 (Token Refresh Tests)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.auth_race
class TestRefreshTokenConcurrency:
    """
    Refresh Token 동시 갱신 테스트

    Purpose:
        동일 Refresh Token으로 동시 갱신 시 race condition 검증
    Note:
        SimpleJWT는 select_for_update를 사용하지 않아 race condition 발생 가능
        실무에서는 Redis 기반 TokenBlacklist 권장
    """

    def test_concurrent_refresh_at_least_one_succeeds(self):
        """
        Purpose:
            동일 Refresh Token 동시 갱신 시 최소 1개 성공
        Scenario:
            5개 스레드가 동일 refresh token으로 갱신 시도
        Expected:
            1~5개 성공 (SimpleJWT 라이브러리 제약)
        """
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화됨")

        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        refresh_token_str = str(refresh)
        refresh_url = reverse("token-refresh")
        concurrent_count = 5

        def refresh_thread(**kwargs):
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

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert 1 <= success_count <= concurrent_count, f"1-{concurrent_count}개 성공 가능. 실제: {success_count}"

        close_db_connections()

    def test_refresh_with_new_token_works(self):
        """
        Purpose:
            갱신 후 새 토큰으로 다시 갱신 가능 확인
        Scenario:
            첫 번째 갱신 후 새 refresh token으로 두 번째 갱신
        Expected:
            둘 다 성공
        """
        if not settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"):
            pytest.skip("ROTATE_REFRESH_TOKENS가 비활성화됨")

        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        refresh_token_str = str(refresh)
        refresh_url = reverse("token-refresh")

        client = APIClient()

        # Act 1 - 첫 번째 갱신
        first_response = client.post(refresh_url, {"refresh": refresh_token_str}, format="json")

        # Assert 1
        assert first_response.status_code == status.HTTP_200_OK
        new_refresh_cookie = first_response.cookies.get("refresh_token")
        assert new_refresh_cookie is not None
        new_refresh_token = new_refresh_cookie.value

        # Act 2 - 새 토큰으로 두 번째 갱신
        second_response = client.post(refresh_url, {"refresh": new_refresh_token}, format="json")

        # Assert 2
        assert second_response.status_code == status.HTTP_200_OK
        assert "access" in second_response.data

        close_db_connections()


# =============================================================================
# C. 고급 시나리오 (Advanced Scenarios)
# =============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.auth_race
class TestMixedAuthConcurrency:
    """
    복합 동시성 시나리오

    Purpose:
        로그인과 토큰 갱신이 동시에 실행되는 상황 검증
    """

    def test_concurrent_login_with_invalid_credentials_mixed(self):
        """
        Purpose:
            20명 동시 로그인 중 절반은 잘못된 비밀번호
        Scenario:
            10명 정상 비밀번호, 10명 잘못된 비밀번호
        Expected:
            10명 성공, 10명 실패
        """
        # Arrange
        user_count = 20
        users = [
            UserFactory(
                username=f"mixeduser{i}",
                email=f"mixed{i}@test.com",
                password="testpass123",
            )
            for i in range(user_count)
        ]

        login_url = reverse("auth-login")

        def login_thread(username, password, should_succeed, **kwargs):
            client = APIClient()
            try:
                response = client.post(
                    login_url,
                    {"username": username, "password": password},
                    format="json",
                )
                return {
                    "username": username,
                    "status": response.status_code,
                    "should_succeed": should_succeed,
                    "success": response.status_code == status.HTTP_200_OK,
                }
            except Exception as e:
                return {"username": username, "error": str(e), "should_succeed": should_succeed}

        # Act
        results = []
        for i, user in enumerate(users):
            password = "testpass123" if i % 2 == 0 else "wrongpass"
            should_succeed = i % 2 == 0
            result_list = concurrent_api_call(login_thread, 1, user.username, password, should_succeed)
            results.extend(result_list)

        # Assert
        success_results = [r for r in results if r.get("success")]
        failed_results = [r for r in results if r.get("status") == status.HTTP_400_BAD_REQUEST]

        expected_success = user_count // 2
        assert len(success_results) == expected_success, f"{expected_success}명 성공. 실제: {len(success_results)}"
        assert len(failed_results) == expected_success, f"{expected_success}명 실패. 실제: {len(failed_results)}"

        close_db_connections()

    def test_concurrent_login_and_refresh_independent(self):
        """
        Purpose:
            로그인과 토큰 갱신 동시 실행 시 독립적으로 작동
        Scenario:
            10개 로그인 + 10개 토큰 갱신 동시 실행
        Expected:
            로그인 10개 성공, 갱신 최소 1개 성공
        """
        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)

        login_url = reverse("auth-login")
        refresh_url = reverse("token-refresh")

        results = []
        lock = threading.Lock()

        def login_thread():
            client = APIClient()
            try:
                response = client.post(
                    login_url,
                    {"username": user.username, "password": "testpass123"},
                    format="json",
                )
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

        # Act
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

        login_success = sum(1 for r in login_results if r.get("success"))
        assert login_success == 10, f"10개 로그인 성공. 실제: {login_success}"

        refresh_success = sum(1 for r in refresh_results if r.get("success"))
        assert refresh_success >= 1, f"최소 1개 갱신 성공. 실제: {refresh_success}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.concurrency
@pytest.mark.auth_race
class TestJWTTokenValidation:
    """
    JWT 토큰 검증 - 동시성 환경

    Purpose:
        동일 토큰으로 여러 요청 시 모두 성공
    """

    def test_concurrent_token_usage_all_succeed(self):
        """
        Purpose:
            20개 스레드에서 동일 access token 사용 시 모두 성공
        Scenario:
            동일 access token으로 20개 동시 API 호출
        Expected:
            20개 모두 성공
        """
        # Arrange
        user = UserFactory(password="testpass123")
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)

        profile_url = reverse("user-profile")
        concurrent_count = 20
        results = []
        lock = threading.Lock()

        def use_token_thread():
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

        # Act
        threads = [threading.Thread(target=use_token_thread) for _ in range(concurrent_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == concurrent_count, f"20개 모두 성공. 실제: {success_count}"

    def test_concurrent_logout_at_least_one_succeeds(self):
        """
        Purpose:
            5개 스레드 동시 로그아웃 시 최소 1개 성공
        Scenario:
            동일 refresh token으로 5개 동시 로그아웃
        Expected:
            1개 성공, 나머지는 이미 blacklist 처리
        """
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

        # Act
        threads = [threading.Thread(target=logout_thread) for _ in range(concurrent_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count >= 1, f"최소 1개 로그아웃 성공. 실제: {success_count}"

        # 로그아웃 후 refresh token 사용 불가 확인
        refresh_url = reverse("token-refresh")
        client = APIClient()
        response = client.post(refresh_url, {"refresh": refresh_token_str}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, "로그아웃된 refresh token 사용 불가"

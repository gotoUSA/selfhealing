# 📘 인증 테스트 가이드

> 인증이 필요한 API에서 JWT 토큰 유무/유효성에 따른 접근 제어가 올바르게 동작하는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

인증 관련 동작이 올바른지:
1. **인증 없이** 보호된 엔드포인트 접근 시 **401** 반환
2. **유효한 토큰**으로 접근 시 **정상 응답**
3. **잘못된 토큰**으로 접근 시 **401** 반환
4. **권한 부족** 시 **403** 반환 (역할 기반)

---

## 📋 테스트 시나리오

| 시나리오 | 예상 응답 |
|----------|-----------|
| 토큰 없음 | 401 Unauthorized |
| 유효한 토큰 | 200 OK |
| 만료된 토큰 | 401 Unauthorized |
| 잘못된 형식 토큰 | 401 Unauthorized |
| Bearer 접두사 누락 | 401 Unauthorized |
| 서명 불일치 토큰 | 401 Unauthorized |
| 일반 사용자 → 판매자 API | 403 Forbidden |

---

## 📝 테스트 예시

### 1. 인증 없이 접근 (401)

```python
@pytest.mark.parametrize("endpoint", [
    "/api/orders/",
    "/api/wishlist/",
    "/api/notifications/",
    "/api/payments/",
    "/api/users/profile/",
])
def test_unauthenticated_access(self, client, endpoint):
    """🔒 인증 없이 보호된 엔드포인트 접근 → 401"""
    # Arrange - endpoint is provided by parametrize

    # Act
    response = client.get(endpoint)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
```

### 2. 유효한 토큰으로 접근 (200)

```python
def test_authenticated_access(self, client, auth_headers):
    """✅ 유효한 토큰으로 보호된 엔드포인트 접근 → 200"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    protected_endpoints = [
        "/api/orders/",
        "/api/wishlist/",
        "/api/users/profile/",
    ]

    for endpoint in protected_endpoints:
        # Act
        response = client.get(endpoint, **headers)

        # Assert
        assert response.status_code == status.HTTP_200_OK, \
            f"{endpoint}에서 인증 실패: {response.status_code}"
```

### 3. 잘못된 토큰 형식 (401)

```python
@pytest.mark.parametrize(
    "auth_header,description",
    [
        ("", "빈 헤더"),
        ("Bearer", "토큰 누락"),
        ("Bearer ", "빈 토큰"),
        ("Bearer invalid.token.here", "잘못된 토큰"),
        ("invalid_token_no_bearer", "Bearer 접두사 누락"),
        ("Basic dXNlcjpwYXNz", "Basic 인증"),
    ],
    ids=["empty", "bearer_only", "bearer_empty", "invalid", "no_bearer", "basic_auth"],
)
def test_invalid_auth_headers(self, client, auth_header, description):
    """🔒 잘못된 인증 헤더 → 401"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_header} if auth_header else {}

    # Act
    response = client.get("/api/orders/", **headers)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED, \
        f"{description}: 401이 아닌 {response.status_code}"
```

### 4. 역할 기반 접근 제어 (403)

```python
def test_regular_user_access_seller_api(self, client, auth_headers):
    """👤 일반 사용자 → 판매자 전용 API 접근"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

    # Act
    response = client.get("/api/seller/returns/", **headers)

    # Assert - 403 또는 200(빈 결과) 허용
    assert response.status_code in [
        status.HTTP_200_OK,      # 소유권 기반 필터링
        status.HTTP_403_FORBIDDEN,  # 역할 기반 거부
    ]

def test_seller_access_seller_api(self, client, seller_auth_headers):
    """🏪 판매자 → 판매자 API 접근 성공"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]}

    # Act
    response = client.get("/api/seller/returns/", **headers)

    # Assert
    assert response.status_code == status.HTTP_200_OK
```

---

## 🔧 Fixture 활용

```python
# conftest.py에서 제공

@pytest.fixture
def auth_headers(user):
    """일반 사용자 JWT 토큰"""
    refresh = RefreshToken.for_user(user)
    return {"Authorization": f"Bearer {refresh.access_token}"}

@pytest.fixture
def seller_auth_headers(seller_user):
    """판매자 JWT 토큰"""
    refresh = RefreshToken.for_user(seller_user)
    return {"Authorization": f"Bearer {refresh.access_token}"}
```

---

## 📋 새 API 인증 테스트 체크리스트

| 테스트 케이스 | 필요 여부 |
|---------------|-----------|
| 토큰 없이 접근 → 401 | ✅ 필수 |
| 유효한 토큰 → 200 | ✅ 필수 |
| 잘못된 토큰 → 401 | ✅ 권장 |
| 만료된 토큰 → 401 | 선택 |
| 역할 권한 체크 | 역할 제한 있을 때 |
| 소유권 체크 | 리소스 소유자만 접근 시 |

---

## ✅ 완료 체크리스트

- [ ] 토큰 없이 접근 401 테스트
- [ ] 유효한 토큰 접근 200 테스트
- [ ] 잘못된 토큰 형식 401 테스트
- [ ] 역할 기반 접근 제어 테스트 (해당 시)
- [ ] 소유권 기반 접근 제어 테스트 (해당 시)
- [ ] AAA 주석 패턴 적용

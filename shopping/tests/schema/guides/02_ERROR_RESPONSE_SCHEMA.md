# 📘 에러 응답 스키마 검증 가이드

> 새 API 추가 시 실패 응답(4xx, 5xx)이 일관된 에러 스키마를 따르는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

API가 실패할 때 반환하는 응답이:
1. **올바른 HTTP 상태 코드**를 반환하는지 (400, 401, 403, 404 등)
2. **일관된 에러 구조**를 갖추는지
3. **명확한 에러 메시지**를 포함하는지
4. **민감 정보를 노출하지 않는지** (스택 트레이스 등)

---

## 📋 에러 코드별 검증 항목

| 상태 코드 | 의미 | 발생 상황 |
|-----------|------|-----------|
| 400 Bad Request | 잘못된 요청 | 필수 필드 누락, 잘못된 타입, 유효성 검사 실패 |
| 401 Unauthorized | 인증 필요 | 토큰 없음, 토큰 만료, 잘못된 토큰 |
| 403 Forbidden | 권한 없음 | 타인 리소스 접근, 역할 권한 부족 |
| 404 Not Found | 리소스 없음 | 존재하지 않는 ID, 비활성 리소스 |
| 409 Conflict | 충돌 | 중복 생성, 동시성 충돌 |
| 422 Unprocessable Entity | 처리 불가 | 비즈니스 규칙 위반 |

---

## 🔧 에러 응답 표준 구조

```python
# DRF 기본 에러 응답 형식들
{
    "detail": "에러 메시지"                    # 단일 에러
}

{
    "field_name": ["에러 메시지1", "에러 메시지2"]  # 필드별 에러
}

{
    "non_field_errors": ["에러 메시지"]         # 필드 외 에러
}

# 커스텀 에러 응답 (프로젝트에 따라 다름)
{
    "error": "에러 코드",
    "message": "에러 메시지",
    "errors": {...}
}
```

---

## 📝 테스트 시나리오별 예시

### 1. 필수 필드 누락 (400)

```python
def test_cart_add_missing_fields(self, client, auth_headers):
    """🛒 장바구니 추가 실패 - 필수 필드 누락"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    test_cases = [
        ({}, "빈 요청"),
        ({"product_id": 1}, "quantity 누락"),
        ({"quantity": 1}, "product_id 누락"),
    ]

    for data, description in test_cases:
        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST, \
            f"{description}: 400이 아닌 {response.status_code}"

        error_data = response.json()
        assert_error_response(error_data, context=f"/api/cart/add_item/ ({description})")
```

### 2. 인증 필요 (401)

```python
@pytest.mark.parametrize("endpoint", [
    "/api/orders/",
    "/api/wishlist/",
    "/api/notifications/",
])
def test_unauthenticated_access_401(self, client, endpoint):
    """🔒 인증 없이 보호된 엔드포인트 접근 → 401"""
    # Arrange - endpoint is provided by parametrize

    # Act
    response = client.get(endpoint)

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED, \
        f"{endpoint}: 401이 아닌 {response.status_code}"

    error_data = response.json()
    assert_error_response(error_data, context=f"{endpoint} (unauthenticated)")
```

### 3. 권한 없음 (403)

```python
def test_access_other_user_order(self, client, auth_headers, other_user_order):
    """🔒 타인의 주문 접근 시도 → 403 or 404"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

    # Act
    response = client.get(f"/api/orders/{other_user_order.id}/", **headers)

    # Assert - 403 또는 404 (보안상 숨김)
    assert response.status_code in [
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    ]

    error_data = response.json()
    assert_error_response(error_data, context="타인 주문 접근")
```

### 4. 리소스 없음 (404)

```python
def test_product_detail_nonexistent_id(self, client):
    """❌ 존재하지 않는 상품 ID → 404"""
    # Arrange
    invalid_id = 99999999

    # Act
    response = client.get(f"/api/products/{invalid_id}/")

    # Assert
    assert response.status_code == status.HTTP_404_NOT_FOUND

    data = response.json()
    assert_error_response(data, context=f"/api/products/{invalid_id}/")
```

### 5. 비즈니스 규칙 위반 (400)

```python
def test_cart_add_out_of_stock(self, client, auth_headers, out_of_stock_product):
    """🛒 재고 없는 상품 장바구니 추가 → 400"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    data = {"product_id": out_of_stock_product.id, "quantity": 1}

    # Act
    response = client.post(
        "/api/cart/add_item/",
        data=json.dumps(data),
        content_type="application/json",
        **headers,
    )

    # Assert
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    error_data = response.json()
    assert_error_response(error_data, context="/api/cart/add_item/ (out of stock)")
```

---

## 🔧 에러 응답 검증 헬퍼 함수

```python
# conftest.py에 추가

def assert_error_response(data: dict, context: str = ""):
    """
    에러 응답 스키마 검증

    DRF의 다양한 에러 응답 형식을 모두 허용합니다.

    Args:
        data: 에러 응답 데이터
        context: 에러 메시지에 표시할 컨텍스트
    """
    assert isinstance(data, dict), f"{context}: 에러 응답이 dict가 아님"
    assert len(data) > 0, f"{context}: 에러 응답이 비어있음"

    # DRF 표준 에러 필드들 중 하나는 있어야 함
    valid_error_keys = [
        "detail",           # 단일 에러 메시지
        "non_field_errors", # 필드 외 에러
        "error",            # 커스텀 에러 코드
        "message",          # 커스텀 메시지
        "errors",           # 중첩 에러
    ]

    # 필드별 에러도 허용 (username, password, product_id 등)
    has_error_info = (
        any(key in data for key in valid_error_keys) or
        any(isinstance(v, list) for v in data.values())  # 필드별 에러 리스트
    )

    assert has_error_info, f"{context}: 에러 정보 없음. 응답: {data}"

    # 민감 정보 미노출 확인
    response_str = str(data).lower()
    assert "traceback" not in response_str, f"{context}: 스택 트레이스 노출!"
    assert "exception" not in response_str or "message" in response_str, \
        f"{context}: 예외 정보 노출 의심"
```

---

## 📋 새 API 에러 테스트 작성 체크리스트

### 필수 테스트 케이스

| 시나리오 | 예상 코드 | 테스트 필요 |
|----------|-----------|-------------|
| 필수 필드 누락 | 400 | ✅ |
| 잘못된 데이터 타입 | 400 | ✅ |
| 인증 토큰 없음 | 401 | ✅ (인증 필요 API) |
| 권한 없음 | 403 | ✅ (권한 체크 있을 때) |
| 리소스 없음 | 404 | ✅ (ID 기반 조회) |

### 비즈니스 로직별 추가 케이스

- [ ] 재고 부족
- [ ] 중복 생성 시도
- [ ] 상태 전이 불가 (예: 배송 완료 → 취소)
- [ ] 수량/금액 제한 초과
- [ ] 기간 만료

---

## ⚠️ 주의사항

### 1. 403 vs 404 선택
```python
# 보안상 404 권장 (리소스 존재 여부 숨김)
# 하지만 둘 다 허용하는 것이 현실적
assert response.status_code in [403, 404]
```

### 2. 에러 메시지 언어
```python
# 한글/영어 메시지 모두 허용
# 내용 검증보다는 구조 검증에 집중
```

### 3. 필드별 에러 검증
```python
# 특정 필드 에러가 있는지 확인할 때
if response.status_code == 400:
    errors = response.json()
    assert "username" in errors or "detail" in errors
```

---

## ✅ 완료 체크리스트

- [ ] 400 에러 케이스 테스트 추가 (필수 필드, 타입)
- [ ] 401 에러 케이스 테스트 추가 (인증 필요 시)
- [ ] 403/404 에러 케이스 테스트 추가 (권한/존재 체크)
- [ ] 비즈니스 규칙 위반 케이스 테스트 추가
- [ ] assert_error_response 헬퍼 사용
- [ ] 민감 정보 미노출 확인
- [ ] AAA 주석 패턴 적용

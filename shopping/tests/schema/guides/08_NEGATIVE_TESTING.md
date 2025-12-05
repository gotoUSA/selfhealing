# 📘 Negative 테스트 가이드

> 의도적으로 잘못된/악의적인 입력을 보내 애플리케이션의 에러 처리가 올바르게 동작하는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

1. **유효성 검증** 로직이 제대로 동작하는지 확인
2. **보안 취약점** (SQL Injection, XSS 등) 방어 검증
3. **에러 응답**이 일관되고 친화적인지 확인
4. **적절한 상태 코드** 반환 검증

---

## 📋 Negative vs Positive 테스트

| 구분 | Positive | Negative |
|------|----------|----------|
| 목적 | 정상 동작 검증 | 에러 처리 검증 |
| 입력 | 유효한 값 | 잘못된/악의적 값 |
| 기대 | 200/201 성공 | 400/401/403/404 등 |
| 예시 | 유효한 이메일 | "not-an-email", "", null |

---

## 🔧 Negative 테스트 카테고리

### 1️⃣ 형식 오류

| 입력 유형 | 잘못된 예시 |
|-----------|-------------|
| 숫자 필드 | "abc", null, "", float |
| 이메일 | "no-at-sign", "@only.com" |
| 날짜 | "2024-13-45", "yesterday" |
| URL | "not-a-url", "ftp://wrong" |
| UUID | "not-uuid", "123" |

### 2️⃣ 경계값 오류

| 조건 | 테스트 값 |
|------|-----------|
| 필수 필드 | null, 누락 |
| 최소값 | -1 (수량), 0 (가격) |
| 최대값 | 99999999, 1000자 문자열 |
| 빈 값 | "", [], {} |

### 3️⃣ 악의적 입력

```python
MALICIOUS_INPUTS = [
    "'; DROP TABLE users; --",  # SQL Injection
    "<script>alert(1)</script>", # XSS
    "{{7*7}}",                   # SSTI
    "../../../etc/passwd",       # Path Traversal
    "a" * 100000,                # 버퍼 오버플로우
    "%00%0D%0A",                 # Null/CRLF
]
```

---

## 📝 테스트 예시

### 1. 필수 필드 누락

```python
@pytest.mark.negative
@pytest.mark.django_db(transaction=True)
class TestOrderNegative:
    """📦 주문 API Negative 테스트"""

    @pytest.mark.parametrize("missing_field", [
        "shipping_address",
        "payment_method",
    ])
    def test_create_order_missing_required_field(
        self, client, auth_headers, missing_field
    ):
        """필수 필드 누락 시 400 에러"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": "서울시...",
            "payment_method": "card",
        }
        del data[missing_field]

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == 400
        response_data = response.json()
        assert missing_field in str(response_data)
```

### 2. 잘못된 형식

```python
@pytest.mark.parametrize("invalid_email", [
    "not-an-email",
    "@no-local.com",
    "no-domain@",
    "spaces in@email.com",
    "",
    "a" * 255 + "@test.com",
])
def test_register_invalid_email(self, client, invalid_email):
    """이메일 형식 검증"""
    # Arrange
    data = {
        "username": "testuser",
        "email": invalid_email,
        "password": "ValidPass123!",
    }

    # Act
    response = client.post(
        "/api/auth/register/",
        data=json.dumps(data),
        content_type="application/json",
    )

    # Assert
    assert response.status_code == 400
    assert "email" in response.json()
```

### 3. 경계값 오류

```python
@pytest.mark.parametrize("invalid_quantity,expected_status", [
    (-1, 400),          # 음수
    (0, 400),           # 0
    (100001, 400),      # 최대 초과
    (1.5, 400),         # 소수
    ("abc", 400),       # 문자열
    (None, 400),        # null
])
def test_cart_invalid_quantity(
    self, client, auth_headers, schema_test_product,
    invalid_quantity, expected_status
):
    """수량 유효성 검증"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    data = {
        "product_id": schema_test_product.id,
        "quantity": invalid_quantity,
    }

    # Act
    response = client.post(
        "/api/cart/add_item/",
        data=json.dumps(data),
        content_type="application/json",
        **headers,
    )

    # Assert
    assert response.status_code == expected_status
```

### 4. 악의적 입력 (보안)

```python
@pytest.mark.parametrize("malicious_input", [
    "'; DROP TABLE products; --",
    "<script>alert('xss')</script>",
    "{{7*7}}",
    "../../../etc/passwd",
])
def test_search_malicious_input(self, client, malicious_input):
    """악의적 입력 방어"""
    # Act
    response = client.get("/api/products/", {"search": malicious_input})

    # Assert - 5xx 없이 정상 처리
    assert response.status_code < 500
    assert response.status_code in [200, 400]

    # 입력이 이스케이프되거나 필터링됨
    if response.status_code == 200:
        data = response.json()
        assert "DROP TABLE" not in str(data)
        assert "<script>" not in str(data)
```

### 5. 존재하지 않는 리소스

```python
@pytest.mark.parametrize("invalid_id", [
    99999999,  # 존재하지 않는 ID
    -1,        # 음수 ID
    0,         # 0
])
def test_product_not_found(self, client, invalid_id):
    """존재하지 않는 상품 조회"""
    # Act
    response = client.get(f"/api/products/{invalid_id}/")

    # Assert
    assert response.status_code == 404
    assert "detail" in response.json()
```

---

## ⚙️ 공통 헬퍼 함수

```python
def assert_validation_error(response, field: str | None = None):
    """400 응답과 필드 에러 검증"""
    assert response.status_code == 400
    data = response.json()

    if field:
        assert field in data or field in str(data)

    # 에러 구조 검증
    assert isinstance(data, dict)


def assert_not_found(response):
    """404 응답 검증"""
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
```

---

## 📋 새 API Negative 테스트 체크리스트

### 입력 검증
- [ ] 필수 필드 누락 → 400
- [ ] 잘못된 형식 (이메일, 날짜 등) → 400
- [ ] 경계값 위반 (음수, 최대 초과) → 400
- [ ] null/빈 문자열 처리

### 리소스 검증
- [ ] 존재하지 않는 ID → 404
- [ ] 삭제된 리소스 접근 → 404

### 권한 검증
- [ ] 다른 사용자 리소스 접근 → 403
- [ ] 미인증 접근 → 401

### 보안 검증
- [ ] SQL Injection 입력 → 5xx 없음
- [ ] XSS 스크립트 입력 → 이스케이프됨
- [ ] Path Traversal 입력 → 거부됨

---

## ⚠️ 주의사항

1. **5xx는 항상 버그** - Negative 테스트에서도 5xx가 나오면 안 됨
2. **에러 메시지 검증** - 친화적이고 정보 유출 없어야 함
3. **일관성** - 같은 유형 에러는 같은 응답 형식

---

## ✅ 완료 체크리스트

- [ ] 필수 필드 누락 테스트
- [ ] 형식 오류 테스트
- [ ] 경계값 오류 테스트
- [ ] 악의적 입력 테스트
- [ ] 404 시나리오 테스트
- [ ] `@pytest.mark.negative` 마커 추가
- [ ] AAA 주석 패턴 적용

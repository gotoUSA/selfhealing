# 📘 Path Parameter 검증 가이드

> URL 경로 파라미터(`/api/products/{id}/`)에 다양한 값을 주입하여 적절히 처리되는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

Path Parameter에 다양한 형태의 값이 들어올 때:
1. **유효한 ID**는 정상 처리되는지
2. **존재하지 않는 ID**는 404를 반환하는지
3. **잘못된 형식의 ID**는 적절히 거부되는지
4. **악성 입력**(SQL Injection 등)에 5xx 에러 없이 처리되는지

---

## 📋 테스트할 입력 유형

| 유형 | 예시 | 예상 응답 |
|------|------|-----------|
| 유효한 ID | `1`, `123` | 200 OK |
| 존재하지 않는 ID | `99999999` | 404 Not Found |
| 음수 ID | `-1` | 400 or 404 |
| 0 | `0` | 400 or 404 |
| 문자열 ID | `abc` | 400 or 404 |
| float ID | `1.5` | 400 or 404 |
| null 문자열 | `null` | 400 or 404 |
| SQL Injection | `1; DROP TABLE--` | 400 or 404 (5xx 절대 X) |
| Path Traversal | `../../../etc/passwd` | 400 or 404 |

---

## 📝 테스트 예시

### 1. 기본 테스트 (유효/무효 ID)

```python
@pytest.mark.schema
@pytest.mark.django_db
class TestProductPathParameters:
    """🔗 상품 API Path Parameter 테스트"""

    def test_valid_id(self, client, schema_test_product):
        """✅ 유효한 상품 ID → 200"""
        # Arrange
        product_id = schema_test_product.id

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == product_id

    def test_nonexistent_id(self, client):
        """❌ 존재하지 않는 상품 ID → 404"""
        # Arrange
        invalid_id = 99999999

        # Act
        response = client.get(f"/api/products/{invalid_id}/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
```

### 2. Parametrize로 여러 케이스 한번에

```python
@pytest.mark.parametrize(
    "invalid_id,description",
    [
        ("-1", "음수 ID"),
        ("0", "0 ID"),
        ("abc", "문자열 ID"),
        ("1.5", "float ID"),
        ("null", "null 문자열"),
        ("1; DROP TABLE--", "SQL Injection"),
        ("../../../etc/passwd", "Path Traversal"),
    ],
    ids=["negative", "zero", "string", "float", "null_str", "sql_injection", "path_traversal"],
)
def test_invalid_id_format(self, client, invalid_id, description):
    """❌ 잘못된 ID 형식 → 400 or 404 (5xx 절대 X)"""
    # Arrange - invalid_id is provided by parametrize

    # Act
    response = client.get(f"/api/products/{invalid_id}/")

    # Assert - 핵심: 5xx 에러 없음
    assert response.status_code < 500, \
        f"{description}에서 서버 에러 발생: {response.status_code}"

    # Assert - 400 또는 404
    assert response.status_code in [
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    ], f"{description}: 예상치 못한 응답 {response.status_code}"
```

### 3. 인증 필요 리소스

```python
def test_order_detail_unauthorized(self, client, schema_test_order):
    """🔒 인증 없이 주문 상세 접근 → 401"""
    # Arrange
    order_id = schema_test_order.id

    # Act
    response = client.get(f"/api/orders/{order_id}/")

    # Assert
    assert response.status_code == status.HTTP_401_UNAUTHORIZED

def test_order_detail_authorized(self, client, auth_headers, schema_test_order):
    """✅ 인증 후 주문 상세 접근 → 200"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    order_id = schema_test_order.id

    # Act
    response = client.get(f"/api/orders/{order_id}/", **headers)

    # Assert
    assert response.status_code == status.HTTP_200_OK
```

---

## 🔧 재사용 가능한 테스트 패턴

```python
# conftest.py에 추가 가능

INVALID_ID_TEST_CASES = [
    ("-1", "음수 ID"),
    ("0", "0 ID"),
    ("abc", "문자열 ID"),
    ("1.5", "float ID"),
    ("null", "null 문자열"),
    ("1; DROP TABLE--", "SQL Injection"),
]

def assert_invalid_id_rejected(response, description: str):
    """잘못된 ID가 적절히 거부되었는지 검증"""
    assert response.status_code < 500, \
        f"{description}에서 서버 에러: {response.status_code}"
    assert response.status_code in [400, 404], \
        f"{description}: 예상치 못한 응답 {response.status_code}"
```

---

## 📋 새 API Path Parameter 테스트 체크리스트

| 테스트 케이스 | 필요 여부 |
|---------------|-----------|
| 유효한 ID → 200 | ✅ 필수 |
| 존재하지 않는 ID → 404 | ✅ 필수 |
| 음수/0 ID → 400 or 404 | ✅ 필수 |
| 문자열 ID → 400 or 404 | ✅ 필수 |
| SQL Injection → 5xx 없음 | ✅ 필수 |
| 인증 없이 접근 → 401 | 인증 필요 API만 |
| 타인 리소스 접근 → 403/404 | 소유권 체크 API만 |

---

## ✅ 완료 체크리스트

- [ ] 유효한 ID 성공 테스트
- [ ] 존재하지 않는 ID 404 테스트
- [ ] 잘못된 형식 ID 테스트 (parametrize 활용)
- [ ] SQL Injection 등 악성 입력 테스트
- [ ] 인증 필요 시 401 테스트
- [ ] 소유권 체크 시 403/404 테스트
- [ ] AAA 주석 패턴 적용

# 📘 성공 응답 스키마 검증 가이드

> 새 API 추가 시 성공 응답(2xx)이 예상된 스키마와 일치하는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

API가 정상 동작할 때 반환하는 응답이:
1. **올바른 HTTP 상태 코드**를 반환하는지 (200, 201, 204 등)
2. **예상된 데이터 구조**를 갖추는지 (dict, list, paginated)
3. **필수 필드**가 모두 존재하는지
4. **필드 타입**이 올바른지 (string, int, bool, array 등)

---

## 📋 검증 항목 체크리스트

### 1. HTTP 상태 코드
| 메서드 | 성공 시 상태 코드 |
|--------|------------------|
| GET (조회) | 200 OK |
| POST (생성) | 201 Created 또는 200 OK |
| PUT/PATCH (수정) | 200 OK 또는 204 No Content |
| DELETE (삭제) | 204 No Content 또는 200 OK |

### 2. 응답 구조 타입
| 유형 | 구조 | 예시 |
|------|------|------|
| 단일 객체 | `dict` | 상품 상세, 사용자 프로필 |
| 목록 (단순) | `list` | 카테고리 트리, 인기 상품 |
| 목록 (페이지네이션) | `{"count": N, "results": [...]}` | 상품 목록, 주문 목록 |

### 3. 필수 필드 정의
```python
# 예시: 상품 응답 필수 필드
PRODUCT_REQUIRED_FIELDS = ["id", "name", "price"]

# 예시: 주문 응답 필수 필드
ORDER_REQUIRED_FIELDS = ["id", "status", "total_amount", "created_at"]

# 예시: 사용자 응답 필수 필드
USER_REQUIRED_FIELDS = ["id", "username", "email"]
```

### 4. 필드 타입 정의
```python
# 예시: 상품 필드 타입
PRODUCT_FIELD_TYPES = {
    "id": int,
    "name": str,
    "price": (int, float, str),  # Decimal은 str로 직렬화될 수 있음
    "stock": int,
    "is_active": bool,
    "created_at": str,  # ISO 8601 문자열
    "category": (dict, int, type(None)),  # 중첩 객체 또는 ID
}
```

---

## 🔧 테스트 작성 단계

### Step 1: 테스트 파일 위치 결정

```
shopping/tests/schema/contract/
├── test_public_endpoints.py      # 인증 불필요 API
├── test_authenticated_endpoints.py # 인증 필요 API
└── test_post_endpoints.py        # POST/PUT/PATCH/DELETE API
```

### Step 2: 필요한 fixture 확인

```python
# conftest.py에서 제공하는 fixture들
@pytest.fixture
def client():                    # Django Test Client
@pytest.fixture
def auth_headers():              # JWT 인증 헤더 (일반 사용자)
@pytest.fixture
def seller_auth_headers():       # JWT 인증 헤더 (판매자)
@pytest.fixture
def schema_test_product():       # 테스트용 상품
@pytest.fixture
def schema_test_order():         # 테스트용 주문
@pytest.fixture
def schema_test_cart():          # 테스트용 장바구니
```

### Step 3: 테스트 함수 작성

```python
@pytest.mark.schema
@pytest.mark.django_db
class TestNewEndpointContract:
    """
    🆕 새 엔드포인트 Contract 테스트

    [엔드포인트 설명]

    📋 검증 대상:
    - GET /api/new-endpoint/
    - GET /api/new-endpoint/{id}/

    ✅ 검증 항목:
    - 200 OK 응답
    - 응답 구조 (dict/list/paginated)
    - 필수 필드 존재 및 타입 일치
    """

    def test_new_endpoint_list_contract(self, client, auth_headers):
        """📋 새 엔드포인트 목록 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/new-endpoint/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조
        data = response.json()
        assert_list_response(data, context="/api/new-endpoint/")

        # Assert - 각 아이템 스키마 (처음 3개만)
        results = data.get("results", data)  # paginated 또는 list
        for item in results[:3]:
            assert_new_endpoint_schema(item, context="/api/new-endpoint/ item")

    def test_new_endpoint_detail_contract(self, client, auth_headers, test_data):
        """🔍 새 엔드포인트 상세 API Contract 검증"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        item_id = test_data.id

        # Act
        response = client.get(f"/api/new-endpoint/{item_id}/", **headers)

        # Assert - 상태 코드
        assert response.status_code == status.HTTP_200_OK

        # Assert - 응답 구조 및 스키마
        data = response.json()
        assert_new_endpoint_schema(data, context=f"/api/new-endpoint/{item_id}/")

        # Assert - ID 일치 확인
        assert data["id"] == item_id
```

### Step 4: 스키마 검증 헬퍼 함수 추가 (conftest.py)

```python
def assert_new_endpoint_schema(data: dict, strict: bool = False, context: str = ""):
    """
    새 엔드포인트 응답 스키마 검증

    Args:
        data: 검증할 응답 데이터
        strict: True이면 추가 필드 불허
        context: 에러 메시지에 표시할 컨텍스트

    Raises:
        SchemaValidationError: 스키마 불일치 시
    """
    required_fields = ["id", "name", "status"]  # 필수 필드 정의

    field_types = {
        "id": int,
        "name": str,
        "status": str,
        "created_at": str,
        "updated_at": str,
    }

    allowed_fields = set(field_types.keys()) | {"extra_field1", "extra_field2"}

    # 기본 검증
    assert isinstance(data, dict), f"{context}: 응답이 dict가 아님"

    # 필수 필드 검증
    for field in required_fields:
        assert field in data, f"{context}: '{field}' 필드 누락"

    # 타입 검증
    for field, expected_type in field_types.items():
        if field in data and data[field] is not None:
            assert isinstance(data[field], expected_type), \
                f"{context}: '{field}' 타입 불일치 (expected: {expected_type}, got: {type(data[field])})"

    # strict 모드: 추가 필드 불허
    if strict:
        extra_fields = set(data.keys()) - allowed_fields
        if extra_fields:
            raise SchemaValidationError(f"{context}: 예상치 못한 필드 발견: {extra_fields}")
```

---

## 📝 실제 예시

### 예시 1: 단순 조회 API (상품 상세)

```python
def test_product_detail_contract(self, client, schema_test_product):
    """🛒 상품 상세 API Contract 검증"""
    # Arrange
    product_id = schema_test_product.id

    # Act
    response = client.get(f"/api/products/{product_id}/")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert_product_schema(data, context=f"/api/products/{product_id}/")
    assert data["id"] == product_id
```

### 예시 2: 페이지네이션 목록 API (주문 목록)

```python
def test_orders_list_contract(self, client, auth_headers, schema_test_order):
    """📦 주문 목록 API Contract 검증"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

    # Act
    response = client.get("/api/orders/", **headers)

    # Assert - 상태 코드
    assert response.status_code == status.HTTP_200_OK

    # Assert - 페이지네이션 구조
    data = response.json()
    results = assert_list_response(data, context="/api/orders/")

    # Assert - 각 주문 스키마
    for order in results[:3]:
        assert_order_schema(order, context="/api/orders/ item")
```

### 예시 3: POST 성공 응답 (장바구니 추가)

```python
def test_cart_add_item_success(self, client, auth_headers, schema_test_product):
    """🛒 장바구니 상품 추가 성공"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    data = {"product_id": schema_test_product.id, "quantity": 1}

    # Act
    response = client.post(
        "/api/cart/add_item/",
        data=json.dumps(data),
        content_type="application/json",
        **headers,
    )

    # Assert - 상태 코드 (200 또는 201)
    assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

    # Assert - 응답 구조
    response_data = response.json()
    assert isinstance(response_data, dict), "응답이 dict가 아님"

    # Assert - 성공 응답에 필요한 정보 포함
    has_valid_response = any(
        key in response_data
        for key in ["message", "cart", "item", "id", "items", "product"]
    )
    assert has_valid_response, f"장바구니 추가 응답에 데이터 없음: {response_data}"
```

---

## ⚠️ 주의사항

### 1. Decimal 필드 처리
```python
# Decimal은 JSON에서 문자열로 직렬화될 수 있음
"price": (int, float, str),  # 세 가지 타입 모두 허용
```

### 2. Nullable 필드 처리
```python
# None이 허용되는 필드
if field in data and data[field] is not None:
    assert isinstance(data[field], expected_type)
```

### 3. 중첩 객체 처리
```python
# category가 객체 또는 ID일 수 있음
"category": (dict, int, type(None)),

# 중첩 객체 상세 검증이 필요한 경우
if "category" in data and isinstance(data["category"], dict):
    assert "id" in data["category"]
    assert "name" in data["category"]
```

### 4. 동적 필드 처리
```python
# 요청에 따라 포함 여부가 달라지는 필드
optional_fields = ["reviews", "related_products", "seller_info"]
# 이런 필드는 strict 모드에서도 허용
```

---

## 🧪 테스트 실행

```bash
# 새로 추가한 테스트만 실행
pytest shopping/tests/schema/contract/test_xxx.py -v -k "test_new_endpoint"

# Contract 테스트 전체 실행
pytest shopping/tests/schema/contract/ -v -m schema

# 실패 시 상세 출력
pytest shopping/tests/schema/contract/ -v --tb=long
```

---

## ✅ 완료 체크리스트

새 API의 성공 응답 스키마 검증 테스트를 추가할 때:

- [ ] 테스트 파일 위치 결정 (public/authenticated/post)
- [ ] 필요한 fixture 확인 또는 추가
- [ ] HTTP 상태 코드 검증 추가
- [ ] 응답 구조 검증 추가 (dict/list/paginated)
- [ ] 필수 필드 목록 정의
- [ ] 필드 타입 정의
- [ ] 스키마 검증 헬퍼 함수 추가 (conftest.py)
- [ ] AAA 주석 패턴 적용
- [ ] 테스트 실행 및 통과 확인
- [ ] SCHEMA_TEST_CHECKLIST.md 업데이트

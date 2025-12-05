# 📘 Query Parameter 검증 가이드

> URL 쿼리 파라미터(`?page=1&search=test`)에 다양한 값을 주입하여 적절히 처리되는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

Query Parameter에 다양한 형태의 값이 들어올 때:
1. **정상 값**은 올바르게 동작하는지
2. **잘못된 값**은 무시되거나 적절히 거부되는지
3. **악성 입력**에 5xx 에러 없이 처리되는지
4. **경계값**이 올바르게 처리되는지

---

## 📋 주요 쿼리 파라미터 유형

| 파라미터 | 용도 | 테스트 포인트 |
|----------|------|---------------|
| `page` | 페이지 번호 | 음수, 0, 큰 수, 문자열 |
| `page_size` | 페이지 크기 | 음수, 0, 매우 큰 수 |
| `search` | 검색어 | SQL Injection, XSS, 긴 문자열 |
| `ordering` | 정렬 | 존재하지 않는 필드, SQL Injection |
| `category` | 필터링 | 존재하지 않는 값 |

---

## 📝 테스트 예시

### 1. 페이지네이션 파라미터

```python
@pytest.mark.parametrize(
    "page_value,description",
    [
        ("-1", "음수 페이지"),
        ("0", "0 페이지"),
        ("999999", "매우 큰 페이지"),
        ("abc", "문자열 페이지"),
        ("1.5", "float 페이지"),
    ],
    ids=["negative", "zero", "huge", "string", "float"],
)
def test_invalid_page_param(self, client, schema_test_product, page_value, description):
    """📄 잘못된 페이지 파라미터 처리"""
    # Arrange - page_value is provided by parametrize

    # Act
    response = client.get(f"/api/products/?page={page_value}")

    # Assert - 5xx 에러 없음
    assert response.status_code < 500, \
        f"{description}에서 서버 에러: {response.status_code}"

    # Assert - 400, 404(빈 페이지), 또는 200(무시) 허용
    assert response.status_code in [200, 400, 404]
```

### 2. 검색 파라미터 (악성 입력)

```python
@pytest.mark.parametrize(
    "search_value,description",
    [
        ("'; DROP TABLE products;--", "SQL Injection"),
        ("<script>alert('xss')</script>", "XSS 시도"),
        ("a" * 5000, "매우 긴 검색어"),
        ("🎉💀🔥", "이모지"),
        ("../../../etc/passwd", "Path Traversal"),
    ],
    ids=["sql_injection", "xss", "long_query", "emoji", "path_traversal"],
)
def test_malicious_search_input(self, client, search_value, description):
    """🔍 악성 검색어 처리"""
    # Arrange - search_value is provided by parametrize

    # Act
    response = client.get(f"/api/products/?search={search_value}")

    # Assert - 핵심: 5xx 에러 없음
    assert response.status_code < 500, \
        f"{description}에서 서버 에러: {response.status_code}"

    # Assert - 200 또는 400
    assert response.status_code in [200, 400]
```

### 3. 정렬 파라미터

```python
@pytest.mark.parametrize(
    "ordering_value,description",
    [
        ("nonexistent_field", "존재하지 않는 필드"),
        ("-nonexistent", "존재하지 않는 필드 역순"),
        ("'; DROP TABLE--", "SQL Injection"),
    ],
    ids=["nonexistent", "nonexistent_desc", "sql_injection"],
)
def test_invalid_ordering(self, client, ordering_value, description):
    """📊 잘못된 정렬 파라미터 처리"""
    # Arrange - ordering_value is provided by parametrize

    # Act
    response = client.get(f"/api/products/?ordering={ordering_value}")

    # Assert - 5xx 에러 없음
    assert response.status_code < 500, \
        f"{description}에서 서버 에러: {response.status_code}"
```

### 4. 복합 파라미터

```python
def test_combined_query_params(self, client, schema_test_product):
    """🔗 여러 쿼리 파라미터 동시 사용"""
    # Arrange - none needed

    # Act
    response = client.get("/api/products/?page=1&search=test&ordering=-price")

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, (dict, list))
```

---

## 🔧 재사용 가능한 테스트 데이터

```python
# conftest.py에 추가 가능

MALICIOUS_SEARCH_INPUTS = [
    "'; DROP TABLE products;--",
    "<script>alert('xss')</script>",
    "../../../etc/passwd",
    "%00",  # Null byte
    "a" * 5000,
]

INVALID_PAGE_VALUES = ["-1", "0", "abc", "1.5", "null"]

INVALID_ORDERING_VALUES = [
    "nonexistent_field",
    "'; DROP TABLE--",
    "price; DELETE FROM",
]
```

---

## 📋 새 API Query Parameter 테스트 체크리스트

| 파라미터 유형 | 테스트 케이스 |
|---------------|---------------|
| 페이지네이션 | 음수, 0, 큰 수, 문자열 |
| 검색 | SQL Injection, XSS, 긴 문자열, 이모지 |
| 정렬 | 없는 필드, SQL Injection |
| 필터 | 없는 값, 잘못된 타입 |
| 복합 | 여러 파라미터 동시 사용 |

---

## ✅ 완료 체크리스트

- [ ] 페이지네이션 경계값 테스트
- [ ] 검색 악성 입력 테스트
- [ ] 정렬 파라미터 테스트
- [ ] 필터 파라미터 테스트 (해당 시)
- [ ] 복합 파라미터 테스트
- [ ] 모든 케이스에서 5xx 없음 확인
- [ ] AAA 주석 패턴 적용

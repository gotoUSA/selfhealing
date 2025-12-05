# 📘 Fuzz 테스트 가이드

> Hypothesis 라이브러리를 활용하여 무작위 입력을 생성하고, 예상치 못한 에러(특히 5xx)가 발생하지 않는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

무작위로 생성된 입력에 대해:
1. **5xx 서버 에러**가 절대 발생하지 않는지
2. **예외 처리**가 올바르게 동작하는지
3. **경계값/극단값**에서 안정적인지
4. **수동 테스트로 놓친 버그**를 발견

---

## 📋 Fuzz vs 수동 테스트

| 구분 | 수동 테스트 | Fuzz 테스트 |
|------|-------------|-------------|
| 입력 | 미리 정의된 값 | 무작위 생성 |
| 커버리지 | 예상한 케이스만 | 예상 못한 케이스 포함 |
| 목적 | 특정 동작 검증 | 안정성 검증 |
| 속성 | "X 입력 → Y 출력" | "어떤 입력이든 5xx 없음" |

---

## 🔧 Hypothesis 전략(Strategy) 예시

```python
from hypothesis import strategies as st

# 상품 ID - 다양한 형태
product_id_strategy = st.one_of(
    st.integers(min_value=-1000, max_value=10000),
    st.text(min_size=1, max_size=20),
    st.just("null"),
    st.just(""),
)

# 수량 - 음수, 0, 큰 수 포함
quantity_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),
    st.floats(min_value=-100.0, max_value=100000.0),
)

# 검색어 - 악성 입력 포함
search_strategy = st.one_of(
    st.text(min_size=0, max_size=1000),
    st.just("'; DROP TABLE products; --"),
    st.just("<script>alert('xss')</script>"),
    st.just("🎉💀🔥"),
)
```

---

## 📝 테스트 예시

### 1. 기본 Fuzz 테스트

```python
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck

@pytest.mark.fuzz
@pytest.mark.django_db(transaction=True)
class TestProductsFuzz:
    """🛍️ 상품 API Fuzz 테스트"""

    @given(search=st.text(min_size=0, max_size=500))
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_search_fuzz(self, client, schema_test_product, search):
        """검색 파라미터 퍼징"""
        # Act
        response = client.get("/api/products/", {"search": search})

        # Assert - 핵심: 5xx 에러 없음
        assert response.status_code < 500, \
            f"서버 에러!\ninput: {repr(search)}\nstatus: {response.status_code}"
```

### 2. 여러 파라미터 조합

```python
@given(
    product_id=product_id_strategy,
    quantity=quantity_strategy,
)
@hypothesis_settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
def test_cart_add_fuzz(self, client, auth_headers, product_id, quantity):
    """장바구니 추가 퍼징"""
    # Arrange
    headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
    data = {"product_id": product_id, "quantity": quantity}

    # Act
    response = client.post(
        "/api/cart/add_item/",
        data=json.dumps(data, default=str),
        content_type="application/json",
        **headers,
    )

    # Assert
    assert response.status_code < 500, \
        f"서버 에러!\ndata: {data}\nstatus: {response.status_code}"
```

### 3. 인증 퍼징

```python
@given(
    username=st.text(min_size=0, max_size=200),
    password=st.text(min_size=0, max_size=200),
)
@hypothesis_settings(max_examples=50, deadline=None)
def test_login_fuzz(self, client, username, password):
    """로그인 자격 증명 퍼징"""
    # Arrange
    data = {"username": username, "password": password}

    # Act
    response = client.post(
        "/api/auth/login/",
        data=json.dumps(data),
        content_type="application/json",
    )

    # Assert - 5xx 없음, 400/401 허용
    assert response.status_code < 500
    assert response.status_code in [200, 400, 401, 403, 429]
```

---

## ⚙️ Hypothesis 설정 설명

```python
@hypothesis_settings(
    max_examples=50,        # 생성할 테스트 케이스 수
    deadline=None,          # 타임아웃 비활성화 (DB 느림)
    suppress_health_check=[ # 경고 억제
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,
    ],
    phases=[Phase.generate], # shrinking 비활성화 (속도)
)
```

---

## 📋 새 API Fuzz 테스트 체크리스트

| 파라미터 유형 | 전략 예시 |
|---------------|-----------|
| ID (path/body) | `st.one_of(st.integers(), st.text())` |
| 수량/금액 | `st.integers(min_value=-100, max_value=100000)` |
| 문자열 | `st.text(min_size=0, max_size=1000)` |
| 검색어 | 악성 입력 포함 전략 |
| 페이지 | `st.integers(min_value=-10, max_value=10000)` |

---

## ⚠️ 주의사항

### 1. 병렬 실행 비활성화
```bash
pytest -m fuzz -n 0  # Hypothesis 상태 유지 필요
```

### 2. JSON 직렬화
```python
json.dumps(data, default=str)  # NaN, Infinity 처리
```

### 3. fixture 사용 시 설정
```python
suppress_health_check=[HealthCheck.function_scoped_fixture]
```

---

## ✅ 완료 체크리스트

- [ ] 주요 입력 파라미터별 전략 정의
- [ ] `@given` 데코레이터 적용
- [ ] `@hypothesis_settings` 설정
- [ ] 5xx 에러 없음 검증
- [ ] `@pytest.mark.fuzz` 마커 추가
- [ ] AAA 주석 패턴 적용

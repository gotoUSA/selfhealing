# 📘 Workflow 테스트 가이드

> 여러 API를 순차적으로 호출하는 사용자 시나리오가 올바르게 동작하는지 검증하는 테스트 작성 가이드

---

## 🎯 목적

실제 사용자 시나리오를 시뮬레이션하여:
1. **상태 전이**가 올바르게 이루어지는지 (비회원→회원→장바구니→주문)
2. **API 간 연동**이 정상 동작하는지
3. **데이터 일관성**이 유지되는지
4. **권한 전이**가 올바른지

---

## 📋 주요 워크플로우 패턴

| 워크플로우 | 단계 |
|------------|------|
| 구매 플로우 | 상품조회 → 회원가입 → 로그인 → 장바구니 → 주문 |
| 위시리스트 | 로그인 → 위시리스트 추가 → 장바구니 이동 |
| 상품 탐색 | 카테고리 → 상품목록 → 상품상세 |
| 장바구니 관리 | 추가 → 수량변경 → 재고확인 → 비우기 |

---

## 📝 테스트 예시

### 1. 완전한 구매 플로우

```python
@pytest.mark.stateful
@pytest.mark.django_db(transaction=True)
class TestUserPurchaseFlow:
    """🛒 사용자 구매 플로우 테스트"""

    def test_complete_purchase_flow(self, client, schema_test_product):
        """완전한 구매 플로우: 비회원→회원→장바구니"""
        
        # ============================================
        # Step 1: 비회원 상품 조회
        # ============================================
        # Act
        response = client.get(f"/api/products/{schema_test_product.id}/")
        
        # Assert
        assert response.status_code == status.HTTP_200_OK

        # ============================================
        # Step 2: 회원가입
        # ============================================
        # Arrange
        unique_id = uuid.uuid4().hex[:8]
        register_data = {
            "username": f"test_{unique_id}",
            "email": f"test_{unique_id}@example.com",
            "password": "SecurePass123!",
            "password2": "SecurePass123!",
        }

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(register_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [200, 201]

        # ============================================
        # Step 3: 로그인
        # ============================================
        # Arrange
        login_data = {
            "username": f"test_{unique_id}",
            "password": "SecurePass123!",
        }

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(login_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        token = response.json().get("token", {}).get("access") or response.json().get("access")
        auth_header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        # ============================================
        # Step 4: 장바구니 추가
        # ============================================
        # Arrange
        cart_data = {"product_id": schema_test_product.id, "quantity": 2}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(cart_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [200, 201]

        # ============================================
        # Step 5: 장바구니 확인
        # ============================================
        # Act
        response = client.get("/api/cart/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
```

### 2. 상태 전이 검증

```python
def test_state_transitions(self, client, schema_test_product):
    """🔄 상태 전이 검증: ANONYMOUS → AUTHENTICATED → HAS_CART"""
    
    # State: ANONYMOUS
    # 보호된 API 접근 불가
    response = client.get("/api/orders/")
    assert response.status_code == 401

    # Transition: 로그인
    # ... (로그인 코드)

    # State: AUTHENTICATED
    # 보호된 API 접근 가능
    response = client.get("/api/orders/", **auth_header)
    assert response.status_code == 200

    # Transition: 장바구니 추가
    # ... (장바구니 추가 코드)

    # State: HAS_CART
    # 장바구니에 아이템 존재
    response = client.get("/api/cart/summary/", **auth_header)
    assert response.status_code == 200
    assert response.json().get("item_count", 0) > 0
```

### 3. 잘못된 상태 전이 차단

```python
def test_invalid_state_transition_blocked(self, client):
    """🚫 잘못된 상태 전이 차단"""
    
    # ANONYMOUS 상태에서 장바구니 추가 시도
    response = client.post(
        "/api/wishlist/toggle/",
        data=json.dumps({"product_id": 1}),
        content_type="application/json",
    )
    
    # Assert - 인증 필요
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
```

---

## 🔧 워크플로우 테스트 구조

```python
@pytest.mark.stateful           # 상태 기반 테스트 마커
@pytest.mark.schema             # 스키마 테스트 그룹
@pytest.mark.django_db(transaction=True)  # 트랜잭션 격리
class TestWorkflowName:
    """
    📋 워크플로우 설명
    
    🔄 상태 전이:
    State1 → [Event] → State2 → [Event] → State3
    """

    def test_workflow_scenario(self, client, ...):
        """시나리오 설명"""
        # Step 1: ...
        # Step 2: ...
        # Step N: ...
```

---

## 📋 새 워크플로우 테스트 체크리스트

| 확인 항목 | 설명 |
|-----------|------|
| 시나리오 정의 | 어떤 사용자 행동을 테스트하는지 |
| 상태 전이 | 어떤 상태에서 어떤 상태로 변하는지 |
| 각 단계 검증 | 각 API 호출 결과가 올바른지 |
| 데이터 연속성 | 이전 단계 데이터가 다음 단계에 반영되는지 |
| 실패 케이스 | 잘못된 전이가 차단되는지 |

---

## ⚠️ 주의사항

### 1. 트랜잭션 격리
```python
@pytest.mark.django_db(transaction=True)  # 테스트 후 자동 롤백
```

### 2. 고유 데이터 생성
```python
unique_id = uuid.uuid4().hex[:8]
username = f"test_{unique_id}"  # 충돌 방지
```

### 3. 토큰 추출 주의
```python
# 응답 구조에 따라 다름
token = (
    response.json().get("access") or 
    response.json().get("token", {}).get("access")
)
```

---

## ✅ 완료 체크리스트

- [ ] 워크플로우 시나리오 정의
- [ ] 각 단계별 API 호출 및 검증
- [ ] 상태 전이 검증
- [ ] 데이터 연속성 검증
- [ ] 잘못된 전이 차단 테스트
- [ ] `@pytest.mark.stateful` 마커 추가
- [ ] 트랜잭션 격리 설정
- [ ] AAA 주석 (각 Step에 적용)

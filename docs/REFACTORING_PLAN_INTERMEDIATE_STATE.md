# 🔧 장바구니/주문 중간 상태 처리 리팩토링 계획

> **리뷰 내용**: 장바구니에 담긴 동안 가격/재고/품절/등급 변경 시 발생하는 문제점 해결
> **작성일**: 2025-12-03
> **우선순위**: 🚨 Critical (실서비스에서 100% 발생 가능한 문제)

---

## 📋 목차

1. [현재 상태 분석](#1-현재-상태-분석)
2. [발견된 문제점](#2-발견된-문제점)
3. [리팩토링 계획](#3-리팩토링-계획)
4. [구현 상세](#4-구현-상세)
5. [테스트 계획](#5-테스트-계획)
6. [마이그레이션 전략](#6-마이그레이션-전략)

---

## 1. 현재 상태 분석

### 1.1 현재 구현된 기능 ✅

| 기능 | 파일 | 상태 |
|------|------|------|
| 재고 부족 검증 (주문 시점) | `order_serializers.py` | ✅ 구현됨 |
| 품절 상품 검증 (주문 시점) | `order_serializers.py` | ✅ 구현됨 |
| 비활성 상품 검증 (주문 시점) | `order_serializers.py` | ✅ 구현됨 |
| 장바구니 재고 확인 API | `cart_service.py` → `check_stock()` | ✅ 구현됨 |
| 주문 당시 가격 스냅샷 | `OrderItem.price` 필드 | ✅ 구현됨 |
| 동시성 제어 (select_for_update) | `order_service.py`, `order_tasks.py` | ✅ 구현됨 |

### 1.2 현재 미구현/부족한 기능 ❌

| 문제점 | 상태 | 영향도 |
|--------|------|--------|
| 장바구니에 담긴 동안 가격 변경 시 사용자 알림 없음 | ❌ 미구현 | 🔴 High |
| CartItem에 담긴 시점 가격 저장 안함 | ❌ 미구현 | 🔴 High |
| 등급(membership_tier) 변경 시 적립률 불일치 가능성 | ⚠️ 부분적 | 🟡 Medium |
| 품절된 상품이 장바구니에 남아있을 때 UI 경고 없음 | ⚠️ 부분적 | 🟡 Medium |
| 장바구니 → 주문 전환 시 가격 변경 검증 없음 | ❌ 미구현 | 🔴 High |

---

## 2. 발견된 문제점

### 2.1 🔴 Critical: 카트에 담긴 동안 가격이 변경되었을 때

**현재 동작:**
```python
# CartItem 모델 (shopping/models/cart.py)
@property
def subtotal(self) -> Decimal:
    """소계 계산 (현재 상품 가격 x 수량)"""
    return self.product.price * self.quantity  # ⚠️ 항상 최신 가격 사용
```

**문제 시나리오:**
1. 사용자가 10,000원 상품을 장바구니에 담음
2. 관리자가 가격을 15,000원으로 변경
3. 사용자가 결제 → **예상 금액과 다른 금액 결제됨**
4. 사용자 불만 또는 분쟁 발생

**영향:**
- 사용자 신뢰도 하락
- CS 문의 증가
- 법적 분쟁 가능성 (소비자보호법 위반 가능)

---

### 2.2 🔴 Critical: 품절된 상품이 카트에 남아 있을 때

**현재 동작:**
```python
# CartService.check_stock() 호출 시에만 확인
# 사용자가 직접 호출하지 않으면 품절 여부 모름
```

**문제 시나리오:**
1. 사용자가 재고 10개 상품을 장바구니에 담음
2. 다른 사용자가 해당 상품 전량 구매 → 품절
3. 첫 번째 사용자가 주문 시도 → **주문 실패**
4. 사용자 경험 저하

**영향:**
- 구매 전환율 하락
- 장바구니 포기율 증가

---

### 2.3 🔴 Critical: 주문 도중 재고가 변경되었을 때

**현재 동작:**
```python
# order_tasks.py - 비동기 처리 시 재고 확인
if product.stock < cart_item.quantity:
    order.status = "failed"
    order.failure_reason = f"재고 부족: {product.name}..."
```

**분석:**
- ✅ 재고 부족 시 주문 실패 처리는 구현됨
- ❌ 하지만 주문 생성 시점(동기)과 처리 시점(비동기) 사이 시간차 존재
- ❌ 사용자에게 "주문 처리 중" → "주문 실패" 전환이 갑작스러움

---

### 2.4 🟡 Medium: 중간 과정에서 유저의 membership tier가 바뀌었을 때

**현재 동작:**
```python
# payment_service.py - 결제 승인 시점에 적립률 계산
earn_rate = user.get_earn_rate()  # 현재 등급 기준
points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))
```

**문제 시나리오:**
1. VIP 회원(5% 적립)이 100,000원 주문 시작 → 예상 적립 5,000P
2. 주문 처리 중 등급이 Gold(3%)로 강등
3. 결제 완료 시 3,000P만 적립됨
4. 사용자: "5,000P 적립된다고 했는데?"

**영향:**
- 사용자 혼란
- CS 문의 증가
- 등급 정책에 대한 불신

---

## 3. 리팩토링 계획

### 3.1 Phase 1: 장바구니 가격 스냅샷 (Priority: 🔴 Critical)

**목표:** 장바구니에 담는 시점의 가격을 저장하고, 변경 시 사용자에게 알림

#### 3.1.1 CartItem 모델 수정

```python
# shopping/models/cart.py

class CartItem(models.Model):
    # 기존 필드들...

    # 🆕 담은 시점 가격 저장
    price_at_add = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        null=True,  # 마이그레이션 호환성
        verbose_name="담은 시점 가격",
        help_text="장바구니에 담을 때의 상품 가격",
    )

    @property
    def is_price_changed(self) -> bool:
        """가격 변경 여부"""
        if self.price_at_add is None:
            return False
        return self.product.price != self.price_at_add

    @property
    def price_difference(self) -> Decimal:
        """가격 차이 (양수면 인상, 음수면 인하)"""
        if self.price_at_add is None:
            return Decimal("0")
        return self.product.price - self.price_at_add
```

#### 3.1.2 CartService 수정

```python
# shopping/services/cart_service.py

@staticmethod
@transaction.atomic
def add_item(cart: Cart, product_id: int, quantity: int = 1) -> CartItem:
    """장바구니에 상품 추가"""
    # ... 기존 로직 ...

    if existing_item:
        # 기존 아이템: 수량만 증가 (가격은 최초 담은 시점 유지)
        CartItem.objects.filter(pk=existing_item.pk).update(
            quantity=F("quantity") + quantity
        )
        existing_item.refresh_from_db()
        cart_item = existing_item
    else:
        # 🆕 새 아이템: 현재 가격 스냅샷 저장
        cart_item = CartItem.objects.create(
            cart=cart,
            product=product,
            quantity=quantity,
            price_at_add=product.price,  # 🆕 가격 스냅샷
        )

    return cart_item
```

#### 3.1.3 가격 변경 감지 메서드 추가

```python
# shopping/services/cart_service.py

@staticmethod
def check_price_changes(cart: Cart) -> list[dict]:
    """
    장바구니 상품들의 가격 변경 확인

    Returns:
        list[dict]: 가격 변경된 상품 목록
    """
    changes = []

    for item in cart.items.select_related("product"):
        if item.is_price_changed:
            changes.append({
                "item_id": item.id,
                "product_id": item.product.id,
                "product_name": item.product.name,
                "original_price": item.price_at_add,
                "current_price": item.product.price,
                "difference": item.price_difference,
                "change_type": "increased" if item.price_difference > 0 else "decreased",
            })

    return changes

@staticmethod
def update_item_prices(cart: Cart) -> int:
    """
    장바구니 상품 가격을 현재 가격으로 업데이트
    (사용자가 가격 변경을 확인하고 동의한 후 호출)

    Returns:
        int: 업데이트된 아이템 수
    """
    updated_count = 0

    for item in cart.items.select_related("product"):
        if item.is_price_changed:
            item.price_at_add = item.product.price
            item.save(update_fields=["price_at_add"])
            updated_count += 1

    return updated_count
```

---

### 3.2 Phase 2: 재고/품절 실시간 검증 강화 (Priority: 🔴 Critical)

**목표:** 주문 전 실시간 재고 상태 검증 및 사용자 알림

#### 3.2.1 장바구니 조회 시 자동 검증

```python
# shopping/views/cart_views.py

def retrieve(self, request: Request) -> Response:
    """장바구니 전체 정보 조회"""
    cart = self._get_cart()

    # 🆕 자동으로 재고/가격 변경 확인
    stock_issues = CartService.check_stock(cart)
    price_changes = CartService.check_price_changes(cart)

    serializer = self.get_serializer(cart)
    response_data = serializer.data

    # 🆕 경고 정보 추가
    if stock_issues or price_changes:
        response_data["warnings"] = {
            "stock_issues": [asdict(issue) for issue in stock_issues],
            "price_changes": price_changes,
            "has_issues": True,
        }

    return Response(response_data)
```

#### 3.2.2 주문 생성 전 검증 강화

```python
# shopping/serializers/order_serializers.py

def _validate_cart_items(self, cart: Cart) -> QuerySet:
    """장바구니 항목 검증 (가격 변경 포함)"""
    # ... 기존 검증 ...

    # 🆕 가격 변경 확인 및 강제 확인 요구
    price_changes = CartService.check_price_changes(cart)
    if price_changes:
        changes_info = ", ".join([
            f"{c['product_name']}: {c['original_price']}원 → {c['current_price']}원"
            for c in price_changes
        ])
        errors.append(
            f"일부 상품의 가격이 변경되었습니다. 장바구니에서 확인 후 다시 주문해주세요. ({changes_info})"
        )

    if errors:
        raise serializers.ValidationError({"cart_items": errors})

    return cart_items
```

---

### 3.3 Phase 3: 적립률 스냅샷 (Priority: 🟡 Medium)

**목표:** 주문 시점의 적립률을 저장하여 일관성 보장

#### 3.3.1 Order 모델 수정

```python
# shopping/models/order.py

class Order(models.Model):
    # 기존 필드들...

    # 🆕 주문 시점 적립률 저장
    earn_rate_at_order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="주문 시점 적립률",
        help_text="주문 생성 시점의 회원 등급별 적립률 (%)",
    )

    # 🆕 주문 시점 회원 등급 저장
    membership_at_order = models.CharField(
        max_length=10,
        blank=True,
        default="",
        verbose_name="주문 시점 회원등급",
        help_text="주문 생성 시점의 회원 등급",
    )
```

#### 3.3.2 OrderService 수정

```python
# shopping/services/order_service.py

@staticmethod
@transaction.atomic
def create_order_from_cart(...) -> Order:
    """장바구니에서 주문 생성"""
    # ... 기존 로직 ...

    # 🆕 주문 생성 시 적립률 스냅샷 저장
    order = Order.objects.create(
        user=user,
        status="pending",
        # ... 기존 필드들 ...
        earn_rate_at_order=user.get_earn_rate(),  # 🆕
        membership_at_order=user.membership_level,  # 🆕
    )
```

#### 3.3.3 결제 완료 시 스냅샷된 적립률 사용

```python
# shopping/services/payment_service.py

@staticmethod
@transaction.atomic
def confirm_payment_sync(...) -> dict[str, Any]:
    """결제 승인 처리"""
    # ... 기존 로직 ...

    # 🆕 스냅샷된 적립률 사용 (현재 등급 대신)
    if order.final_amount > 0:
        earn_rate = order.earn_rate_at_order  # 🆕 스냅샷 사용
        product_amount = order.total_amount
        points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))
```

---

### 3.4 Phase 4: 장바구니 상태 자동 정리 (Priority: 🟢 Low)

**목표:** 품절/비활성 상품 자동 처리

#### 3.4.1 장바구니 정리 서비스

```python
# shopping/services/cart_service.py

@staticmethod
@transaction.atomic
def cleanup_unavailable_items(cart: Cart) -> dict:
    """
    구매 불가능한 상품 자동 정리

    Returns:
        dict: 정리된 상품 정보
    """
    removed_items = []
    updated_items = []

    for item in cart.items.select_related("product"):
        product = item.product

        # 비활성 상품 제거
        if not product.is_active:
            removed_items.append({
                "product_name": product.name,
                "reason": "판매 중단",
            })
            item.delete()
            continue

        # 품절 상품 제거
        if product.stock == 0:
            removed_items.append({
                "product_name": product.name,
                "reason": "품절",
            })
            item.delete()
            continue

        # 재고 부족 시 수량 조정
        if item.quantity > product.stock:
            old_quantity = item.quantity
            item.quantity = product.stock
            item.save(update_fields=["quantity"])
            updated_items.append({
                "product_name": product.name,
                "old_quantity": old_quantity,
                "new_quantity": product.stock,
                "reason": "재고 부족",
            })

    return {
        "removed": removed_items,
        "updated": updated_items,
        "removed_count": len(removed_items),
        "updated_count": len(updated_items),
    }
```

---

## 4. 구현 상세

### 4.1 데이터베이스 마이그레이션

```python
# shopping/migrations/XXXX_add_intermediate_state_fields.py

from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('shopping', 'previous_migration'),
    ]

    operations = [
        # CartItem 가격 스냅샷 필드
        migrations.AddField(
            model_name='cartitem',
            name='price_at_add',
            field=models.DecimalField(
                decimal_places=0,
                max_digits=10,
                null=True,
                verbose_name='담은 시점 가격',
            ),
        ),

        # Order 적립률/등급 스냅샷 필드
        migrations.AddField(
            model_name='order',
            name='earn_rate_at_order',
            field=models.PositiveSmallIntegerField(
                default=1,
                verbose_name='주문 시점 적립률',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='membership_at_order',
            field=models.CharField(
                blank=True,
                default='',
                max_length=10,
                verbose_name='주문 시점 회원등급',
            ),
        ),
    ]
```

### 4.2 기존 데이터 마이그레이션

```python
# shopping/migrations/XXXX_populate_price_at_add.py

def populate_price_at_add(apps, schema_editor):
    """기존 CartItem에 현재 가격으로 price_at_add 채우기"""
    CartItem = apps.get_model('shopping', 'CartItem')
    for item in CartItem.objects.select_related('product').all():
        if item.price_at_add is None:
            item.price_at_add = item.product.price
            item.save(update_fields=['price_at_add'])

class Migration(migrations.Migration):
    dependencies = [
        ('shopping', 'XXXX_add_intermediate_state_fields'),
    ]

    operations = [
        migrations.RunPython(populate_price_at_add, migrations.RunPython.noop),
    ]
```

### 4.3 API 응답 스키마 변경

```python
# shopping/serializers/cart_serializers.py

class CartItemSerializer(serializers.ModelSerializer):
    # 기존 필드들...

    # 🆕 가격 변경 관련 필드
    price_at_add = serializers.DecimalField(max_digits=10, decimal_places=0, read_only=True)
    is_price_changed = serializers.BooleanField(read_only=True)
    price_difference = serializers.DecimalField(max_digits=10, decimal_places=0, read_only=True)
    current_price = serializers.DecimalField(source='product.price', max_digits=10, decimal_places=0, read_only=True)

    class Meta:
        model = CartItem
        fields = [
            # 기존 필드들...
            'price_at_add',
            'is_price_changed',
            'price_difference',
            'current_price',
        ]
```

---

## 5. 테스트 계획

### 5.1 신규 테스트 케이스

```
shopping/tests/unit/test_intermediate_state.py
```

| 테스트 클래스 | 테스트 메서드 | 설명 |
|---------------|--------------|------|
| `TestCartPriceSnapshot` | `test_price_at_add_saved_on_add_item` | 장바구니 추가 시 가격 스냅샷 저장 |
| | `test_price_at_add_preserved_on_quantity_update` | 수량 변경 시 가격 유지 |
| | `test_is_price_changed_detects_increase` | 가격 인상 감지 |
| | `test_is_price_changed_detects_decrease` | 가격 인하 감지 |
| | `test_price_difference_calculation` | 가격 차이 계산 정확성 |
| `TestCartStockValidation` | `test_order_blocked_when_price_changed` | 가격 변경 시 주문 차단 |
| | `test_order_allowed_after_price_confirmation` | 가격 확인 후 주문 허용 |
| | `test_stock_decrease_during_cart` | 장바구니 보관 중 재고 감소 처리 |
| | `test_product_inactive_during_cart` | 장바구니 보관 중 상품 비활성화 처리 |
| `TestMembershipSnapshot` | `test_earn_rate_snapshot_saved` | 주문 시점 적립률 저장 |
| | `test_membership_downgrade_during_order` | 주문 중 등급 하락 시 스냅샷 적립률 사용 |
| | `test_membership_upgrade_during_order` | 주문 중 등급 상승 시 스냅샷 적립률 사용 |

### 5.2 통합 테스트 시나리오

```
shopping/tests/integration/test_intermediate_state_flow.py
```

| 시나리오 | 설명 |
|----------|------|
| 가격 변경 → 장바구니 조회 → 경고 표시 → 가격 동의 → 주문 성공 | 전체 플로우 테스트 |
| 품절 → 장바구니 조회 → 자동 제거 → 주문 성공 | 품절 상품 자동 처리 |
| 등급 변경 → 주문 생성 → 결제 → 스냅샷 적립률로 포인트 적립 | 등급 스냅샷 플로우 |
| 동시 주문 → 재고 경합 → 일부 성공/일부 실패 | 동시성 테스트 |

---

## 6. 마이그레이션 전략

### 6.1 배포 단계

| 단계 | 내용 | 예상 시간 | 롤백 가능 |
|------|------|----------|----------|
| 1 | DB 마이그레이션 (신규 필드 추가) | 5분 | ✅ |
| 2 | 기존 데이터 채우기 (price_at_add) | 10분 | ✅ |
| 3 | 백엔드 코드 배포 (Phase 1-2) | 15분 | ✅ |
| 4 | 프론트엔드 경고 UI 배포 | 30분 | ✅ |
| 5 | Phase 3 배포 (적립률 스냅샷) | 10분 | ✅ |
| 6 | Phase 4 배포 (자동 정리) | 10분 | ✅ |

### 6.2 하위 호환성

- `price_at_add` 필드는 `null=True`로 설정하여 기존 데이터와 호환
- 프론트엔드가 업데이트되기 전까지 API 응답에 경고 정보만 추가 (기존 동작 유지)
- 점진적 롤아웃 (10% → 50% → 100%)

### 6.3 모니터링

| 지표 | 기준 | 알림 조건 |
|------|------|----------|
| 가격 변경 감지 수 | - | 일일 100건 이상 시 알림 |
| 주문 차단 수 (가격 변경) | - | 일일 10건 이상 시 알림 |
| 장바구니 자동 정리 수 | - | 일일 50건 이상 시 알림 |
| 적립률 불일치 건수 | 0건 | 1건 이상 시 알림 |

---

## 7. 예상 일정

| Phase | 작업 | 예상 소요 |
|-------|------|----------|
| Phase 1 | 가격 스냅샷 구현 + 테스트 | 4시간 |
| Phase 2 | 재고/품절 검증 강화 + 테스트 | 3시간 |
| Phase 3 | 적립률 스냅샷 구현 + 테스트 | 2시간 |
| Phase 4 | 자동 정리 구현 + 테스트 | 2시간 |
| 통합 | 통합 테스트 + 문서화 | 2시간 |
| **총계** | | **13시간** |

---

## 8. 리스크 및 대응 방안

| 리스크 | 영향 | 대응 방안 |
|--------|------|----------|
| 기존 장바구니 데이터 마이그레이션 실패 | 🔴 High | 현재 가격으로 기본값 설정, 롤백 스크립트 준비 |
| 프론트엔드 UI 업데이트 지연 | 🟡 Medium | 백엔드만 먼저 배포, 경고 정보는 무시해도 기능 동작 |
| 가격 변경 검증으로 주문 전환율 하락 | 🟡 Medium | A/B 테스트로 영향 측정, 필요시 경고만 표시 (차단 안함) |
| 동시성 이슈 발생 | 🔴 High | 기존 select_for_update 패턴 유지, 추가 락 최소화 |

---

## 9. 승인 체크리스트

- [ ] 기술 리드 승인
- [ ] 코드 리뷰 완료
- [ ] 테스트 커버리지 80% 이상
- [ ] 스테이징 환경 검증
- [ ] 롤백 계획 수립
- [ ] 모니터링 대시보드 설정
- [ ] 문서화 완료

---

**작성자:** GitHub Copilot
**최종 수정일:** 2025-12-03

"""
Test factories for shopping app

Factory Boy를 사용하여 테스트 데이터를 생성합니다.
- 재사용 가능한 테스트 객체 생성
- 기본값 제공 및 필요시 오버라이드 가능
- 관계형 데이터 자동 처리
"""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.point import PointHistory
from shopping.models.product import Category, Product, ProductImage
from shopping.models.product_qa import ProductAnswer, ProductQuestion
from shopping.models.user import User


# ==========================================
# 상수 정의
# ==========================================


class TestConstants:
    """테스트에서 사용하는 상수"""

    # 금액
    DEFAULT_PRODUCT_PRICE = Decimal("10000")
    DEFAULT_SHIPPING_FEE = Decimal("3000")
    DEFAULT_TOTAL_AMOUNT = Decimal("13000")
    FREE_SHIPPING_THRESHOLD = Decimal("30000")
    REMOTE_AREA_FEE = Decimal("3000")

    # 포인트
    DEFAULT_POINTS = 5000
    HIGH_POINTS = 50000
    DEFAULT_EARN_POINTS = 100

    # 재고
    DEFAULT_STOCK = 100
    LOW_STOCK = 1
    OUT_OF_STOCK = 0

    # 배송 정보
    DEFAULT_SHIPPING_NAME = "홍길동"
    DEFAULT_SHIPPING_PHONE = "010-1234-5678"
    DEFAULT_SHIPPING_POSTAL_CODE = "12345"
    DEFAULT_SHIPPING_ADDRESS = "서울시 강남구"
    DEFAULT_SHIPPING_ADDRESS_DETAIL = "101동"

    # Toss 응답 날짜
    DEFAULT_APPROVED_AT = "2025-01-15T10:00:00+09:00"
    DEFAULT_CANCELED_AT = "2025-01-15T11:00:00+09:00"

    # 비밀번호
    DEFAULT_PASSWORD = "testpass123"


# ==========================================
# User & Auth Factories
# ==========================================


class UserFactory(DjangoModelFactory):
    """
    User factory

    사용 예시:
        user = UserFactory()  # 기본 사용자
        user = UserFactory.verified()  # 이메일 인증 완료
        user = UserFactory.unverified()  # 이메일 미인증
        user = UserFactory.seller()  # 판매자
        user = UserFactory.with_points(10000)  # 포인트 10000
    """

    class Meta:
        model = User
        django_get_or_create = ("username",)
        skip_postgeneration_save = True  # post_generation에서 명시적으로 save() 호출하므로 자동 저장 비활성화

    username = factory.Sequence(lambda n: f"testuser{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@test.com")
    phone_number = factory.Sequence(lambda n: f"010-{1000+n:04d}-{5678+n:04d}")
    points = 0
    membership_level = "bronze"
    is_email_verified = True  # 기본값: 인증 완료
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        """
        비밀번호 설정 및 저장

        extracted가 제공되면 해당 비밀번호 사용, 아니면 기본 비밀번호 사용
        """
        if not create:
            # build()로 생성된 경우 (DB 저장 안 함)
            return

        password_value = extracted if extracted else TestConstants.DEFAULT_PASSWORD
        obj.set_password(password_value)
        obj.save()

    @classmethod
    def verified(cls, **kwargs):
        """이메일 인증 완료 사용자 (기본값과 동일)"""
        kwargs.setdefault("is_email_verified", True)
        return cls(**kwargs)

    @classmethod
    def unverified(cls, **kwargs):
        """이메일 미인증 사용자"""
        kwargs.setdefault("is_email_verified", False)
        return cls(**kwargs)

    @classmethod
    def inactive(cls, **kwargs):
        """비활성화된 사용자"""
        kwargs.setdefault("is_active", False)
        return cls(**kwargs)

    @classmethod
    def withdrawn(cls, **kwargs):
        """탈퇴한 사용자"""
        kwargs.setdefault("is_withdrawn", True)
        kwargs.setdefault("is_active", False)
        kwargs.setdefault("withdrawn_at", timezone.now())
        return cls(**kwargs)

    @classmethod
    def seller(cls, **kwargs):
        """판매자 사용자"""
        kwargs.setdefault("is_seller", True)
        kwargs.setdefault("is_email_verified", True)
        return cls(**kwargs)

    @classmethod
    def admin(cls, **kwargs):
        """관리자 사용자"""
        kwargs.setdefault("is_staff", True)
        kwargs.setdefault("is_superuser", True)
        kwargs.setdefault("is_email_verified", True)
        return cls(**kwargs)

    @classmethod
    def with_points(cls, amount=None, **kwargs):
        """포인트를 가진 사용자"""
        if amount is None:
            amount = TestConstants.DEFAULT_POINTS
        kwargs.setdefault("points", amount)
        return cls(**kwargs)

    @classmethod
    def with_high_points(cls, **kwargs):
        """많은 포인트를 가진 사용자 (50,000)"""
        kwargs.setdefault("points", TestConstants.HIGH_POINTS)
        return cls(**kwargs)

    @classmethod
    def with_membership(cls, level="bronze", **kwargs):
        """특정 등급의 사용자"""
        kwargs.setdefault("membership_level", level)
        return cls(**kwargs)


class EmailVerificationTokenFactory(DjangoModelFactory):
    """
    이메일 인증 토큰 Factory

    사용 예시:
        token = EmailVerificationTokenFactory()  # 유효한 토큰
        token = EmailVerificationTokenFactory.expired()  # 만료된 토큰
        token = EmailVerificationTokenFactory.used()  # 사용된 토큰
    """

    class Meta:
        model = "shopping.EmailVerificationToken"

    user = factory.SubFactory(UserFactory, is_email_verified=False)

    @classmethod
    def valid(cls, **kwargs):
        """유효한 토큰 (기본값)"""
        return cls(**kwargs)

    @classmethod
    def expired(cls, hours_ago=25, **kwargs):
        """만료된 토큰 (24시간 경과)"""
        token = cls(**kwargs)
        token.created_at = timezone.now() - timedelta(hours=hours_ago)
        token.save()
        return token

    @classmethod
    def used(cls, **kwargs):
        """사용된 토큰"""
        token = cls(**kwargs)
        token.mark_as_used()
        return token

    @classmethod
    def recent(cls, seconds_ago=30, **kwargs):
        """방금 생성된 토큰 (재발송 제한 테스트용)"""
        token = cls(**kwargs)
        token.created_at = timezone.now() - timedelta(seconds=seconds_ago)
        token.save()
        return token


class PasswordResetTokenFactory(DjangoModelFactory):
    """
    비밀번호 재설정 토큰 Factory

    사용 예시:
        token = PasswordResetTokenFactory()
        token = PasswordResetTokenFactory.expired()
    """

    class Meta:
        model = "shopping.PasswordResetToken"

    user = factory.SubFactory(UserFactory)

    @classmethod
    def valid(cls, **kwargs):
        """유효한 토큰"""
        return cls(**kwargs)

    @classmethod
    def expired(cls, hours_ago=25, **kwargs):
        """만료된 토큰 (24시간 경과)"""
        token = cls(**kwargs)
        token.created_at = timezone.now() - timedelta(hours=hours_ago)
        token.save()
        return token


class SocialAppFactory(DjangoModelFactory):
    """
    소셜 앱 Factory

    사용 예시:
        app = SocialAppFactory.google()
        app = SocialAppFactory.kakao()
    """

    class Meta:
        model = "socialaccount.SocialApp"

    name = "Test Social App"
    provider = "google"
    client_id = factory.Sequence(lambda n: f"test_client_id_{n}")
    secret = factory.Sequence(lambda n: f"test_secret_{n}")

    @factory.post_generation
    def sites(self, create, extracted, **kwargs):
        if not create:
            return

        from django.contrib.sites.models import Site

        site = Site.objects.get_or_create(id=1, defaults={"domain": "testserver", "name": "testserver"})[0]
        self.sites.add(site)

    @classmethod
    def google(cls, **kwargs):
        """Google 소셜 앱"""
        kwargs.setdefault("provider", "google")
        kwargs.setdefault("name", "Google Test App")
        kwargs.setdefault("client_id", "test_google_client_id")
        kwargs.setdefault("secret", "test_google_secret")
        return cls(**kwargs)

    @classmethod
    def kakao(cls, **kwargs):
        """Kakao 소셜 앱"""
        kwargs.setdefault("provider", "kakao")
        kwargs.setdefault("name", "Kakao Test App")
        kwargs.setdefault("client_id", "test_kakao_client_id")
        kwargs.setdefault("secret", "test_kakao_secret")
        return cls(**kwargs)

    @classmethod
    def naver(cls, **kwargs):
        """Naver 소셜 앱"""
        kwargs.setdefault("provider", "naver")
        kwargs.setdefault("name", "Naver Test App")
        kwargs.setdefault("client_id", "test_naver_client_id")
        kwargs.setdefault("secret", "test_naver_secret")
        return cls(**kwargs)


class SocialAccountFactory(DjangoModelFactory):
    """
    소셜 계정 Factory

    사용 예시:
        account = SocialAccountFactory()
        account = SocialAccountFactory.google(user=user)
    """

    class Meta:
        model = "socialaccount.SocialAccount"

    user = factory.SubFactory(UserFactory)
    provider = "google"
    uid = factory.Sequence(lambda n: f"social_uid_{n}")
    extra_data = {}

    @classmethod
    def google(cls, **kwargs):
        """Google 계정"""
        kwargs.setdefault("provider", "google")
        kwargs.setdefault("uid", "google_user_id_123456")
        return cls(**kwargs)

    @classmethod
    def kakao(cls, **kwargs):
        """Kakao 계정"""
        kwargs.setdefault("provider", "kakao")
        kwargs.setdefault("uid", "123456789")
        return cls(**kwargs)

    @classmethod
    def naver(cls, **kwargs):
        """Naver 계정"""
        kwargs.setdefault("provider", "naver")
        kwargs.setdefault("uid", "naver_user_id_12345")
        return cls(**kwargs)


# ==========================================
# Product Factories
# ==========================================


class CategoryFactory(DjangoModelFactory):
    """Category factory"""

    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"테스트 카테고리 {n}")
    slug = factory.Sequence(lambda n: f"test-category-{n}")


class ProductFactory(DjangoModelFactory):
    """
    Product factory

    사용 예시:
        product = ProductFactory()  # 기본 상품
        product = ProductFactory.out_of_stock()  # 품절
        product = ProductFactory.low_stock()  # 재고 1개
        product = ProductFactory.inactive()  # 판매 중단
    """

    class Meta:
        model = Product

    name = factory.Sequence(lambda n: f"테스트 상품 {n}")
    category = factory.SubFactory(CategoryFactory)
    seller = factory.SubFactory(UserFactory, is_seller=True, is_email_verified=True)
    price = TestConstants.DEFAULT_PRODUCT_PRICE
    stock = TestConstants.DEFAULT_STOCK
    sku = factory.Sequence(lambda n: f"TEST-SKU-{n:06d}")
    is_active = True
    description = "테스트 상품 설명"

    @classmethod
    def active(cls, **kwargs):
        """판매중인 상품 (기본값)"""
        kwargs.setdefault("is_active", True)
        return cls(**kwargs)

    @classmethod
    def inactive(cls, **kwargs):
        """판매 중단 상품"""
        kwargs.setdefault("is_active", False)
        return cls(**kwargs)

    @classmethod
    def out_of_stock(cls, **kwargs):
        """품절 상품"""
        kwargs.setdefault("stock", TestConstants.OUT_OF_STOCK)
        return cls(**kwargs)

    @classmethod
    def low_stock(cls, **kwargs):
        """재고 부족 상품 (1개)"""
        kwargs.setdefault("stock", TestConstants.LOW_STOCK)
        return cls(**kwargs)

    @classmethod
    def with_price(cls, price, **kwargs):
        """특정 가격의 상품"""
        kwargs.setdefault("price", price)
        return cls(**kwargs)

    @classmethod
    def with_long_name(cls, **kwargs):
        """긴 이름의 상품"""
        kwargs.setdefault("name", "아주 긴 상품명 테스트 " * 10)
        return cls(**kwargs)


class ProductImageFactory(DjangoModelFactory):
    """
    ProductImage factory

    사용 예시:
        image = ProductImageFactory()
        image = ProductImageFactory.primary()
    """

    class Meta:
        model = ProductImage

    product = factory.SubFactory(ProductFactory)
    image = factory.django.ImageField(color="blue")
    alt_text = factory.Sequence(lambda n: f"테스트 이미지 {n}")
    is_primary = False
    order = 0

    @classmethod
    def primary(cls, **kwargs):
        """대표 이미지"""
        kwargs.setdefault("is_primary", True)
        return cls(**kwargs)


class ProductReviewFactory(DjangoModelFactory):
    """
    ProductReview factory

    사용 예시:
        review = ProductReviewFactory()
        review = ProductReviewFactory(rating=3)
        review = ProductReviewFactory.low_rating()
    """

    class Meta:
        model = "shopping.ProductReview"

    product = factory.SubFactory(ProductFactory)
    user = factory.SubFactory(UserFactory)
    rating = 5
    comment = factory.Sequence(lambda n: f"테스트 리뷰 내용 {n}")

    @classmethod
    def low_rating(cls, **kwargs):
        """낮은 평점 리뷰"""
        kwargs.setdefault("rating", 1)
        return cls(**kwargs)

    @classmethod
    def with_rating(cls, rating, **kwargs):
        """특정 평점 리뷰"""
        kwargs.setdefault("rating", rating)
        return cls(**kwargs)


# ==========================================
# Order & OrderItem Factories
# ==========================================


class OrderFactory(DjangoModelFactory):
    """
    Order factory

    기본적으로 pending 상태의 주문을 생성합니다.
    배송 정보는 TestConstants의 기본값을 사용합니다.

    사용 예시:
        order = OrderFactory()  # pending 주문
        order = OrderFactory.paid()  # 결제 완료 주문
        order = OrderFactory.with_points(2000)  # 포인트 사용
        order = OrderFactory.with_items(product_count=3)  # 여러 상품
    """

    class Meta:
        model = Order

    user = factory.SubFactory(UserFactory, is_email_verified=True)
    status = "confirmed"
    total_amount = TestConstants.DEFAULT_PRODUCT_PRICE
    shipping_fee = TestConstants.DEFAULT_SHIPPING_FEE
    final_amount = factory.LazyAttribute(lambda obj: obj.total_amount + obj.shipping_fee - obj.used_points)
    used_points = 0
    earned_points = 0
    payment_method = ""

    # 배송 정보
    shipping_name = TestConstants.DEFAULT_SHIPPING_NAME
    shipping_phone = TestConstants.DEFAULT_SHIPPING_PHONE
    shipping_postal_code = TestConstants.DEFAULT_SHIPPING_POSTAL_CODE
    shipping_address = TestConstants.DEFAULT_SHIPPING_ADDRESS
    shipping_address_detail = TestConstants.DEFAULT_SHIPPING_ADDRESS_DETAIL

    # 주문 번호 자동 생성
    order_number = factory.Sequence(lambda n: f"{timezone.now().strftime('%Y%m%d')}{n:06d}")

    @classmethod
    def pending(cls, **kwargs):
        """결제 대기 주문 (기본값)"""
        kwargs.setdefault("status", "pending")
        kwargs.setdefault("payment_method", "")
        return cls(**kwargs)

    @classmethod
    def paid(cls, **kwargs):
        """결제 완료 주문"""
        kwargs.setdefault("status", "paid")
        kwargs.setdefault("payment_method", "card")
        # 포인트 적립 (1%)
        total_amount = kwargs.get("total_amount", TestConstants.DEFAULT_PRODUCT_PRICE)
        kwargs.setdefault("earned_points", int(total_amount * Decimal("0.01")))
        return cls(**kwargs)

    @classmethod
    def canceled(cls, **kwargs):
        """취소된 주문"""
        kwargs.setdefault("status", "canceled")
        return cls(**kwargs)

    @classmethod
    def shipped(cls, **kwargs):
        """배송중 주문"""
        kwargs.setdefault("status", "shipped")
        kwargs.setdefault("payment_method", "card")
        return cls(**kwargs)

    @classmethod
    def delivered(cls, **kwargs):
        """배송 완료 주문"""
        kwargs.setdefault("status", "delivered")
        kwargs.setdefault("payment_method", "card")
        return cls(**kwargs)

    @classmethod
    def with_points(cls, used_points=2000, **kwargs):
        """포인트 사용 주문"""
        kwargs.setdefault("used_points", used_points)
        return cls(**kwargs)

    @classmethod
    def with_full_points(cls, **kwargs):
        """포인트 전액 결제 주문"""
        total_amount = kwargs.get("total_amount", TestConstants.DEFAULT_PRODUCT_PRICE)
        shipping_fee = kwargs.get("shipping_fee", TestConstants.DEFAULT_SHIPPING_FEE)
        kwargs.setdefault("used_points", int(total_amount + shipping_fee))
        kwargs.setdefault("final_amount", Decimal("0"))
        return cls(**kwargs)

    @classmethod
    def with_free_shipping(cls, **kwargs):
        """무료 배송 주문"""
        kwargs.setdefault("total_amount", TestConstants.FREE_SHIPPING_THRESHOLD)
        kwargs.setdefault("shipping_fee", Decimal("0"))
        kwargs.setdefault("is_free_shipping", True)
        return cls(**kwargs)

    @classmethod
    def with_remote_area(cls, **kwargs):
        """도서산간 배송 주문"""
        kwargs.setdefault("additional_shipping_fee", TestConstants.REMOTE_AREA_FEE)
        kwargs.setdefault("shipping_postal_code", "63000")  # 제주
        return cls(**kwargs)

    @classmethod
    def with_items(cls, product_count=3, **kwargs):
        """여러 상품이 포함된 주문"""
        order = cls(**kwargs)
        for _ in range(product_count):
            OrderItemFactory(order=order)
        return order


class OrderItemFactory(DjangoModelFactory):
    """OrderItem factory"""

    class Meta:
        model = OrderItem

    order = factory.SubFactory(OrderFactory)
    product = factory.SubFactory(ProductFactory)
    product_name = factory.LazyAttribute(lambda obj: obj.product.name)
    quantity = 1
    price = factory.LazyAttribute(lambda obj: obj.product.price)


# ==========================================
# Payment Factories
# ==========================================


class PaymentFactory(DjangoModelFactory):
    """
    Payment factory

    Order와 연결된 Payment를 생성합니다.
    기본값은 ready 상태입니다.

    사용 예시:
        payment = PaymentFactory()  # ready 상태
        payment = PaymentFactory.done()  # 완료 (일반)
        payment = PaymentFactory.done_card()  # 카드 결제 완료
        payment = PaymentFactory.done(method="계좌이체")  # 계좌이체 완료
        payment = PaymentFactory.canceled()  # 취소
    """

    class Meta:
        model = Payment

    order = factory.SubFactory(OrderFactory)
    amount = factory.LazyAttribute(lambda obj: obj.order.final_amount)
    status = "ready"
    toss_order_id = factory.LazyAttribute(lambda obj: str(obj.order.id))
    payment_key = factory.Sequence(lambda n: f"test_payment_key_{n}")
    method = ""

    @classmethod
    def ready(cls, **kwargs):
        """결제 준비 상태 (기본값)"""
        kwargs.setdefault("status", "ready")
        return cls(**kwargs)

    @classmethod
    def pending(cls, **kwargs):
        """결제 대기 상태"""
        kwargs.setdefault("status", "pending")
        return cls(**kwargs)

    @classmethod
    def done(cls, **kwargs):
        """결제 완료 상태 (일반)"""
        kwargs.setdefault("status", "done")
        kwargs.setdefault("approved_at", timezone.now())
        return cls(**kwargs)

    @classmethod
    def done_card(cls, **kwargs):
        """카드 결제 완료 상태"""
        kwargs.setdefault("status", "done")
        kwargs.setdefault("method", "카드")
        kwargs.setdefault("approved_at", timezone.now())
        kwargs.setdefault("card_company", "신한카드")
        kwargs.setdefault("card_number", "1234****")
        return cls(**kwargs)

    @classmethod
    def canceled(cls, **kwargs):
        """결제 취소 상태"""
        kwargs.setdefault("status", "canceled")
        kwargs.setdefault("is_canceled", True)
        kwargs.setdefault("canceled_at", timezone.now())
        kwargs.setdefault("cancel_reason", "사용자 요청")
        return cls(**kwargs)

    @classmethod
    def failed(cls, **kwargs):
        """결제 실패 상태"""
        kwargs.setdefault("status", "failed")
        return cls(**kwargs)

    @classmethod
    def aborted(cls, **kwargs):
        """결제 중단 상태"""
        kwargs.setdefault("status", "aborted")
        return cls(**kwargs)


# ==========================================
# Cart Factories
# ==========================================


class CartFactory(DjangoModelFactory):
    """
    Cart factory

    사용 예시:
        cart = CartFactory()  # 빈 장바구니
        cart = CartFactory.with_items([product1, product2])  # 상품 담긴 장바구니
    """

    class Meta:
        model = Cart

    user = factory.SubFactory(UserFactory, is_email_verified=True)
    is_active = True

    @classmethod
    def active(cls, **kwargs):
        """활성 장바구니 (기본값)"""
        kwargs.setdefault("is_active", True)
        return cls(**kwargs)

    @classmethod
    def inactive(cls, **kwargs):
        """비활성 장바구니"""
        kwargs.setdefault("is_active", False)
        return cls(**kwargs)

    @classmethod
    def with_items(cls, products=None, **kwargs):
        """상품이 담긴 장바구니"""
        cart = cls(**kwargs)
        if products:
            for product in products:
                CartItemFactory(cart=cart, product=product)
        else:
            # products가 없으면 기본 상품 1개 추가
            CartItemFactory(cart=cart)
        return cart


class CartItemFactory(DjangoModelFactory):
    """CartItem factory"""

    class Meta:
        model = CartItem

    cart = factory.SubFactory(CartFactory)
    product = factory.SubFactory(ProductFactory)
    quantity = 1


# ==========================================
# Point Factories
# ==========================================


class PointHistoryFactory(DjangoModelFactory):
    """
    PointHistory factory

    포인트 이력을 생성합니다.
    기본적으로 earn 타입의 이력을 생성합니다.
    """

    class Meta:
        model = PointHistory

    user = factory.SubFactory(UserFactory)
    points = TestConstants.DEFAULT_EARN_POINTS
    balance = factory.LazyAttribute(lambda obj: obj.user.points + obj.points)
    type = "earn"
    description = "테스트 적립"
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=365))

    @classmethod
    def earn(cls, **kwargs):
        """포인트 적립"""
        kwargs.setdefault("type", "earn")
        kwargs.setdefault("description", "테스트 적립")
        return cls(**kwargs)

    @classmethod
    def use(cls, **kwargs):
        """포인트 사용"""
        kwargs.setdefault("type", "use")
        kwargs.setdefault("points", -TestConstants.DEFAULT_EARN_POINTS)
        kwargs.setdefault("description", "테스트 사용")
        return cls(**kwargs)

    @classmethod
    def earn_expiring_soon(cls, days=7, **kwargs):
        """곧 만료될 적립 포인트"""
        kwargs.setdefault("type", "earn")
        kwargs.setdefault("expires_at", timezone.now() + timedelta(days=days))
        kwargs.setdefault("description", "만료 예정 적립")
        return cls(**kwargs)

    @classmethod
    def earn_expired(cls, **kwargs):
        """이미 만료된 적립 포인트"""
        kwargs.setdefault("type", "earn")
        kwargs.setdefault("expires_at", timezone.now() - timedelta(days=1))
        kwargs.setdefault("description", "만료된 적립")
        return cls(**kwargs)

    @classmethod
    def with_partial_usage(cls, used_amount=50, **kwargs):
        """부분 사용된 포인트"""
        history = cls.earn(**kwargs)
        history.metadata["used_amount"] = used_amount
        history.save(update_fields=["metadata"])
        return history


# ==========================================
# Trait 및 Helper Factory
# ==========================================


class PaidOrderFactory(OrderFactory):
    """
    결제 완료된 주문 Factory

    status='paid'이며 earned_points가 자동 계산됩니다.
    """

    status = "paid"
    payment_method = "card"
    earned_points = factory.LazyAttribute(lambda obj: int(obj.total_amount * Decimal("0.01")))  # 1% 적립


class CompletedPaymentFactory(PaymentFactory):
    """
    완료된 Payment Factory (카드 결제)

    status='done'이며 카드 결제 정보가 기본 설정됩니다.
    비카드 결제는 명시적으로 전달해야 합니다.

    사용 예시:
        # 카드 결제 완료 (기본)
        payment = CompletedPaymentFactory()

        # 계좌이체 완료 (카드 정보 제거)
        payment = CompletedPaymentFactory(
            method="계좌이체",
            card_company="",
            card_number=""
        )
    """

    status = "done"
    method = "카드"
    approved_at = factory.LazyFunction(timezone.now)
    card_company = "신한카드"
    card_number = "1234****"


class OrderWithItemsFactory(OrderFactory):
    """
    OrderItem이 포함된 Order Factory

    주문과 함께 OrderItem을 자동 생성합니다.
    """

    @factory.post_generation
    def items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            # items가 명시적으로 전달된 경우
            for item_data in extracted:
                OrderItemFactory(order=self, **item_data)
        else:
            # 기본적으로 1개의 아이템 생성
            OrderItemFactory(order=self)


# ==========================================
# Toss API 응답 빌더
# ==========================================


class TossResponseBuilder:
    """
    Toss API 응답 빌더

    재사용 가능한 Toss API 응답을 생성합니다.
    동시성 테스트를 위해 기본 payment_key는 UUID로 자동 생성됩니다.

    사용 예시:
        response = TossResponseBuilder.success_response()  # 고유 UUID 생성
        response = TossResponseBuilder.success_response(payment_key="custom_key")
        response = TossResponseBuilder.cancel_response()
        response = TossResponseBuilder.error_response("INVALID_REQUEST")
    """

    @staticmethod
    def success_response(
        payment_key=None,
        order_id="ORDER_20250115_001",
        amount=13000,
        method="카드",
        approved_at=None,
    ):
        """
        결제 승인 성공 응답

        Args:
            payment_key: 결제키 (None이면 UUID 자동 생성)
            order_id: 주문번호
            amount: 결제금액
            method: 결제수단
            approved_at: 승인시간
        """
        import uuid

        if payment_key is None:
            payment_key = f"test_key_{uuid.uuid4().hex[:16]}"

        return {
            "paymentKey": payment_key,
            "orderId": order_id,
            "status": "DONE",
            "totalAmount": amount,
            "method": method,
            "approvedAt": approved_at or TestConstants.DEFAULT_APPROVED_AT,
            "card": {
                "company": "신한카드",
                "number": "1234****5678",
                "installmentPlanMonths": 0,
                "cardType": "신용",
                "ownerType": "개인",
            },
        }

    @staticmethod
    def cancel_response(
        payment_key="test_payment_key_123",
        cancel_reason="고객 변심",
        canceled_at=None,
    ):
        """결제 취소 성공 응답"""
        return {
            "paymentKey": payment_key,
            "status": "CANCELED",
            "cancelReason": cancel_reason,
            "canceledAt": canceled_at or TestConstants.DEFAULT_CANCELED_AT,
        }

    @staticmethod
    def error_response(code="INVALID_REQUEST", message="잘못된 요청입니다."):
        """에러 응답"""
        return {"code": code, "message": message}


# ==========================================
# Webhook 데이터 빌더
# ==========================================


class WebhookDataBuilder:
    """
    웹훅 데이터 빌더

    Toss 웹훅 이벤트 데이터를 생성합니다.

    사용 예시:
        data = WebhookDataBuilder.payment_done(order_id="ORDER_001")
        data = WebhookDataBuilder.payment_canceled(order_id="ORDER_001")
    """

    @staticmethod
    def payment_done(order_id, payment_key="test_payment_key_123", amount=10000, method="카드", **kwargs):
        """PAYMENT.DONE 이벤트"""
        data = {
            "eventType": "PAYMENT.DONE",
            "data": {
                "orderId": order_id,
                "paymentKey": payment_key,
                "status": "DONE",
                "totalAmount": amount,
                "method": method,
                "approvedAt": timezone.now().isoformat(),
            },
        }

        # 카드 결제인 경우 카드 정보 추가
        if method == "카드":
            data["data"]["card"] = {
                "company": "신한카드",
                "number": "1234****",
                "installmentPlanMonths": 0,
            }

        # 추가 필드 병합
        data["data"].update(kwargs)
        return data

    @staticmethod
    def payment_canceled(order_id, payment_key="test_payment_key_123", cancel_reason="사용자 요청", **kwargs):
        """PAYMENT.CANCELED 이벤트"""
        data = {
            "eventType": "PAYMENT.CANCELED",
            "data": {
                "orderId": order_id,
                "paymentKey": payment_key,
                "status": "CANCELED",
                "cancelReason": cancel_reason,
                "canceledAt": timezone.now().isoformat(),
            },
        }
        data["data"].update(kwargs)
        return data

    @staticmethod
    def payment_failed(order_id, fail_reason="카드 한도 초과", **kwargs):
        """PAYMENT.FAILED 이벤트"""
        data = {
            "eventType": "PAYMENT.FAILED",
            "data": {
                "orderId": order_id,
                "failReason": fail_reason,
            },
        }
        data["data"].update(kwargs)
        return data


# ==========================================
# OAuth 데이터 빌더
# ==========================================


class OAuthDataBuilder:
    """
    OAuth 응답 빌더

    소셜 로그인 OAuth 응답 데이터를 생성합니다.

    사용 예시:
        data = OAuthDataBuilder.google()
        data = OAuthDataBuilder.kakao()
        data = OAuthDataBuilder.naver()
    """

    @staticmethod
    def google(email="testuser@gmail.com", user_id="google_user_id_123456"):
        """Google OAuth 응답"""
        return {
            "id": user_id,
            "email": email,
            "verified_email": True,
            "name": "Test User",
            "given_name": "Test",
            "family_name": "User",
            "picture": "https://lh3.googleusercontent.com/a/default-user",
            "locale": "ko",
        }

    @staticmethod
    def kakao(email="testuser@kakao.com", user_id=123456789):
        """Kakao OAuth 응답"""
        return {
            "id": user_id,
            "connected_at": "2025-01-28T10:00:00Z",
            "kakao_account": {
                "profile_needs_agreement": False,
                "profile": {
                    "nickname": "테스트유저",
                    "profile_image_url": "http://k.kakaocdn.net/img.jpg",
                },
                "has_email": True,
                "email_needs_agreement": False,
                "is_email_valid": True,
                "is_email_verified": True,
                "email": email,
            },
        }

    @staticmethod
    def naver(email="testuser@naver.com", user_id="naver_user_id_12345"):
        """Naver OAuth 응답"""
        return {
            "resultcode": "00",
            "message": "success",
            "response": {
                "id": user_id,
                "email": email,
                "name": "테스트",
                "nickname": "테스터",
                "profile_image": "https://ssl.pstatic.net/static/pwe/address/img_profile.png",
                "age": "20-29",
                "gender": "M",
                "birthday": "01-28",
                "birthyear": "1990",
            },
        }


# ==========================================
# 배송 정보 빌더
# ==========================================


class ShippingDataBuilder:
    """
    배송 정보 빌더

    주문 생성 시 사용하는 배송 정보를 생성합니다.

    사용 예시:
        data = ShippingDataBuilder.default()
        data = ShippingDataBuilder.remote_area()  # 제주/도서산간
        data = ShippingDataBuilder.invalid("shipping_name")  # 특정 필드 비움
    """

    @staticmethod
    def default():
        """기본 배송 정보"""
        return {
            "shipping_name": TestConstants.DEFAULT_SHIPPING_NAME,
            "shipping_phone": TestConstants.DEFAULT_SHIPPING_PHONE,
            "shipping_postal_code": TestConstants.DEFAULT_SHIPPING_POSTAL_CODE,
            "shipping_address": TestConstants.DEFAULT_SHIPPING_ADDRESS,
            "shipping_address_detail": TestConstants.DEFAULT_SHIPPING_ADDRESS_DETAIL,
            "order_memo": "부재시 경비실에 맡겨주세요",
        }

    @staticmethod
    def remote_area():
        """도서산간 배송지 (제주)"""
        return {
            "shipping_name": "김제주",
            "shipping_phone": "010-6300-0000",
            "shipping_postal_code": "63000",
            "shipping_address": "제주특별자치도 제주시 연동",
            "shipping_address_detail": "301호",
            "order_memo": "제주 배송 테스트",
        }

    @staticmethod
    def invalid(empty_field=None):
        """
        잘못된 배송 정보

        Args:
            empty_field: 빈 값으로 설정할 필드명
        """
        data = ShippingDataBuilder.default()
        if empty_field:
            data[empty_field] = ""
        else:
            # 모든 필드를 비움
            for key in data:
                data[key] = ""
        return data


# ==========================================
# 결제 요청 빌더
# ==========================================


class PaymentRequestBuilder:
    """
    결제 요청 데이터 빌더

    Toss 결제 승인 요청 데이터를 생성합니다.

    사용 예시:
        data = PaymentRequestBuilder.confirm_request(payment)
        data = PaymentRequestBuilder.confirm_request(payment, payment_key="custom_key")
    """

    @staticmethod
    def confirm_request(payment, payment_key=None):
        """
        결제 승인 요청 데이터

        Args:
            payment: Payment 객체
            payment_key: 결제 키 (미지정 시 자동 생성)
        """
        if payment_key is None:
            payment_key = f"test_key_{payment.id}"

        return {
            "order_id": payment.order.order_number,
            "payment_key": payment_key,
            "amount": int(payment.amount),
        }


# ==========================================
# Utilities
# ==========================================


# ==========================================
# Return Factories
# ==========================================


class ReturnFactory(DjangoModelFactory):
    """
    Return factory

    교환/환불 신청을 생성합니다.
    기본값은 requested 상태의 환불 신청입니다.

    사용 예시:
        return_obj = ReturnFactory()  # 환불 신청 (requested)
        return_obj = ReturnFactory.refund()  # 환불
        return_obj = ReturnFactory.exchange()  # 교환
        return_obj = ReturnFactory.approved()  # 승인됨
        return_obj = ReturnFactory.with_items([order_item1, order_item2])
    """

    class Meta:
        model = "shopping.Return"

    order = factory.SubFactory(OrderFactory, status="delivered")
    user = factory.LazyAttribute(lambda obj: obj.order.user)
    return_number = factory.Sequence(lambda n: f"RET{timezone.now().strftime('%Y%m%d')}{n:03d}")
    type = "refund"
    status = "requested"
    reason = "change_of_mind"
    reason_detail = "단순 변심으로 인한 환불 요청"

    # 환불 정보
    refund_amount = Decimal("0")
    refund_account_bank = "신한은행"
    refund_account_number = "110-123-456789"
    refund_account_holder = "홍길동"

    # 반품 배송 정보
    return_shipping_fee = Decimal("0")

    @classmethod
    def refund(cls, **kwargs):
        """환불 신청 (기본값)"""
        kwargs.setdefault("type", "refund")
        kwargs.setdefault("refund_account_bank", "신한은행")
        kwargs.setdefault("refund_account_number", "110-123-456789")
        kwargs.setdefault("refund_account_holder", "홍길동")
        return cls(**kwargs)

    @classmethod
    def exchange(cls, **kwargs):
        """교환 신청"""
        kwargs.setdefault("type", "exchange")
        kwargs.setdefault("exchange_product", factory.SubFactory(ProductFactory))
        # 교환은 계좌 정보 불필요
        kwargs.setdefault("refund_account_bank", "")
        kwargs.setdefault("refund_account_number", "")
        kwargs.setdefault("refund_account_holder", "")
        return cls(**kwargs)

    @classmethod
    def requested(cls, **kwargs):
        """신청 상태 (기본값)"""
        kwargs.setdefault("status", "requested")
        return cls(**kwargs)

    @classmethod
    def approved(cls, **kwargs):
        """승인 상태"""
        kwargs.setdefault("status", "approved")
        kwargs.setdefault("approved_at", timezone.now())
        return cls(**kwargs)

    @classmethod
    def rejected(cls, **kwargs):
        """거부 상태"""
        kwargs.setdefault("status", "rejected")
        kwargs.setdefault("rejected_reason", "상품 하자가 아님")
        return cls(**kwargs)

    @classmethod
    def shipping(cls, **kwargs):
        """반품 배송중 상태"""
        kwargs.setdefault("status", "shipping")
        kwargs.setdefault("approved_at", timezone.now())
        kwargs.setdefault("return_shipping_company", "CJ대한통운")
        kwargs.setdefault("return_tracking_number", "123456789012")
        return cls(**kwargs)

    @classmethod
    def received(cls, **kwargs):
        """반품 도착 상태"""
        kwargs.setdefault("status", "received")
        kwargs.setdefault("approved_at", timezone.now() - timedelta(days=2))
        kwargs.setdefault("return_shipping_company", "CJ대한통운")
        kwargs.setdefault("return_tracking_number", "123456789012")
        return cls(**kwargs)

    @classmethod
    def completed(cls, **kwargs):
        """완료 상태"""
        kwargs.setdefault("status", "completed")
        kwargs.setdefault("approved_at", timezone.now() - timedelta(days=3))
        kwargs.setdefault("completed_at", timezone.now())
        return cls(**kwargs)

    @classmethod
    def with_items(cls, order_items=None, **kwargs):
        """ReturnItem이 포함된 Return"""
        return_obj = cls(**kwargs)
        if order_items:
            for order_item in order_items:
                ReturnItemFactory(return_request=return_obj, order_item=order_item)
        else:
            # 기본적으로 1개의 아이템 생성
            ReturnItemFactory(return_request=return_obj)
        return return_obj

    @classmethod
    def with_shipping_fee(cls, fee=Decimal("3000"), **kwargs):
        """반품 배송비가 있는 Return"""
        kwargs.setdefault("return_shipping_fee", fee)
        return cls(**kwargs)


class ReturnItemFactory(DjangoModelFactory):
    """
    ReturnItem factory

    교환/환불 상품 항목을 생성합니다.
    """

    class Meta:
        model = "shopping.ReturnItem"

    return_request = factory.SubFactory(ReturnFactory)
    order_item = factory.LazyAttribute(lambda obj: OrderItemFactory(order=obj.return_request.order))
    quantity = 1
    product_name = factory.LazyAttribute(lambda obj: obj.order_item.product_name)
    product_price = factory.LazyAttribute(lambda obj: obj.order_item.price)


# ==========================================
# Product QA Factories
# ==========================================


class ProductQuestionFactory(DjangoModelFactory):
    """ProductQuestion factory"""

    class Meta:
        model = ProductQuestion

    product = factory.SubFactory(ProductFactory)
    user = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"상품 문의 {n}")
    content = "상품에 대해 궁금한 점이 있습니다."
    is_secret = False
    is_answered = False

    @classmethod
    def secret(cls, **kwargs):
        """비밀글"""
        kwargs.setdefault("is_secret", True)
        return cls(**kwargs)

    @classmethod
    def answered(cls, **kwargs):
        """답변 완료된 문의"""
        kwargs.setdefault("is_answered", True)
        return cls(**kwargs)


class ProductAnswerFactory(DjangoModelFactory):
    """ProductAnswer factory"""

    class Meta:
        model = ProductAnswer

    question = factory.SubFactory(ProductQuestionFactory, is_answered=True)
    seller = factory.LazyAttribute(lambda obj: obj.question.product.seller)
    content = "문의하신 내용에 대한 답변입니다."


# ==========================================
# Utilities
# ==========================================


class SKUGenerator:
    """
    SKU 생성기

    테스트 간 충돌 방지를 위한 고유 SKU 생성

    사용 예시:
        sku = SKUGenerator.generate()  # TEST-000001
        sku = SKUGenerator.generate("PROD")  # PROD-000002
    """

    counter = {"value": 0}

    @classmethod
    def generate(cls, prefix="TEST"):
        """SKU 생성"""
        cls.counter["value"] += 1
        return f"{prefix}-{cls.counter['value']:06d}"

    @classmethod
    def reset(cls):
        """카운터 리셋"""
        cls.counter["value"] = 0

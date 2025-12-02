"""장바구니 서비스 레이어

장바구니 관련 비즈니스 로직을 처리합니다.

현업에서 널리 사용되는 서비스 레이어 패턴 적용:
1. 단일 책임 원칙 (SRP): 장바구니 관련 로직만 담당
2. 트랜잭션 경계 명확화: @transaction.atomic 데코레이터로 트랜잭션 관리
3. 예외 처리 표준화: CartServiceError로 비즈니스 로직 예외 통합
4. 로깅 표준화: 구조화된 로깅으로 디버깅 및 모니터링 용이

사용 예시:
    # 아이템 추가
    cart_item = CartService.add_item(cart, product_id=1, quantity=2)

    # 일괄 추가
    result = CartService.bulk_add_items(cart, items_data=[...])

    # 재고 확인
    issues = CartService.check_stock(cart)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import F, Prefetch

if TYPE_CHECKING:
    from django.http import HttpRequest

    from ..models.user import User

from ..models.cart import Cart, CartItem
from ..models.product import Product
from .base import ServiceError, log_service_call

logger = logging.getLogger(__name__)


class CartServiceError(ServiceError):
    """장바구니 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "CART_ERROR", details: dict | None = None):
        super().__init__(message, code, details)


# ===== Data Transfer Objects (DTO) =====
# 서비스 레이어 결과를 구조화된 형태로 반환


@dataclass
class StockIssue:
    """재고 문제 정보"""

    item_id: int
    product_id: int
    product_name: str
    issue_type: str  # 'inactive', 'out_of_stock', 'insufficient'
    requested: int
    available: int


@dataclass
class PriceChange:
    """가격 변경 정보"""

    item_id: int
    product_id: int
    product_name: str
    original_price: Decimal
    current_price: Decimal
    difference: Decimal
    change_type: str  # 'increased', 'decreased'


@dataclass
class BulkAddResult:
    """일괄 추가 결과"""

    added_items: list[CartItem]
    errors: list[dict]
    success_count: int
    error_count: int


class CartService:
    """
    장바구니 관련 비즈니스 로직 서비스

    책임:
    - 장바구니 생성/조회
    - 아이템 추가/수정/삭제
    - 재고 검증
    - 일괄 처리

    Note:
        모든 메서드는 stateless하게 설계되어 있으며,
        필요한 상태는 인자로 전달받습니다.
    """

    # ===== 정책 상수 =====
    MIN_QUANTITY = 1
    MAX_QUANTITY = 999

    # ===== 장바구니 생성/조회 =====

    @staticmethod
    @log_service_call
    def get_or_create_cart(
        user: User | None = None,
        session_key: str | None = None,
        request: HttpRequest | None = None,
    ) -> Cart:
        """
        사용자 또는 세션의 활성 장바구니 조회/생성

        Args:
            user: 인증된 사용자 (회원)
            session_key: 세션 키 (비회원)
            request: HTTP 요청 객체 (세션 자동 생성용)

        Returns:
            Cart: 활성 장바구니

        Raises:
            CartServiceError: 사용자 또는 세션 정보가 없는 경우

        Note:
            - 회원: user 기반 장바구니
            - 비회원: session_key 기반 장바구니
            - request가 주어지면 세션이 없을 때 자동 생성
        """
        if user and user.is_authenticated:
            # 회원 장바구니
            cart, created = Cart.get_or_create_active_cart(user=user)
            if created:
                logger.info("[Cart] 회원 장바구니 생성 | user_id=%d, cart_id=%d", user.id, cart.id)
        elif session_key:
            # 비회원 장바구니
            cart, created = Cart.get_or_create_active_cart(session_key=session_key)
            if created:
                logger.info("[Cart] 비회원 장바구니 생성 | session=%s, cart_id=%d", session_key[:8], cart.id)
        elif request:
            # 세션이 없으면 생성
            if not request.session.session_key:
                request.session.create()
            session_key = request.session.session_key
            cart, created = Cart.get_or_create_active_cart(session_key=session_key)
            if created:
                logger.info("[Cart] 비회원 장바구니 생성 (세션 자동) | session=%s, cart_id=%d", session_key[:8], cart.id)
        else:
            raise CartServiceError(
                "사용자 또는 세션 정보가 필요합니다.",
                code="MISSING_IDENTITY",
            )

        # 성능 최적화: 관련 데이터 미리 로드
        cart = Cart.objects.prefetch_related(
            Prefetch(
                "items",
                queryset=CartItem.objects.select_related("product").order_by("-added_at"),
            )
        ).get(pk=cart.pk)

        return cart

    # ===== 아이템 추가 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def add_item(
        cart: Cart,
        product_id: int,
        quantity: int = 1,
    ) -> CartItem:
        """
        장바구니에 상품 추가

        이미 존재하는 상품이면 수량을 증가시킵니다.

        Args:
            cart: 장바구니
            product_id: 상품 ID
            quantity: 수량 (기본값: 1)

        Returns:
            CartItem: 추가/수정된 장바구니 아이템

        Raises:
            CartServiceError: 상품 없음, 재고 부족, 수량 초과 등
        """
        # 1. 수량 검증
        CartService._validate_quantity(quantity)

        # 2. 상품 조회 (장바구니 추가 시점에는 재고를 변경하지 않으므로 락 불필요)
        product = CartService._get_product(product_id)

        # 3. 장바구니 락 획득 (동시성 제어)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        # 4. 기존 아이템 확인
        existing_item = cart.items.filter(product_id=product_id).first()
        total_quantity = quantity + (existing_item.quantity if existing_item else 0)

        # 5. 재고 검증
        CartService._validate_stock(product, total_quantity)

        # 6. 아이템 생성 또는 수량 업데이트
        if existing_item:
            # 기존 아이템 수량 증가 (F 객체로 안전하게)
            # 가격은 최초 담은 시점 유지
            CartItem.objects.filter(pk=existing_item.pk).update(quantity=F("quantity") + quantity)
            existing_item.refresh_from_db()
            cart_item = existing_item
            logger.info(
                "[Cart] 아이템 수량 증가 | cart_id=%d, product_id=%d, new_qty=%d", cart.id, product_id, cart_item.quantity
            )
        else:
            # 새 아이템 생성 (가격 스냅샷 저장)
            cart_item = CartItem.objects.create(
                cart=cart,
                product=product,
                quantity=quantity,
                price_at_add=product.price,
            )
            logger.info("[Cart] 아이템 추가 | cart_id=%d, product_id=%d, qty=%d, price=%s", cart.id, product_id, quantity, product.price)

        return cart_item

    @staticmethod
    @log_service_call
    @transaction.atomic
    def update_item_quantity(cart: Cart, item_id: int, quantity: int) -> CartItem | None:
        """
        장바구니 아이템 수량 변경

        수량이 0이면 아이템을 삭제합니다.

        Args:
            cart: 장바구니
            item_id: 장바구니 아이템 ID
            quantity: 새로운 수량

        Returns:
            CartItem: 수정된 아이템 (삭제된 경우 None)

        Raises:
            CartServiceError: 아이템 없음, 재고 부족 등
        """
        # 수량이 0이면 삭제
        if quantity == 0:
            CartService.remove_item(cart, item_id)
            return None

        # 수량 검증
        CartService._validate_quantity(quantity)

        # 아이템 조회 (동시성 제어)
        try:
            cart_item = CartItem.objects.select_for_update().get(
                pk=item_id,
                cart=cart,
            )
        except CartItem.DoesNotExist:
            raise CartServiceError(
                "장바구니에 해당 상품이 없습니다.",
                code="ITEM_NOT_FOUND",
            )

        # 재고 검증
        CartService._validate_stock(cart_item.product, quantity)

        # 수량 업데이트
        cart_item.quantity = quantity
        cart_item.save(update_fields=["quantity", "added_at"])

        logger.info("[Cart] 수량 변경 | cart_id=%d, item_id=%d, new_qty=%d", cart.id, item_id, quantity)

        return cart_item

    @staticmethod
    @log_service_call
    @transaction.atomic
    def remove_item(cart: Cart, item_id: int) -> str:
        """
        장바구니 아이템 삭제

        Args:
            cart: 장바구니
            item_id: 장바구니 아이템 ID

        Returns:
            str: 삭제된 상품명

        Raises:
            CartServiceError: 아이템 없음
        """
        try:
            cart_item = cart.items.get(pk=item_id)
        except CartItem.DoesNotExist:
            raise CartServiceError(
                "장바구니에 해당 상품이 없습니다.",
                code="ITEM_NOT_FOUND",
            )

        product_name = cart_item.product.name
        cart_item.delete()

        logger.info("[Cart] 아이템 삭제 | cart_id=%d, item_id=%d, product=%s", cart.id, item_id, product_name)

        return product_name

    @staticmethod
    @log_service_call
    @transaction.atomic
    def clear_cart(cart: Cart) -> int:
        """
        장바구니 비우기

        Args:
            cart: 장바구니

        Returns:
            int: 삭제된 아이템 수

        Raises:
            CartServiceError: 이미 비어있는 경우
        """
        item_count = cart.items.count()

        if item_count == 0:
            raise CartServiceError(
                "장바구니가 이미 비어있습니다.",
                code="CART_EMPTY",
            )

        cart.items.all().delete()

        logger.info("[Cart] 장바구니 비우기 | cart_id=%d, deleted=%d", cart.id, item_count)

        return item_count

    # ===== 일괄 처리 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def bulk_add_items(cart: Cart, items_data: list[dict]) -> BulkAddResult:
        """
        여러 상품을 한 번에 장바구니에 추가 (N+1 쿼리 최적화)

        일부 실패해도 성공한 항목은 추가됩니다.

        Args:
            cart: 장바구니
            items_data: 추가할 상품 목록
                [{"product_id": 1, "quantity": 2}, ...]

        Returns:
            BulkAddResult: 추가 결과

        Raises:
            CartServiceError: 상품 정보가 없는 경우
        """
        if not items_data:
            raise CartServiceError(
                "추가할 상품 정보가 없습니다.",
                code="EMPTY_ITEMS",
            )

        # 1. 상품 ID 수집 및 일괄 조회 (N+1 방지)
        product_ids = [item.get("product_id") for item in items_data if item.get("product_id")]
        products = {p.id: p for p in Product.objects.filter(id__in=product_ids, is_active=True)}

        added_items: list[CartItem] = []
        errors: list[dict] = []

        # 2. 장바구니 락 획득 (동시성 제어)
        cart = Cart.objects.select_for_update().get(pk=cart.pk)

        # 3. 각 아이템 처리
        for idx, item_data in enumerate(items_data):
            product_id = item_data.get("product_id")
            quantity = item_data.get("quantity", 1)

            # 검증
            error = CartService._validate_bulk_item(idx, product_id, quantity, products)
            if error:
                errors.append(error)
                continue

            product = products[product_id]

            # 재고 검증
            if product.stock < quantity:
                errors.append(
                    {
                        "index": idx,
                        "product_id": product_id,
                        "errors": {"quantity": f"재고 부족. 현재 재고: {product.stock}개"},
                    }
                )
                continue

            # 아이템 추가/업데이트
            try:
                cart_item = CartService._add_or_update_item(cart, product_id, quantity)
                added_items.append(cart_item)
            except Exception as e:
                errors.append(
                    {
                        "index": idx,
                        "product_id": product_id,
                        "errors": {"detail": str(e)},
                    }
                )

        logger.info("[Cart] 일괄 추가 완료 | cart_id=%d, added=%d, errors=%d", cart.id, len(added_items), len(errors))

        return BulkAddResult(
            added_items=added_items,
            errors=errors,
            success_count=len(added_items),
            error_count=len(errors),
        )

    # ===== 재고 확인 =====

    @staticmethod
    @log_service_call
    def check_stock(cart: Cart) -> list[StockIssue]:
        """
        장바구니 상품들의 재고 확인

        주문 직전 재고 확인에 사용합니다.

        Args:
            cart: 장바구니

        Returns:
            list[StockIssue]: 재고 문제 목록 (문제 없으면 빈 리스트)
        """
        issues: list[StockIssue] = []

        for item in cart.items.select_related("product"):
            product = item.product

            if not product.is_active:
                issues.append(
                    StockIssue(
                        item_id=item.id,
                        product_id=product.id,
                        product_name=product.name,
                        issue_type="inactive",
                        requested=item.quantity,
                        available=0,
                    )
                )
            elif product.stock == 0:
                issues.append(
                    StockIssue(
                        item_id=item.id,
                        product_id=product.id,
                        product_name=product.name,
                        issue_type="out_of_stock",
                        requested=item.quantity,
                        available=0,
                    )
                )
            elif product.stock < item.quantity:
                issues.append(
                    StockIssue(
                        item_id=item.id,
                        product_id=product.id,
                        product_name=product.name,
                        issue_type="insufficient",
                        requested=item.quantity,
                        available=product.stock,
                    )
                )

        if issues:
            logger.warning("[Cart] 재고 문제 발견 | cart_id=%d, issues=%d", cart.id, len(issues))

        return issues

    @staticmethod
    def get_stock_issue_message(issue_type: str) -> str:
        """재고 문제 유형에 대한 메시지 반환"""
        messages = {
            "inactive": "판매 중단",
            "out_of_stock": "품절",
            "insufficient": "재고 부족",
        }
        return messages.get(issue_type, "알 수 없는 문제")

    # ===== 가격 변경 확인 =====

    @staticmethod
    @log_service_call
    def check_price_changes(cart: Cart) -> list[PriceChange]:
        """
        장바구니 상품들의 가격 변경 확인

        Args:
            cart: 장바구니

        Returns:
            list[PriceChange]: 가격 변경된 상품 목록 (변경 없으면 빈 리스트)
        """
        changes: list[PriceChange] = []

        for item in cart.items.select_related("product"):
            if item.is_price_changed:
                changes.append(
                    PriceChange(
                        item_id=item.id,
                        product_id=item.product.id,
                        product_name=item.product.name,
                        original_price=item.price_at_add,
                        current_price=item.product.price,
                        difference=item.price_difference,
                        change_type="increased" if item.price_difference > 0 else "decreased",
                    )
                )

        if changes:
            logger.info("[Cart] 가격 변경 감지 | cart_id=%d, changes=%d", cart.id, len(changes))

        return changes

    @staticmethod
    @log_service_call
    @transaction.atomic
    def update_item_prices(cart: Cart) -> int:
        """
        장바구니 상품 가격을 현재 가격으로 업데이트
        (사용자가 가격 변경을 확인하고 동의한 후 호출)

        Args:
            cart: 장바구니

        Returns:
            int: 업데이트된 아이템 수
        """
        updated_count = 0

        for item in cart.items.select_related("product"):
            if item.is_price_changed:
                item.price_at_add = item.product.price
                item.save(update_fields=["price_at_add"])
                updated_count += 1

        if updated_count:
            logger.info("[Cart] 가격 업데이트 완료 | cart_id=%d, updated=%d", cart.id, updated_count)

        return updated_count

    # ===== 장바구니 병합 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def merge_anonymous_cart(user: User, session_key: str) -> int:
        """
        비회원 장바구니를 회원 장바구니로 병합

        로그인 시 호출하여 비회원 시절에 담은 상품을 회원 장바구니로 이전합니다.

        Args:
            user: 로그인한 사용자
            session_key: 비회원 세션 키

        Returns:
            int: 병합된 아이템 수
        """
        if not session_key:
            return 0

        # 비회원 장바구니 조회
        try:
            anonymous_cart = Cart.objects.get(session_key=session_key, is_active=True)
        except Cart.DoesNotExist:
            return 0

        if not anonymous_cart.items.exists():
            return 0

        # 회원 장바구니 조회/생성
        user_cart, _ = Cart.get_or_create_active_cart(user=user)

        merged_count = 0

        for item in anonymous_cart.items.all():
            # 기존 아이템 확인
            existing = user_cart.items.filter(product_id=item.product_id).first()

            if existing:
                # 수량 합산
                existing.quantity += item.quantity
                existing.save(update_fields=["quantity", "added_at"])
            else:
                # 새 아이템으로 이전
                item.cart = user_cart
                item.save(update_fields=["cart", "added_at"])

            merged_count += 1

        # 비회원 장바구니 비활성화
        anonymous_cart.is_active = False
        anonymous_cart.save(update_fields=["is_active"])

        logger.info(
            "[Cart] 장바구니 병합 완료 | user_id=%d, merged=%d, from_cart=%d", user.id, merged_count, anonymous_cart.id
        )

        return merged_count

    # ===== Private Helper Methods =====

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        """수량 유효성 검증"""
        if not isinstance(quantity, int) or quantity < CartService.MIN_QUANTITY:
            raise CartServiceError(
                f"수량은 {CartService.MIN_QUANTITY} 이상이어야 합니다.",
                code="INVALID_QUANTITY",
            )

        if quantity > CartService.MAX_QUANTITY:
            raise CartServiceError(
                f"수량은 {CartService.MAX_QUANTITY} 이하여야 합니다.",
                code="QUANTITY_EXCEEDED",
            )

    @staticmethod
    def _get_product(product_id: int) -> Product:
        """상품 조회"""
        try:
            product = Product.objects.get(
                id=product_id,
                is_active=True,
            )
        except Product.DoesNotExist:
            raise CartServiceError(
                "상품을 찾을 수 없거나 판매 중단되었습니다.",
                code="PRODUCT_NOT_FOUND",
            )
        return product

    @staticmethod
    def _validate_stock(product: Product, quantity: int) -> None:
        """재고 유효성 검증"""
        if product.stock < quantity:
            raise CartServiceError(
                f"재고가 부족합니다. 현재 재고: {product.stock}개",
                code="INSUFFICIENT_STOCK",
                details={
                    "product_id": product.id,
                    "requested": quantity,
                    "available": product.stock,
                },
            )

    @staticmethod
    def _validate_bulk_item(
        idx: int,
        product_id: int | None,
        quantity: int,
        products: dict[int, Product],
    ) -> dict | None:
        """일괄 추가 시 개별 아이템 검증"""
        if not product_id:
            return {
                "index": idx,
                "product_id": None,
                "errors": {"product_id": "상품 ID가 필요합니다."},
            }

        if product_id not in products:
            return {
                "index": idx,
                "product_id": product_id,
                "errors": {"product_id": "상품을 찾을 수 없거나 판매 중단되었습니다."},
            }

        if not isinstance(quantity, int) or quantity < CartService.MIN_QUANTITY:
            return {
                "index": idx,
                "product_id": product_id,
                "errors": {"quantity": f"수량은 {CartService.MIN_QUANTITY} 이상이어야 합니다."},
            }

        if quantity > CartService.MAX_QUANTITY:
            return {
                "index": idx,
                "product_id": product_id,
                "errors": {"quantity": f"수량은 {CartService.MAX_QUANTITY} 이하여야 합니다."},
            }

        return None

    @staticmethod
    def _add_or_update_item(cart: Cart, product_id: int, quantity: int) -> CartItem:
        """아이템 추가 또는 수량 업데이트 (내부용)"""
        cart_item = cart.items.filter(product_id=product_id).select_for_update().first()

        if cart_item:
            # 기존 아이템 수량 증가 (가격은 최초 담은 시점 유지)
            CartItem.objects.filter(pk=cart_item.pk).update(quantity=F("quantity") + quantity)
            cart_item.refresh_from_db()
        else:
            # 새 아이템 생성 (가격 스냅샷 저장)
            product = Product.objects.get(pk=product_id)
            cart_item = CartItem.objects.create(
                cart=cart,
                product_id=product_id,
                quantity=quantity,
                price_at_add=product.price,
            )

        return cart_item

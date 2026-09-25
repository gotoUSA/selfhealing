"""위시리스트(찜) 서비스 레이어

위시리스트 관련 비즈니스 로직을 처리합니다.

현업에서 널리 사용되는 서비스 레이어 패턴 적용:
1. 단일 책임 원칙 (SRP): 위시리스트 관련 로직만 담당
2. 트랜잭션 경계 명확화: @transaction.atomic 데코레이터로 트랜잭션 관리
3. 예외 처리 표준화: WishlistServiceError로 비즈니스 로직 예외 통합
4. 로깅 표준화: 구조화된 로깅으로 디버깅 및 모니터링 용이

사용 예시:
    # 찜하기 토글
    result = WishlistService.toggle(user, product_id=1)

    # 일괄 추가
    result = WishlistService.bulk_add(user, product_ids=[1, 2, 3])

    # 통계 조회
    stats = WishlistService.get_stats(user)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Case, Count, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ..models.user import User

from ..models.cart import Cart, CartItem
from ..models.product import Product
from .base import ServiceError, log_service_call
from .product_query_service import wishlist_count_subquery

logger = logging.getLogger(__name__)


class WishlistServiceError(ServiceError):
    """위시리스트 서비스 관련 에러"""

    def __init__(self, message: str, code: str = "WISHLIST_ERROR", details: dict | None = None):
        super().__init__(message, code, details)


# ===== Data Transfer Objects (DTO) =====


@dataclass
class ToggleResult:
    """찜하기 토글 결과"""

    is_wished: bool
    message: str
    wishlist_count: int  # 해당 상품의 전체 찜 수


@dataclass
class BulkAddResult:
    """일괄 추가 결과"""

    added_count: int
    skipped_count: int  # 이미 찜한 상품 수
    total_wishlist_count: int


@dataclass
class WishlistStats:
    """위시리스트 통계"""

    total_count: int
    available_count: int
    out_of_stock_count: int
    on_sale_count: int
    total_price: Decimal
    total_sale_price: Decimal
    total_discount: Decimal


@dataclass
class MoveToCartResult:
    """장바구니 이동 결과"""

    added_items: list[str] = field(default_factory=list)
    already_in_cart: list[str] = field(default_factory=list)
    out_of_stock: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class WishlistFilter:
    """위시리스트 필터 옵션"""

    is_available: bool | None = None  # 구매 가능 상품만
    on_sale: bool | None = None  # 세일 중인 상품만
    ordering: str = "-created_at"


class WishlistService:
    """
    위시리스트(찜) 관련 비즈니스 로직 서비스

    책임:
    - 찜하기 토글 (추가/제거)
    - 일괄 추가/삭제
    - 찜 목록 조회 및 필터링
    - 통계 계산
    - 장바구니 이동

    Note:
        모든 메서드는 stateless하게 설계되어 있으며,
        필요한 상태는 인자로 전달받습니다.
    """

    # ===== 정책 상수 =====
    VALID_ORDERINGS = ["created_at", "-created_at", "price", "-price", "name"]

    # ===== 찜하기 토글 =====

    @staticmethod
    @log_service_call
    def toggle(user: User, product_id: int) -> ToggleResult:
        """
        찜하기 토글 (추가/제거)

        Args:
            user: 사용자
            product_id: 상품 ID

        Returns:
            ToggleResult: 토글 결과

        Raises:
            WishlistServiceError: 상품이 없는 경우
        """
        product = WishlistService._get_product(product_id)

        if user.is_in_wishlist(product):
            user.remove_from_wishlist(product)
            is_wished = False
            message = "찜 목록에서 제거되었습니다."
            logger.info("[Wishlist] 찜 제거 | user_id=%d, product_id=%d", user.id, product_id)
        else:
            user.add_to_wishlist(product)
            is_wished = True
            message = "찜 목록에 추가되었습니다."
            logger.info("[Wishlist] 찜 추가 | user_id=%d, product_id=%d", user.id, product_id)

        wishlist_count = product.wished_by_users.count()

        return ToggleResult(
            is_wished=is_wished,
            message=message,
            wishlist_count=wishlist_count,
        )

    # ===== 찜 추가 =====

    @staticmethod
    @log_service_call
    def add(user: User, product_id: int) -> tuple[bool, str, int]:
        """
        찜 목록에 상품 추가

        Args:
            user: 사용자
            product_id: 상품 ID

        Returns:
            tuple: (is_new, message, wishlist_count)
                - is_new: 새로 추가된 경우 True
                - message: 결과 메시지
                - wishlist_count: 해당 상품의 전체 찜 수

        Raises:
            WishlistServiceError: 상품이 없는 경우
        """
        product = WishlistService._get_product(product_id)

        if user.is_in_wishlist(product):
            return False, "이미 찜한 상품입니다.", product.wished_by_users.count()

        user.add_to_wishlist(product)
        logger.info("[Wishlist] 찜 추가 | user_id=%d, product_id=%d", user.id, product_id)

        return True, "찜 목록에 추가되었습니다.", product.wished_by_users.count()

    # ===== 찜 제거 =====

    @staticmethod
    @log_service_call
    def remove(user: User, product_id: int) -> str:
        """
        찜 목록에서 상품 제거

        Args:
            user: 사용자
            product_id: 상품 ID

        Returns:
            str: 제거된 상품명

        Raises:
            WishlistServiceError: 상품이 없거나 찜 목록에 없는 경우
        """
        product = WishlistService._get_product(product_id)

        if not user.is_in_wishlist(product):
            raise WishlistServiceError(
                "찜 목록에 없는 상품입니다.",
                code="NOT_IN_WISHLIST",
                details={"product_id": product_id},
            )

        user.remove_from_wishlist(product)
        logger.info("[Wishlist] 찜 제거 | user_id=%d, product_id=%d", user.id, product_id)

        return product.name

    # ===== 일괄 추가 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def bulk_add(user: User, product_ids: list[int]) -> BulkAddResult:
        """
        여러 상품을 한 번에 찜 목록에 추가

        Args:
            user: 사용자
            product_ids: 상품 ID 목록

        Returns:
            BulkAddResult: 일괄 추가 결과

        Note:
            중복된 상품은 자동으로 스킵됩니다.
        """
        if not product_ids:
            raise WishlistServiceError(
                "추가할 상품이 없습니다.",
                code="EMPTY_PRODUCT_IDS",
            )

        # 현재 찜한 상품 ID 집합
        current_wishlist_ids = set(user.wishlist_products.values_list("id", flat=True))

        # 새로 추가할 상품과 스킵할 상품 분류
        products_to_add_ids = [pid for pid in product_ids if pid not in current_wishlist_ids]
        skipped_count = len(product_ids) - len(products_to_add_ids)

        # 새 상품들 추가 (N+1 방지: 단일 쿼리)
        added_count = 0
        if products_to_add_ids:
            products = Product.objects.filter(id__in=products_to_add_ids, is_active=True)
            user.wishlist_products.add(*products)
            added_count = products.count()

        logger.info(
            "[Wishlist] 일괄 추가 | user_id=%d, added=%d, skipped=%d",
            user.id,
            added_count,
            skipped_count,
        )

        return BulkAddResult(
            added_count=added_count,
            skipped_count=skipped_count,
            total_wishlist_count=user.get_wishlist_count(),
        )

    # ===== 전체 삭제 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def clear(user: User) -> int:
        """
        찜 목록 전체 삭제

        Args:
            user: 사용자

        Returns:
            int: 삭제된 상품 수

        Raises:
            WishlistServiceError: 이미 비어있는 경우
        """
        count = user.get_wishlist_count()

        if count == 0:
            raise WishlistServiceError(
                "찜 목록이 이미 비어있습니다.",
                code="WISHLIST_EMPTY",
            )

        user.clear_wishlist()
        logger.info("[Wishlist] 전체 삭제 | user_id=%d, deleted=%d", user.id, count)

        return count

    # ===== 찜 상태 확인 =====

    @staticmethod
    @log_service_call
    def check(user: User, product_id: int) -> dict:
        """
        특정 상품의 찜 상태 확인

        Args:
            user: 사용자
            product_id: 상품 ID

        Returns:
            dict: 찜 상태 정보

        Raises:
            WishlistServiceError: 상품이 없는 경우
        """
        product = WishlistService._get_product(product_id)

        return {
            "product_id": product.id,
            "is_wished": user.is_in_wishlist(product),
            "wishlist_count": product.wished_by_users.count(),
        }

    # ===== 찜 목록 조회 =====

    @staticmethod
    @log_service_call
    def get_list(user: User, filters: WishlistFilter | None = None) -> QuerySet:
        """
        찜 목록 조회 (필터링 및 정렬)

        Args:
            user: 사용자
            filters: 필터 옵션

        Returns:
            QuerySet: 필터링/정렬된 상품 쿼리셋
        """
        filters = filters or WishlistFilter()

        # 찜 수는 서브쿼리로, 이미지는 한 번에 — 상품마다 따로 세거나 읽지 않는다
        queryset = (
            user.wishlist_products.select_related("category")
            .prefetch_related("images")
            .annotate(wishlist_cnt=wishlist_count_subquery())
        )

        # 구매 가능 필터
        if filters.is_available is True:
            queryset = queryset.filter(stock__gt=0, is_active=True)
        elif filters.is_available is False:
            queryset = queryset.filter(Q(stock=0) | Q(is_active=False))

        # 세일 필터
        if filters.on_sale is True:
            queryset = queryset.filter(
                compare_price__isnull=False,
                compare_price__gt=0,
            ).extra(where=["compare_price > price"])

        # 정렬
        if filters.ordering in WishlistService.VALID_ORDERINGS:
            queryset = queryset.order_by(filters.ordering)

        return queryset

    # ===== 통계 조회 =====

    @staticmethod
    @log_service_call
    def get_stats(user: User) -> WishlistStats:
        """
        찜 목록 통계 조회 (DB 집계 쿼리 최적화)

        Args:
            user: 사용자

        Returns:
            WishlistStats: 통계 정보

        Note:
            성능 최적화: Python 루프 대신 DB 집계 쿼리 사용
            - 기존: N개 상품을 Python에서 순회 (O(N))
            - 개선: 단일 DB 쿼리로 집계 (O(1) DB 호출)
        """
        products = user.wishlist_products.all()

        # 단일 쿼리로 모든 통계 집계
        stats = products.aggregate(
            # 기본 카운트
            total_count=Count("id"),
            available_count=Count("id", filter=Q(stock__gt=0, is_active=True)),
            out_of_stock_count=Count("id", filter=Q(stock=0) | Q(is_active=False)),
            # 세일 상품 카운트 (compare_price가 있고 price보다 큰 경우)
            on_sale_count=Count(
                "id",
                filter=Q(compare_price__isnull=False) & Q(compare_price__gt=F("price")),
            ),
            # 가격 합계 - 세일 상품은 compare_price, 일반 상품은 price
            total_price=Coalesce(
                Sum(
                    Case(
                        When(
                            compare_price__isnull=False,
                            compare_price__gt=F("price"),
                            then=F("compare_price"),
                        ),
                        default=F("price"),
                    )
                ),
                Value(Decimal("0")),
            ),
            # 실제 결제 금액 (할인가)
            total_sale_price=Coalesce(Sum("price"), Value(Decimal("0"))),
            # 할인 금액 합계
            total_discount=Coalesce(
                Sum(
                    Case(
                        When(
                            compare_price__isnull=False,
                            compare_price__gt=F("price"),
                            then=F("compare_price") - F("price"),
                        ),
                        default=Value(Decimal("0")),
                    )
                ),
                Value(Decimal("0")),
            ),
        )

        return WishlistStats(
            total_count=stats["total_count"] or 0,
            available_count=stats["available_count"] or 0,
            out_of_stock_count=stats["out_of_stock_count"] or 0,
            on_sale_count=stats["on_sale_count"] or 0,
            total_price=stats["total_price"] or Decimal("0"),
            total_sale_price=stats["total_sale_price"] or Decimal("0"),
            total_discount=stats["total_discount"] or Decimal("0"),
        )

    # ===== 장바구니로 이동 =====

    @staticmethod
    @log_service_call
    @transaction.atomic
    def move_to_cart(
        user: User,
        product_ids: list[int],
        remove_from_wishlist: bool = False,
    ) -> MoveToCartResult:
        """
        찜 목록에서 장바구니로 이동

        Args:
            user: 사용자
            product_ids: 이동할 상품 ID 목록
            remove_from_wishlist: 장바구니 추가 후 찜 목록에서 제거 여부

        Returns:
            MoveToCartResult: 이동 결과

        Raises:
            WishlistServiceError: 상품이 없거나 찜 목록에 없는 경우
        """
        if not product_ids:
            raise WishlistServiceError(
                "상품을 선택해주세요.",
                code="EMPTY_PRODUCT_IDS",
            )

        # 찜 목록에 있는 상품만 필터링
        products = user.wishlist_products.filter(id__in=product_ids)

        if not products.exists():
            user_wishlist_ids = list(user.wishlist_products.values_list("id", flat=True))
            raise WishlistServiceError(
                "찜 목록에 해당 상품이 없습니다.",
                code="PRODUCTS_NOT_IN_WISHLIST",
                details={
                    "requested_ids": product_ids,
                    "user_wishlist_ids": user_wishlist_ids,
                },
            )

        # 장바구니 가져오기 또는 생성
        cart, _ = Cart.get_or_create_active_cart(user)

        result = MoveToCartResult()

        for product in products:
            # 재고 확인
            if product.stock <= 0:
                result.out_of_stock.append(product.name)
                continue

            # 장바구니에 추가
            cart_item, created = CartItem.objects.get_or_create(
                cart=cart,
                product=product,
                defaults={"quantity": 1},
            )

            if created:
                result.added_items.append(product.name)
            else:
                result.already_in_cart.append(product.name)

        # 찜 목록에서 제거 옵션
        if remove_from_wishlist and result.added_items:
            moved_products = Product.objects.filter(name__in=result.added_items)
            user.wishlist_products.remove(*moved_products)

        # 결과 메시지 생성
        result.message = WishlistService._build_move_to_cart_message(result)

        logger.info(
            "[Wishlist] 장바구니 이동 | user_id=%d, added=%d, skipped=%d, out_of_stock=%d",
            user.id,
            len(result.added_items),
            len(result.already_in_cart),
            len(result.out_of_stock),
        )

        return result

    # ===== Private Helper Methods =====

    @staticmethod
    def _get_product(product_id: int) -> Product:
        """상품 조회"""
        try:
            return Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise WishlistServiceError(
                "상품을 찾을 수 없습니다.",
                code="PRODUCT_NOT_FOUND",
                details={"product_id": product_id},
            )

    @staticmethod
    def _build_move_to_cart_message(result: MoveToCartResult) -> str:
        """장바구니 이동 결과 메시지 생성"""
        parts = []

        if result.added_items:
            parts.append(f"{len(result.added_items)}개 상품이 장바구니에 추가되었습니다.")
        if result.already_in_cart:
            parts.append(f"{len(result.already_in_cart)}개 상품은 이미 장바구니에 있습니다.")
        if result.out_of_stock:
            parts.append(f"{len(result.out_of_stock)}개 상품은 품절입니다.")

        return " ".join(parts) if parts else "처리할 상품이 없습니다."

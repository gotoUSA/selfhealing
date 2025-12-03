"""
상품 필터링 파라미터 DTO
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rest_framework.request import Request


@dataclass(frozen=True)
class ProductFilterParams:
    """
    상품 필터링 파라미터 데이터 클래스

    View에서 Request를 파싱하여 서비스 레이어로 전달하는 DTO입니다.
    frozen=True로 불변 객체로 생성하여 안전성을 보장합니다.

    Attributes:
        category_id: 카테고리 ID (하위 카테고리 포함 필터링)
        min_price: 최소 가격
        max_price: 최대 가격
        in_stock: 재고 여부 (True: 재고 있음, False: 재고 없음, None: 필터 없음)
        seller_id: 판매자 ID
    """

    category_id: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    in_stock: bool | None = None
    seller_id: int | None = None

    @classmethod
    def from_request(cls, request: Request) -> ProductFilterParams:
        """
        Request 객체에서 필터 파라미터를 추출하여 DTO 생성

        Args:
            request: DRF Request 객체

        Returns:
            ProductFilterParams: 필터 파라미터 DTO
        """
        query_params = request.query_params

        # 카테고리 ID 파싱
        category_id = cls._parse_int(query_params.get("category"))

        # 가격 범위 파싱
        min_price = cls._parse_int(query_params.get("min_price"))
        max_price = cls._parse_int(query_params.get("max_price"))

        # 재고 상태 파싱
        in_stock = cls._parse_bool(query_params.get("in_stock"))

        # 판매자 ID 파싱
        seller_id = cls._parse_int(query_params.get("seller"))

        return cls(
            category_id=category_id,
            min_price=min_price,
            max_price=max_price,
            in_stock=in_stock,
            seller_id=seller_id,
        )

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        """문자열을 정수로 파싱, 실패 시 None 반환"""
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_bool(value: str | None) -> bool | None:
        """문자열을 불리언으로 파싱, 실패 시 None 반환"""
        if value is None:
            return None
        lower_value = value.lower()
        if lower_value == "true":
            return True
        elif lower_value == "false":
            return False
        return None

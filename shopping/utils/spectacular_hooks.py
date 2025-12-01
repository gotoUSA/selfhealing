"""
drf-spectacular 태그 정리 hooks

Swagger 문서의 태그를 일관성 있게 정리합니다.
- 소문자 태그 → 대문자로 변환
- 중복 태그 통합
- 불필요한 엔드포인트 제외
"""

# 태그 변환 맵 (소문자/한글 → 영문 대문자)
TAG_MAPPING = {
    # 소문자 → 대문자
    "auth": "Auth",
    "orders": "Orders",
    "products": "Products",
    "categories": "Categories",
    "cart": "Cart",
    "wishlist": "Wishlist",
    "payments": "Payments",
    "points": "Points",
    "notifications": "Notifications",
    "webhooks": "Webhooks",
    # 한글 → 영문
    "인증": "Auth",
    "주문": "Orders",
    "상품": "Products",
    "장바구니": "Cart",
    "찜하기": "Wishlist",
    "결제": "Payments",
    "포인트": "Points",
    "알림": "Notifications",
    # 태그 통합
    "Product QA": "Product Q&A",
}


def preprocess_exclude_endpoints(endpoints: list) -> list:
    """
    스키마 생성 전 특정 엔드포인트 제외

    Args:
        endpoints: [(path, path_regex, method, callback), ...] 형태

    Returns:
        필터링된 endpoints
    """
    # 제외할 패턴들
    exclude_patterns = [
        # dj-rest-auth 기본 엔드포인트 중 사용하지 않는 것들
        "/api/auth/social/registration/",
        "/api/auth/social/password/reset/",
        "/api/auth/social/password/change/",
    ]

    filtered = []
    for endpoint in endpoints:
        path = endpoint[0]
        # 제외 패턴에 해당하지 않으면 포함
        if not any(pattern in path for pattern in exclude_patterns):
            filtered.append(endpoint)

    return filtered


def postprocess_tags(result: dict, generator, request, public) -> dict:
    """
    스키마 생성 후 태그 정리

    - 소문자 태그를 대문자로 변환
    - 한글 태그를 영문으로 통합
    - 중복 태그 제거

    Args:
        result: OpenAPI 스키마 딕셔너리
        generator: SchemaGenerator 인스턴스
        request: HttpRequest
        public: bool

    Returns:
        수정된 스키마
    """
    if "paths" not in result:
        return result

    # 각 경로의 태그 변환
    for path, methods in result["paths"].items():
        for method, operation in methods.items():
            if isinstance(operation, dict) and "tags" in operation:
                # 태그 변환
                new_tags = []
                for tag in operation["tags"]:
                    # 매핑 테이블에 있으면 변환, 없으면 그대로
                    new_tag = TAG_MAPPING.get(tag, tag)
                    if new_tag not in new_tags:
                        new_tags.append(new_tag)
                operation["tags"] = new_tags

    # 전체 tags 목록에서 중복/소문자 제거
    if "tags" in result:
        seen = set()
        unique_tags = []
        for tag in result["tags"]:
            tag_name = tag.get("name", "")
            # 매핑 테이블로 변환
            mapped_name = TAG_MAPPING.get(tag_name, tag_name)

            if mapped_name not in seen:
                seen.add(mapped_name)
                tag["name"] = mapped_name
                unique_tags.append(tag)

        result["tags"] = unique_tags

    return result

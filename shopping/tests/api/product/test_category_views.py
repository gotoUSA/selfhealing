"""
CategoryViewSet 테스트

테스트 범위:
- 카테고리 트리 조회 (계층 구조)
- 카테고리별 상품 목록 조회 (하위 카테고리 포함)
"""

from django.core.cache import cache
from django.urls import reverse

import pytest
from rest_framework import status

from shopping.tests.factories import CategoryFactory, ProductFactory


@pytest.mark.django_db
class TestCategoryViews:
    """카테고리 조회 기능 테스트"""

    def test_get_category_tree(self, api_client):
        """계층 구조로 카테고리 트리 조회"""
        # Arrange
        cache.delete("category_tree_v2")  # 캐시 초기화
        parent = CategoryFactory(name="전자제품", parent=None)
        child1 = CategoryFactory(name="컴퓨터", parent=parent)
        child2 = CategoryFactory(name="스마트폰", parent=parent)
        CategoryFactory(name="노트북", parent=child1)

        url = reverse("category-tree")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)

        # 최상위 카테고리 확인
        root_names = [cat["name"] for cat in response.data]
        assert "전자제품" in root_names

        # 하위 카테고리 구조 확인
        electronics = next(cat for cat in response.data if cat["name"] == "전자제품")
        child_names = [child["name"] for child in electronics["children"]]
        assert "컴퓨터" in child_names
        assert "스마트폰" in child_names

    def test_list_products_by_category(self, api_client):
        """특정 카테고리의 상품 목록 조회"""
        # Arrange
        category = CategoryFactory(name="의류")
        ProductFactory(category=category, name="티셔츠")
        ProductFactory(category=category, name="청바지")

        other_category = CategoryFactory(name="식품")
        ProductFactory(category=other_category, name="과자")

        url = reverse("category-products", kwargs={"pk": category.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        product_names = [p["name"] for p in response.data["results"]]
        assert "티셔츠" in product_names
        assert "청바지" in product_names
        assert "과자" not in product_names

    def test_list_products_includes_subcategories(self, api_client):
        """상위 카테고리 조회 시 하위 카테고리 상품 포함"""
        # Arrange
        parent = CategoryFactory(name="전자제품", parent=None)
        child = CategoryFactory(name="컴퓨터", parent=parent)

        ProductFactory(category=parent, name="충전기")
        ProductFactory(category=child, name="노트북")

        url = reverse("category-products", kwargs={"pk": parent.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        product_names = [p["name"] for p in response.data["results"]]
        assert "충전기" in product_names
        assert "노트북" in product_names

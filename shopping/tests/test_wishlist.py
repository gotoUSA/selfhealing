from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from shopping.models.product import Category, Product
from shopping.models.user import User


class WishlistTestCase(TestCase):
    """찜하기 기능 테스트"""

    def setUp(self):
        """테스트 데이터 설정"""
        # 테스트 클라이언트
        self.client = APIClient()

        # 테스트 사용자 생성
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            first_name="테스트",
            last_name="유저",
        )

        # 다른 사용자 (통계 테스트용)
        self.other_user = User.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="otherpass123",
        )

        # 카테고리 생성
        self.category = Category.objects.create(
            name="전자제품",
            slug="electronics",
        )

        # 테스트 상품들 생성
        self.product1 = Product.objects.create(
            name="노트북",
            price=Decimal("900000"),  # 판매가
            compare_price=Decimal("1000000"),  # 할인 전 가격 (원가)
            stock=10,
            category=self.category,
            seller=self.user,
            sku="NOTE001",
            description="테스트 노트북",
        )

        self.product2 = Product.objects.create(
            name="마우스",
            price=Decimal("50000"),
            stock=5,
            category=self.category,
            seller=self.user,
            sku="MOUSE001",
        )

        self.product3 = Product.objects.create(
            name="키보드 (품절)",
            price=Decimal("80000"),
            stock=0,  # 품절
            category=self.category,
            seller=self.user,
            sku="KEY001",
        )

        # URL 정의
        self.wishlist_url = reverse("wishlist-list")
        self.toggle_url = reverse("wishlist-toggle")
        self.add_url = reverse("wishlist-add")
        self.remove_url = reverse("wishlist-remove")
        self.bulk_add_url = reverse("wishlist-bulk-add")
        self.clear_url = reverse("wishlist-clear")
        self.check_url = reverse("wishlist-check")
        self.stats_url = reverse("wishlist-stats")
        self.move_to_cart_url = reverse("wishlist-move-to-cart")

    def _login(self):
        """로그인 헬퍼 메서드"""
        response = self.client.post(reverse("auth-login"), {"username": "testuser", "password": "testpass123"})
        token = response.json()["token"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return token

    # 인증 테스트

    def test_wishlist_requires_authentication(self):
        """인증 되지 않은 사용자는 찜하기 기능을 사용할 수 없음"""
        response = self.client.get(self.wishlist_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # 찜하기 추가 테스트

    def test_add_to_wishlist(self):
        """상품을 찜 목록에 추가"""
        self._login()

        response = self.client.post(self.add_url, {"product_id": self.product1.id})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["is_wished"])
        self.assertIn("찜 목록에 추가", response.data["message"])

        # 실제로 추가되었는지 확인
        self.assertTrue(self.user.is_in_wishlist(self.product1))
        self.assertEqual(self.user.get_wishlist_count(), 1)

    def test_add_duplicate_to_wishlist(self):
        """이미 찜한 상품을 다시 추가하려고 할 때"""
        self._login()

        # 첫 번째 추가
        self.user.add_to_wishlist(self.product1)

        # 두 번째 추가 시도
        response = self.client.post(self.add_url, {"product_id": self.product1.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("이미 찜한", response.data["message"])
        self.assertEqual(self.user.get_wishlist_count(), 1)  # 여전히 1개

    def test_add_invalid_product(self):
        """존재하지 않는 상품 찜하기 시도"""
        self._login()

        response = self.client.post(self.add_url, {"product_id": 99999})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    # 찜하기 토글 테스트

    def test_toggle_wishlist(self):
        """찜하기 토글 (추가 -> 제거 -> 추가)"""
        self._login()

        # 처음 토글 (추가)
        response = self.client.post(self.toggle_url, {"product_id": self.product1.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_wished"])
        self.assertIn("추가", response.data["message"])

        # 두 번째 토글 (제거)
        response = self.client.post(self.toggle_url, {"product_id": self.product1.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_wished"])
        self.assertIn("제거", response.data["message"])

        # 세 번째 토글 (다시 추가)
        response = self.client.post(self.toggle_url, {"product_id": self.product1.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_wished"])

    # 찜 목록 조회 테스트

    def test_list_wishlist(self):
        """찜 목록 조회"""
        self._login()

        # 상품들 찜하기
        self.user.add_to_wishlist(self.product1)
        self.user.add_to_wishlist(self.product2)
        self.user.add_to_wishlist(self.product3)

        response = self.client.get(self.wishlist_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(len(response.data["results"]), 3)

        # 첫 번째 상품 정보 확인
        product_data = response.data["results"][0]
        self.assertIn("id", product_data)
        self.assertIn("name", product_data)
        self.assertIn("price", product_data)
        self.assertIn("is_available", product_data)
        self.assertIn("wishlist_count", product_data)

    def test_list_wishlist_with_filters(self):
        """필터를 적용한 찜 목록 조회"""
        self._login()

        # 상품들 찜하기
        self.user.add_to_wishlist(self.product1)  # 세일 중, 재고 있음
        self.user.add_to_wishlist(self.product2)  # 세일 아님, 재고 있음
        self.user.add_to_wishlist(self.product3)  # 품절

        # 구매 가능한 상품만
        response = self.client.get(f"{self.wishlist_url}?is_available=true")
        self.assertEqual(response.data["count"], 2)

        # 세일 중인 상품만
        response = self.client.get(f"{self.wishlist_url}?on_sale=true")
        self.assertEqual(response.data["count"], 1)

        # 가격 오름차순 정렬
        response = self.client.get(f"{self.wishlist_url}?ordering=price")
        prices = [p["price"] for p in response.data["results"]]
        self.assertEqual(prices, sorted(prices))

    # 여러 상품 한번에 찜하기

    def test_bulk_add_to_wishlist(self):
        """여러 상품을 한번에 찜하기"""
        self._login()

        response = self.client.post(
            self.bulk_add_url,
            {"product_ids": [self.product1.id, self.product2.id, self.product3.id]},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["added_count"], 3)
        self.assertEqual(response.data["skipped_count"], 0)
        self.assertEqual(response.data["total_wishlist_count"], 3)

        # 실제로 추가되었는지 확인
        self.assertEqual(self.user.get_wishlist_count(), 3)

    def test_bulk_add_with_duplicates(self):
        """이미 찜한 상품 포함하여 여러 상품 찜하기"""
        self._login()

        # 하나는 미리 찜하기
        self.user.add_to_wishlist(self.product1)

        response = self.client.post(self.bulk_add_url, {"product_ids": [self.product1.id, self.product2.id]})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["added_count"], 1)  # product2만 추가
        self.assertEqual(response.data["skipped_count"], 1)  # product1은 스킵
        self.assertEqual(response.data["total_wishlist_count"], 2)

    # 찜 상태 확인 테스트

    def test_check_wishlist_status(self):
        """특정 상품의 찜 상태 확인"""
        self._login()

        # 찜하기 전
        response = self.client.get(f"{self.check_url}?product_id={self.product1.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_wished"])

        # 찜하기
        self.user.add_to_wishlist(self.product1)

        # 찜하기 후
        response = self.client.get(f"{self.check_url}?product_id={self.product1.id}")
        self.assertTrue(response.data["is_wished"])
        self.assertEqual(response.data["wishlist_count"], 1)

    def test_wishlist_count_multiple_users(self):
        """여러 사용자가 찜했을 때 카운트 확인"""
        self._login()

        # 두 사용자가 같은 상품 찜하기
        self.user.add_to_wishlist(self.product1)
        self.other_user.add_to_wishlist(self.product1)

        response = self.client.get(f"{self.check_url}?product_id={self.product1.id}")
        self.assertEqual(response.data["wishlist_count"], 2)

    # 찜 목록 통계 테스트

    def test_wishlist_statistics(self):
        """찜 목록 통계 조회"""
        self._login()

        # 상품들 찜하기
        self.user.add_to_wishlist(self.product1)  # 세일, 재고 있음
        self.user.add_to_wishlist(self.product2)  # 정가, 재고 있음
        self.user.add_to_wishlist(self.product3)  # 품절

        response = self.client.get(self.stats_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stats = response.data

        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(stats["available_count"], 2)
        self.assertEqual(stats["out_of_stock_count"], 1)
        self.assertEqual(stats["on_sale_count"], 1)

        # product1: compare_price=1000000, price=900000
        # product2: price=50000 (compare_price 없음)
        # product3: price=80000 (compare_price 없음)
        self.assertEqual(float(stats["total_price"]), 1130000)  # total_price = 1000000 + 50000 + 80000
        self.assertEqual(float(stats["total_sale_price"]), 1030000)  # total_sale_price 900000 + 50000 + 80000
        self.assertEqual(float(stats["total_discount"]), 100000)  # total_discount = 1000000 - 900000 = 100000

    # 찜 목록 삭제 테스트

    def test_remove_from_wishlist(self):
        """찜 목록에서 제거"""
        self._login()

        # 찜하기
        self.user.add_to_wishlist(self.product1)
        self.assertEqual(self.user.get_wishlist_count(), 1)

        # 제거
        response = self.client.delete(f"{self.remove_url}?product_id={self.product1.id}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # 확인
        self.assertEqual(self.user.get_wishlist_count(), 0)
        self.assertFalse(self.user.is_in_wishlist(self.product1))

    def test_clear_wishlist(self):
        """찜 목록 전체 삭제"""
        self._login()

        # 여러 상품 찜하기
        self.user.add_to_wishlist(self.product1)
        self.user.add_to_wishlist(self.product2)
        self.user.add_to_wishlist(self.product3)

        # confirm 없이 시도
        response = self.client.delete(self.clear_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # confirm=true로 삭제
        response = self.client.delete(f"{self.clear_url}?confirm=true")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # 확인
        self.assertEqual(self.user.get_wishlist_count(), 0)

    # 장바구니 연동 테스트

    def test_move_to_cart(self):
        """찜 목록에서 장바구니로 이동"""
        from shopping.models.cart import Cart

        self._login()

        # 상품들 찜하기
        self.user.add_to_wishlist(self.product1)
        self.user.add_to_wishlist(self.product2)
        self.user.add_to_wishlist(self.product3)  # 품절

        # 찜 목록 확인 (디버깅용)
        wishlist_count = self.user.wishlist_products.count()
        wishlist_ids = list(self.user.wishlist_products.values_list("id", flat=True))

        # 디버깅 정보 출력 (테스트 실패시 보임)
        self.assertEqual(
            wishlist_count,
            3,
            f"찜 목록에 상품이 3개 있어야 하는데 {wishlist_count}개 있습니다",
        )
        self.assertIn(
            self.product1.id,
            wishlist_ids,
            f"product1({self.product1.id})이 찜 목록에 없습니다. 현재 찜 목록: {wishlist_ids}",
        )

        # 장바구니로 이동
        response = self.client.post(
            self.move_to_cart_url,
            {
                "product_ids": [self.product1.id, self.product2.id, self.product3.id],
                "remove_from_wishlist": True,
            },
            format="json",  # JSON 형식으로 전송
        )
        # 404인 경우 응답 내용 확인
        if response.status_code == status.HTTP_404_NOT_FOUND:
            print(f"404 에러 응답: {response.data}")
            self.fail(f"404 에러 발생: {response.data}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["added_items"]), 2)  # 품절 제외
        self.assertEqual(len(response.data["out_of_stock"]), 1)  # 품절 1개

        # 장바구니 확인
        cart = Cart.objects.get(user=self.user, is_active=True)
        self.assertEqual(cart.items.count(), 2)

        # 찜 목록에서 제거되었는지 확인 (remove_from_wishlist=True)
        self.assertEqual(self.user.get_wishlist_count(), 1)  # 품절 상품만 남음

    def test_move_to_cart_without_removing(self):
        """찜 목록에서 장바구니로 이동 (찜 유지)"""
        from shopping.models.cart import Cart

        self._login()

        # 찜 목록 초기화 (다른 테스트의 영향 제거)
        self.user.wishlist_products.clear()

        # 상품 찜하기
        self.user.add_to_wishlist(self.product1)

        # DB에서 user를 다시 가져오기
        from shopping.models.user import User

        self.user = User.objects.get(id=self.user.id)

        # 장바구니로 이동 (찜 유지)
        response = self.client.post(
            self.move_to_cart_url,
            {"product_ids": [self.product1.id], "remove_from_wishlist": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 장바구니 확인
        cart = Cart.objects.get(user=self.user, is_active=True)
        self.assertEqual(cart.items.count(), 1)

        # 찜 목록에 여전히 있는지 확인
        self.assertEqual(self.user.get_wishlist_count(), 1)
        self.assertTrue(self.user.is_in_wishlist(self.product1))

    # 본인 찜 목록만 보기 테스트

    def test_user_can_only_see_own_wishlist(self):
        """사용자는 본인의 찜 목록만 볼 수 있음"""
        # 첫 번째 사용자
        self._login()
        self.user.add_to_wishlist(self.product1)
        self.user.add_to_wishlist(self.product2)

        response = self.client.get(self.wishlist_url)
        self.assertEqual(response.data["count"], 2)

        # 두 번째 사용자로 로그인
        self.client.credentials()  # 인증 초기화
        response = self.client.post(reverse("auth-login"), {"username": "otheruser", "password": "otherpass123"})
        token = response.json()["token"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # 다른 사용자의 찜 목록 조회 (비어있어야 함)
        response = self.client.get(self.wishlist_url)
        self.assertEqual(response.data["count"], 0)

"""로드 테스트용 데이터 생성

1000명의 사용자와 100개의 상품을 미리 생성합니다.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from decimal import Decimal
from shopping.models.user import User
from shopping.models.product import Category, Product


def setup_test_data():
    """테스트 데이터 생성"""
    print("테스트 사용자 생성 중...")
    users = []
    created_count = 0
    total_users = 1000  # 1000명으로 충분 (0-999 범위)

    for i in range(total_users):
        user, created = User.objects.get_or_create(
            username=f"load_test_user_{i}",
            defaults={
                "email": f"load_test_{i}@example.com",
                "is_email_verified": True,
            },
        )
        if created:
            user.set_password("testpass123")
            user.save()
            created_count += 1
        users.append(user)

        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{total_users} 사용자 처리 완료 (새로 생성: {created_count}개)")

    print(f"\n총 {len(users)}명 사용자 처리 완료 (새로 생성: {created_count}개)")

    print("\n테스트 상품 생성 중...")
    # 카테고리 먼저 생성
    category, _ = Category.objects.get_or_create(name="성능테스트 카테고리", defaults={"slug": "performance-test"})

    products = []
    product_created_count = 0
    updated_count = 0
    for i in range(100):
        product, created = Product.objects.get_or_create(
            name=f"성능테스트 상품 {i}",
            defaults={
                "category": category,
                "price": Decimal(10000 + (i * 1000)),
                "stock": 100000,  # 10만으로 증가
                "is_active": True,
                "description": f"로드 테스트용 상품 {i}번",
                "sku": f"PERF-TEST-{i:04d}",  # SKU 추가 (고유값)
            },
        )
        if created:
            product_created_count += 1
        else:
            # 기존 상품의 재고를 100000으로 업데이트
            product.stock = 100000
            product.save()
            updated_count += 1
        products.append(product)

        if (i + 1) % 20 == 0:
            print(f"{i + 1}/100 상품 처리 완료 (생성: {product_created_count}개, 재고 업데이트: {updated_count}개)")

    print(f"\n총 {len(users)}명 사용자, {len(products)}개 상품 처리 완료")
    print(f"(사용자 생성: {created_count}명, 상품 생성: {product_created_count}개, 재고 업데이트: {updated_count}개)")


if __name__ == "__main__":
    setup_test_data()

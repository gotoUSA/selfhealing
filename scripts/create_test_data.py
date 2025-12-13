#!/usr/bin/env python
"""
GAP 테스트용 테스트 데이터 생성 스크립트
Docker 환경에서 Django shell 없이 직접 실행
"""
import os
import sys
import django

# Django 설정
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings.local')

# 프로젝트 루트를 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

django.setup()

from django.contrib.auth import get_user_model
from shopping.models import Category, Product

User = get_user_model()


def create_test_data():
    """테스트 데이터 생성"""
    print("Creating test data...")
    
    # 1. 슈퍼유저 생성
    if not User.objects.filter(is_superuser=True).exists():
        User.objects.create_superuser(
            username='admin',
            email='admin@test.com',
            password='adminpass123'
        )
        print("Created superuser: admin")
    
    # 2. 테스트 셀러 생성
    seller, created = User.objects.get_or_create(
        username='test_seller',
        defaults={
            'email': 'seller@test.com',
            'is_staff': True,
        }
    )
    if created:
        seller.set_password('sellerpass123')
        seller.save()
        print("Created seller: test_seller")
    
    # 3. 카테고리 생성
    categories_data = [
        ('Electronics', 'electronics'),
        ('Clothing', 'clothing'),
        ('Books', 'books'),
        ('Home & Garden', 'home-garden'),
        ('Sports', 'sports'),
    ]
    
    categories = []
    for name, slug in categories_data:
        cat, created = Category.objects.get_or_create(
            name=name,
            defaults={
                'slug': slug,
                'is_active': True
            }
        )
        categories.append(cat)
        if created:
            print(f"Created category: {name}")
    
    # 4. 상품 생성
    products_to_create = 50
    existing_count = Product.objects.count()
    
    if existing_count < products_to_create:
        for i in range(existing_count + 1, products_to_create + 1):
            category = categories[i % len(categories)]
            product, created = Product.objects.get_or_create(
                name=f'Test Product {i}',
                defaults={
                    'slug': f'test-product-{i}',
                    'description': f'This is test product number {i}. It is in the {category.name} category.',
                    'price': 10000 + (i * 100),  # 10,100 ~ 15,000
                    'stock': 100,
                    'sku': f'TEST-SKU-{i:04d}',  # 고유 SKU 추가
                    'category': category,
                    'seller': seller,
                    'is_active': True
                }
            )
        print(f"Products created: {Product.objects.count()}")
    else:
        print(f"Products already exist: {existing_count}")
    
    print("Test data creation complete!")
    print(f"  - Users: {User.objects.count()}")
    print(f"  - Categories: {Category.objects.count()}")
    print(f"  - Products: {Product.objects.count()}")


if __name__ == '__main__':
    create_test_data()

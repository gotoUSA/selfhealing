"""
Product stock >= 0 CHECK 제약조건 추가

재고가 음수가 되는 것을 DB 레벨에서 방지합니다.
코드 레벨 검증에 더해 추가적인 안전장치 역할을 합니다.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shopping", "0015_sellerprofile"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                check=models.Q(stock__gte=0),
                name="product_stock_non_negative",
                violation_error_message="재고는 0 이상이어야 합니다.",
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                check=models.Q(sold_count__gte=0),
                name="product_sold_count_non_negative",
                violation_error_message="판매량은 0 이상이어야 합니다.",
            ),
        ),
    ]

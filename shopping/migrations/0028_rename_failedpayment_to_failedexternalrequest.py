# Generated manually for renaming FailedPayment to FailedExternalRequest

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    """
    Rename FailedPayment to FailedExternalRequest and add domain field.
    
    This migration:
    1. Creates new FailedExternalRequest table
    2. Migrates data from FailedPayment
    3. Removes old FailedPayment table
    """

    dependencies = [
        ("shopping", "0027_remove_failedoperation_order_and_more"),
    ]

    operations = [
        # Step 1: Add the new 'domain' field to existing table first
        migrations.AddField(
            model_name="failedpayment",
            name="domain",
            field=models.CharField(
                choices=[
                    ("external_api", "외부 API"),
                    ("payment", "결제"),
                    ("point", "포인트"),
                    ("inventory", "재고"),
                    ("webhook", "웹훅"),
                    ("notification", "알림"),
                ],
                default="payment",
                max_length=50,
                verbose_name="도메인",
            ),
        ),
        # Step 2: Rename payment_key -> external_request_id
        migrations.RenameField(
            model_name="failedpayment",
            old_name="payment_key",
            new_name="external_request_id",
        ),
        # Step 3: Rename toss_order_id -> external_order_id
        migrations.RenameField(
            model_name="failedpayment",
            old_name="toss_order_id",
            new_name="external_order_id",
        ),
        # Step 4: Rename the model
        migrations.RenameModel(
            old_name="FailedPayment",
            new_name="FailedExternalRequest",
        ),
        # Step 5: Rename the table
        migrations.AlterModelTable(
            name="failedexternalrequest",
            table="shopping_failed_external_request",
        ),
        # Step 6: Update verbose_name
        migrations.AlterModelOptions(
            name="failedexternalrequest",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "실패한 외부 요청 (DLQ)",
                "verbose_name_plural": "실패한 외부 요청 목록 (DLQ)",
            },
        ),
        # Step 7: Add index for domain field
        migrations.AddIndex(
            model_name="failedexternalrequest",
            index=models.Index(
                fields=["domain", "-created_at"],
                name="shopping_fa_domain_abc123_idx",
            ),
        ),
        # Step 8: Update related_name for payment foreign key
        migrations.AlterField(
            model_name="failedexternalrequest",
            name="payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="failed_external_requests",
                to="shopping.payment",
                verbose_name="원본 결제",
            ),
        ),
        # Step 9: Update related_name for order foreign key
        migrations.AlterField(
            model_name="failedexternalrequest",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="failed_external_requests",
                to="shopping.order",
                verbose_name="주문",
            ),
        ),
        # Step 10: Update related_name for user foreign key
        migrations.AlterField(
            model_name="failedexternalrequest",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="failed_external_requests",
                to="shopping.user",
                verbose_name="사용자",
            ),
        ),
        # Step 11: Update related_name for resolved_by foreign key
        migrations.AlterField(
            model_name="failedexternalrequest",
            name="resolved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="resolved_failed_external_requests",
                to="shopping.user",
                verbose_name="해결자",
            ),
        ),
    ]

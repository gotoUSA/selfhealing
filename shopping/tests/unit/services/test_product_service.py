"""
ProductService 테스트
"""
import logging
import threading

import pytest

from shopping.models.product import ProductImage
from shopping.services import ProductService
from shopping.tests.factories import ProductFactory, ProductImageFactory


@pytest.mark.django_db
class TestProductServiceSetPrimaryImage:
    """대표 이미지 설정 기능 테스트"""

    def test_set_primary_image_updates_others(self):
        """
        [정상 케이스] 새로운 대표 이미지 설정 시 기존 대표 이미지는 해제되어야 함
        """
        # Arrange
        product = ProductFactory()
        old_primary = ProductImageFactory(product=product, is_primary=True)
        new_primary = ProductImageFactory(product=product, is_primary=False)

        # Act
        new_primary.is_primary = True
        ProductService.set_primary_image(new_primary)

        # Assert
        old_primary.refresh_from_db()
        new_primary.refresh_from_db()

        assert old_primary.is_primary is False
        assert new_primary.is_primary is True

    def test_set_primary_image_initial(self):
        """
        [정상 케이스] 이미지가 없는 상태에서 첫 이미지를 대표로 설정
        """
        # Arrange
        product = ProductFactory()
        image = ProductImageFactory(product=product, is_primary=False)

        # Act
        image.is_primary = True
        ProductService.set_primary_image(image)

        # Assert
        image.refresh_from_db()
        assert image.is_primary is True

    def test_set_primary_image_multiple_updates(self):
        """
        [정상 케이스] 여러 번 대표 이미지를 변경해도 항상 하나만 대표여야 함
        """
        # Arrange
        product = ProductFactory()
        image1 = ProductImageFactory(product=product, is_primary=True)
        image2 = ProductImageFactory(product=product, is_primary=False)
        image3 = ProductImageFactory(product=product, is_primary=False)

        # Act
        image2.is_primary = True
        ProductService.set_primary_image(image2)

        image3.is_primary = True
        ProductService.set_primary_image(image3)

        # Assert
        image1.refresh_from_db()
        image2.refresh_from_db()
        image3.refresh_from_db()

        assert image1.is_primary is False
        assert image2.is_primary is False
        assert image3.is_primary is True

        # 대표 이미지가 정확히 1개만 존재
        primary_count = ProductImage.objects.filter(
            product=product, is_primary=True
        ).count()
        assert primary_count == 1

    def test_set_primary_image_ignored_if_not_primary(self):
        """
        [경계값] is_primary=False인 이미지가 전달되면 로직이 실행되지 않아야 함
        """
        # Arrange
        product = ProductFactory()
        primary_image = ProductImageFactory(product=product, is_primary=True)
        other_image = ProductImageFactory(product=product, is_primary=False)

        # Act
        ProductService.set_primary_image(other_image)

        # Assert
        primary_image.refresh_from_db()
        other_image.refresh_from_db()

        assert primary_image.is_primary is True
        assert other_image.is_primary is False

    def test_set_primary_image_exclude_self(self):
        """
        [경계값] 자기 자신이 이미 대표인 경우, 자기 자신을 해제하지 않아야 함
        """
        # Arrange
        product = ProductFactory()
        image = ProductImageFactory(product=product, is_primary=True)

        # Act
        ProductService.set_primary_image(image)

        # Assert
        image.refresh_from_db()
        assert image.is_primary is True

    def test_set_primary_image_different_product(self):
        """
        [예외 케이스] 다른 상품의 대표 이미지에는 영향을 주지 않아야 함
        """
        # Arrange
        product1 = ProductFactory()
        product2 = ProductFactory()

        p1_primary = ProductImageFactory(product=product1, is_primary=True)
        p2_primary = ProductImageFactory(product=product2, is_primary=True)

        p1_new = ProductImageFactory(product=product1, is_primary=False)

        # Act
        p1_new.is_primary = True
        ProductService.set_primary_image(p1_new)

        # Assert
        p1_primary.refresh_from_db()
        p1_new.refresh_from_db()
        p2_primary.refresh_from_db()

        assert p1_primary.is_primary is False
        assert p1_new.is_primary is True
        assert p2_primary.is_primary is True

    def test_set_primary_image_logging(self, caplog):
        """
        [정상 케이스] 대표 이미지 설정 시 로깅이 기록되어야 함
        """
        # Arrange
        caplog.set_level(logging.INFO, logger="shopping.services.product_service")
        product = ProductFactory()
        image = ProductImageFactory(product=product, is_primary=False)

        # Act
        image.is_primary = True
        ProductService.set_primary_image(image)

        # Assert
        log_messages = [record.message for record in caplog.records]
        assert any("대표 이미지 설정" in msg for msg in log_messages)
        assert any(f"product_id={product.id}" in msg for msg in log_messages)
        assert any(f"image_id={image.pk}" in msg for msg in log_messages)

    def test_set_primary_image_no_duplicate_primary(self):
        """
        [정상 케이스] 설정 후 같은 상품에 대표 이미지가 정확히 1개만 존재
        """
        # Arrange
        product = ProductFactory()
        image1 = ProductImageFactory(product=product, is_primary=True)
        image2 = ProductImageFactory(product=product, is_primary=False)
        image3 = ProductImageFactory(product=product, is_primary=False)

        # Act
        image2.is_primary = True
        ProductService.set_primary_image(image2)

        # Assert
        primary_images = ProductImage.objects.filter(
            product=product, is_primary=True
        )
        assert primary_images.count() == 1
        assert primary_images.first().id == image2.id


@pytest.mark.django_db(transaction=True)
class TestProductServiceSetPrimaryImageConcurrency:
    """대표 이미지 설정 동시성 테스트"""

    def test_set_primary_image_concurrency(self):
        """
        [동시성] 여러 스레드에서 동시에 대표 이미지 설정 시도
        select_for_update()가 동시성을 제어하여 최종적으로 1개만 대표여야 함
        """
        # Arrange
        product = ProductFactory()
        images = [
            ProductImageFactory(product=product, is_primary=False)
            for _ in range(3)
        ]
        results = []
        lock = threading.Lock()

        def set_primary_thread(image):
            try:
                image.is_primary = True
                ProductService.set_primary_image(image)
                with lock:
                    results.append({"success": True, "image_id": image.id})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [
            threading.Thread(target=set_primary_thread, args=(img,))
            for img in images
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 3

        # 최종적으로 대표 이미지는 정확히 1개
        primary_count = ProductImage.objects.filter(
            product=product, is_primary=True
        ).count()
        assert primary_count == 1

    def test_set_primary_image_race_condition(self):
        """
        [동시성] 경쟁 조건에서도 데이터 무결성 유지
        """
        # Arrange
        product = ProductFactory()
        old_primary = ProductImageFactory(product=product, is_primary=True)
        new_images = [
            ProductImageFactory(product=product, is_primary=False)
            for _ in range(5)
        ]
        results = []
        lock = threading.Lock()

        def rapid_set_primary(image):
            try:
                image.is_primary = True
                ProductService.set_primary_image(image)
                with lock:
                    results.append({"success": True})
            except Exception as e:
                with lock:
                    results.append({"error": str(e)})

        # Act
        threads = [
            threading.Thread(target=rapid_set_primary, args=(img,))
            for img in new_images
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert
        # 모든 스레드가 성공적으로 완료
        success_count = sum(1 for r in results if r.get("success", False))
        assert success_count == 5

        # 대표 이미지는 정확히 1개만 존재
        primary_images = ProductImage.objects.filter(
            product=product, is_primary=True
        )
        assert primary_images.count() == 1

        # 기존 대표는 해제됨
        old_primary.refresh_from_db()
        assert old_primary.is_primary is False

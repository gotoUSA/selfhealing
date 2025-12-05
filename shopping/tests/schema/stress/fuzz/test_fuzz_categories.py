"""
카테고리 API Fuzz 테스트
========================

카테고리 조회 API에 무작위 입력을 주입하여
안정성을 검증합니다.

📋 테스트 대상:
- GET /api/categories/{id}/ (상세)
- GET /api/categories/tree/ (트리 구조)

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_categories.py -v -n 0
```
"""

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck

from .conftest import product_id_strategy


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestCategoriesFuzz:
    """
    📂 카테고리 API Fuzz 테스트

    카테고리 조회 API에 무작위 입력을 주입하여
    안정성을 검증합니다.

    📋 테스트 대상:
    - GET /api/categories/{id}/ (상세)
    - GET /api/categories/tree/ (트리 구조)
    """

    @given(category_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_categories_detail_id_fuzz(self, client, category_id):
        """
        카테고리 상세 API ID 퍼징

        다양한 형식의 ID를 주입하여
        ID 파싱 로직의 안정성을 확인합니다.

        Args:
            category_id: Hypothesis가 생성한 무작위 카테고리 ID
        """
        # Act
        response = client.get(f"/api/categories/{category_id}/")

        # Assert
        assert response.status_code < 500, (
            f"카테고리 상세에서 서버 에러 발생!\n" f"category_id={repr(category_id)}\n" f"상태 코드: {response.status_code}"
        )

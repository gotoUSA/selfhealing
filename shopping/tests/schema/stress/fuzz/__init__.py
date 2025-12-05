"""
Fuzz 테스트 모듈 (Schemathesis Phase 5.1)
=========================================

이 패키지는 Hypothesis를 활용한 Fuzz 테스트를 포함합니다.

📁 파일 구조:
- conftest.py: 공통 Strategy 정의 및 Fixture
- test_fuzz_products.py: 상품 API 퍼징
- test_fuzz_cart.py: 장바구니 API 퍼징
- test_fuzz_auth.py: 인증 API 퍼징
- test_fuzz_categories.py: 카테고리 API 퍼징
- test_fuzz_orders.py: 주문 API 퍼징
- test_fuzz_payments.py: 결제 API 퍼징
- test_fuzz_reviews.py: 리뷰 API 퍼징
- test_fuzz_full_api.py: 전체 API 퍼징 (slow)

🚀 실행 방법:
```bash
# 모든 Fuzz 테스트 실행
pytest -m fuzz --no-cov -v -n 0

# 특정 도메인만 실행
pytest shopping/tests/schema/stress/fuzz/test_fuzz_orders.py -v -n 0

# slow 테스트 제외
pytest -m "fuzz and not slow" --no-cov -v -n 0
```
"""

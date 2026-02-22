# 여기에 test_concurrency.py의 1~256줄을 복사하세요
# (docstring + imports + 헬퍼 함수들)

"""
동시성 테스트 (Schemathesis Phase 6.1)
======================================

📋 개요
-------
이 모듈은 API 레벨에서 동시 요청(Concurrent Requests)을 시뮬레이션하여
데이터 무결성과 경쟁 조건(Race Condition) 방지를 검증합니다.

🎯 테스트 목적
-------------
1. **동시 요청 처리 검증**: 여러 클라이언트가 동시에 같은 리소스에 접근할 때 데이터 일관성 유지
2. **경쟁 조건 방지 확인**: 재고 차감, 장바구니 수정, 주문 생성 등에서 Race Condition 없음
3. **데이터베이스 락(Lock) 동작 확인**: select_for_update, F() 객체 등 동시성 제어 메커니즘 검증
4. **API 응답 일관성**: 동시 요청에도 예상된 응답 코드와 에러 메시지 반환

🔧 동시성 테스트 vs 부하 테스트
-----------------------------
| 구분 | 동시성 테스트 | 부하 테스트 |
|------|--------------|------------|
| 목적 | 데이터 무결성, Race Condition 방지 | 처리량, 응답 시간 측정 |
| 요청 수 | 10~50 동시 요청 | 수백~수천 요청 |
| 검증 대상 | 최종 상태 (재고, 포인트 등) | 처리 시간, 에러율 |
| 도구 | threading, asyncio | Locust, k6, JMeter |

📊 테스트 클래스 구조
-------------------
1. TestConcurrentCartOperations
   - 동시 장바구니 추가/수정/삭제 시 데이터 무결성 유지
   - 같은 상품 동시 추가 시 수량 정확성

2. TestConcurrentStockDeduction
   - 한정 재고 상품에 동시 주문 시 재고 음수 방지
   - sold_count 정확한 증가

3. TestConcurrentPointOperations
   - 동시 포인트 적립/차감 시 음수 방지
   - 포인트 잔액 정확성

4. TestConcurrentOrderCreation
   - 동시 주문 생성 시 주문번호 중복 없음
   - 결제 대기 상태 일관성

5. TestConcurrentResourceAccess (고급)
   - 동시 리소스 수정 시 낙관적/비관적 락 동작 확인

🚀 실행 방법
-----------
```bash
# 동시성 테스트만 실행 (병렬 실행 비활성화 필수!)
pytest -m concurrency --no-cov -v -n 0

# 스키마 + 동시성 테스트 모두 실행
pytest shopping/tests/schema/test_concurrency.py --no-cov -v -n 0

# slow 테스트 제외 (빠른 피드백용)
pytest -m "concurrency and not slow" --no-cov -v -n 0

# 특정 테스트만 실행
pytest -m concurrency -k "test_cart" --no-cov -v -n 0
```

⚙️ 동시성 제어 메커니즘 설명
--------------------------
1. **select_for_update**:
   - 행 레벨 비관적 락(Pessimistic Lock)
   - 조회 시 다른 트랜잭션의 수정/삭제 차단
   - 예: Product.objects.select_for_update().get(id=product_id)

2. **F() 객체**:
   - 데이터베이스 레벨에서 원자적 연산
   - Python에서 값을 읽지 않고 DB에서 직접 연산
   - 예: Product.objects.filter(id=product_id).update(stock=F('stock') - quantity)

3. **transaction.atomic()**:
   - 여러 연산을 하나의 트랜잭션으로 묶음
   - 실패 시 전체 롤백

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터
- test_api_contract.py: Phase 2 - API Contract 테스트
- test_stateful_workflow.py: Phase 3 - Stateful 워크플로우 테스트
- test_fuzz.py: Phase 5.1 - Fuzz 테스트
- test_negative.py: Phase 5.2 - Negative 테스트
- test_performance.py: Phase 6.2 - 성능 임계값 테스트

기존 동시성 테스트 파일 (더 상세한 단위 테스트):
- shopping/tests/integration/test_order_concurrency.py
- shopping/tests/integration/test_payment_concurrency.py
- shopping/tests/integration/test_auth_concurrency.py

⚠️ 주의사항
----------
1. **병렬 실행 비활성화 필수**: pytest -n 0 옵션 사용!
   동시성 테스트 자체에서 스레드를 사용하므로 pytest-xdist와 충돌합니다.

2. **DB 연결 정리**: 각 스레드에서 connection.close() 호출 필요
   Django는 스레드별로 독립적인 DB 연결을 사용합니다.

3. **APIClient 독립 인스턴스**: 각 스레드에서 새 APIClient() 생성!
   공유된 클라이언트는 상태 오염(State Pollution) 발생합니다.

4. **트랜잭션 격리**: @pytest.mark.django_db(transaction=True) 사용
   테스트 간 데이터 격리를 보장합니다.

5. **타이밍 이슈**: 동시 요청 결과는 비결정적일 수 있음
   결과의 "합계"나 "최종 상태"를 검증하는 것이 안정적입니다.

📅 작성 정보
-----------
- 작성일: 2025-12-05
- Schemathesis Phase: 6 - 현업 수준 고도화
- 테스트 개수: 12개 (slow 마커 포함 시)

💡 구현 참고
-----------
이 파일은 API 레벨의 동시성 테스트입니다.
더 상세한 서비스 레벨 동시성 테스트는 다음 파일들을 참조하세요:
- shopping/tests/integration/test_order_concurrency.py (주문 동시성)
- shopping/tests/integration/test_payment_concurrency.py (결제 동시성)
- shopping/tests/integration/test_auth_concurrency.py (인증 동시성)

이 테스트들은 이미 존재하는 동시성 테스트를 보완하여,
OpenAPI 스키마 기반 API 계약 관점에서 동시성을 검증합니다.
"""

import concurrent.futures
from typing import Any

from django.db import connection
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient



# =============================================================================
# 헬퍼 함수 및 유틸리티
# =============================================================================


def close_db_connection():
    """
    스레드별 DB 연결 정리 - 멀티스레딩 테스트 필수

    Django는 각 스레드에서 독립적인 DB 연결을 생성합니다.
    스레드 종료 시 명시적으로 연결을 닫지 않으면 연결 누수가 발생합니다.

    사용 위치: 각 스레드 함수의 finally 블록 또는 마지막에 호출
    """
    connection.close()


def login_and_get_token(username: str, password: str = "testpass123") -> tuple[APIClient | None, str | None, str | None]:
    """
    로그인하여 JWT 토큰 발급

    동시성 테스트에서 각 스레드는 독립적인 APIClient와 토큰이 필요합니다.
    이 함수는 새 APIClient를 생성하고 로그인 후 토큰을 반환합니다.

    Args:
        username: 사용자 이름
        password: 비밀번호 (기본값: testpass123)

    Returns:
        (client, token, error) 튜플
        - 성공 시: (APIClient, access_token, None)
        - 실패 시: (None, None, error_message)

    사용 예:
        client, token, error = login_and_get_token("testuser")
        if error:
            return {"error": error}
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.post("/api/cart/add_item/", ...)
    """
    client = APIClient()
    login_url = reverse("auth-login")
    response = client.post(
        login_url,
        {"username": username, "password": password},
        format="json",
    )

    if response.status_code != status.HTTP_200_OK:
        return None, None, f"Login failed: {response.status_code}"

    data = response.json()
    # 응답 구조: {"token": {"access": "...", "refresh": "..."}} 또는 {"access": "..."}
    token = data.get("access") or data.get("token", {}).get("access")
    return client, token, None


def run_concurrent_requests(
    func,
    args_list: list[tuple],
    max_workers: int = 10,
) -> list[dict[str, Any]]:
    """
    동시 요청 실행 유틸리티

    ThreadPoolExecutor를 사용하여 여러 요청을 동시에 실행합니다.
    각 스레드에서 독립적인 DB 연결을 사용하고, 완료 시 정리합니다.

    Args:
        func: 실행할 함수 (callable)
        args_list: 각 스레드에 전달할 인자 튜플 리스트
        max_workers: 최대 동시 워커 수

    Returns:
        각 요청의 결과 딕셔너리 리스트 (순서 보장 안 됨)

    구현 원리:
        1. ThreadPoolExecutor 생성 (max_workers 만큼)
        2. 모든 작업 submit()하여 Future 객체 획득
        3. as_completed()로 완료된 순서대로 결과 수집
        4. 각 스레드에서 close_db_connection() 호출
    """
    results = []

    def worker_wrapper(args):
        """DB 연결 정리를 포함한 래퍼 함수"""
        try:
            return func(*args)
        finally:
            close_db_connection()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker_wrapper, args) for args in args_list]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"error": str(e), "exception_type": type(e).__name__})

    return results

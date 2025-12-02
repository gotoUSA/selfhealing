"""
환불/교환 Locust 부하 테스트 - 메인 파일

📌 실행 방법:

    # 1. 웹 UI 모드 (개발/디버깅용)
    locust -f shopping/tests/performance/return_exchange_locust.py --host=http://localhost:8000
    # → http://localhost:8089 접속

    # 2. CLI 모드 - 현실적 트래픽
    locust -f shopping/tests/performance/return_exchange_locust.py \\
        --host=http://localhost:8000 \\
        --users 100 \\
        --spawn-rate 10 \\
        --run-time 5m \\
        --headless \\
        --html return_test_report.html

    # 3. 특정 시나리오만 실행
    locust -f shopping/tests/performance/return_exchange_locust.py \\
        CustomerOnlyUser \\
        --host=http://localhost:8000 \\
        --users 50 --spawn-rate 5

📌 사전 준비:

    1. Redis 실행:
       redis-server  (또는 Docker: docker run -p 6379:6379 redis)

    2. Celery Worker 실행 (환불 처리에 필요):
       celery -A myproject worker --loglevel=info --pool=solo  # Windows
       celery -A myproject worker --loglevel=info              # Linux/Mac

    3. Django 서버 실행:
       python manage.py runserver

    4. 테스트 데이터 생성:
       python manage.py create_load_test_users --count 1000 --points 50000
       python manage.py create_test_data --preset full

📌 프리셋 변경:

    아래 `CURRENT_SCENARIO` 변수를 원하는 프리셋으로 변경하세요.
    - LIGHT_TRAFFIC: 조회 위주 (고객 90%, 판매자 10%)
    - MEDIUM_TRAFFIC: 신청 중심 (고객 80%, 판매자 20%)
    - REALISTIC_TRAFFIC: 현실적 혼합 (고객 70%, 판매자 30%) ✅ 기본값
    - HIGH_LOAD_TRAFFIC: 처리 집중 (고객 50%, 판매자 50%)
    - STRESS_TEST: E2E 전체 플로우 (100%)

📌 목표 지표:

    - 응답 시간: 조회 < 200ms, 신청/처리 < 500ms
    - 성공률: > 95%
    - 처리량: > 50 RPS (서버 사양에 따라 조정)
"""

from locust import HttpUser, between, events

# scenarios 모듈에서 TaskSet import
# Note: 'return'은 Python 예약어이므로 'return_exchange.py' 사용
from .scenarios.return_exchange import (
    CustomerReturnBehavior,
    SellerReturnBehavior,
    ReturnE2EBehavior,
)


# =============================================================================
# 시나리오 프리셋
# =============================================================================

# 📌 프리셋 1: Light Traffic (조회 위주)
# - 용도: DB read 성능, 목록 조회 성능 측정
# - 적정 유저: 100 → 300 → 500
LIGHT_TRAFFIC = {
    CustomerReturnBehavior: 90,
    SellerReturnBehavior: 10,
}

# 📌 프리셋 2: Medium Traffic (신청 중심)
# - 용도: 환불/교환 신청 처리량 측정
# - 적정 유저: 50 → 100 → 200
MEDIUM_TRAFFIC = {
    CustomerReturnBehavior: 80,
    SellerReturnBehavior: 20,
}

# 📌 프리셋 3: Realistic Traffic (현실적 혼합) ✅ 기본값
# - 용도: 실제 운영 환경과 유사한 트래픽
# - 적정 유저: 50 → 100 → 200 → 300
REALISTIC_TRAFFIC = {
    CustomerReturnBehavior: 70,
    SellerReturnBehavior: 30,
}

# 📌 프리셋 4: High Load (처리 집중)
# - 용도: 판매자 처리 로직 부하 테스트
# - 적정 유저: 30 → 50 → 100
HIGH_LOAD_TRAFFIC = {
    CustomerReturnBehavior: 50,
    SellerReturnBehavior: 50,
}

# 📌 프리셋 5: Stress Test (E2E 전체 플로우)
# - 용도: 전체 프로세스 동시성 검증
# - 적정 유저: 10 → 20 → 50 (주의: 높은 부하!)
STRESS_TEST = {
    ReturnE2EBehavior: 100,
}


# ==================== 실제 사용할 시나리오 선택 ====================
# 👇 여기서 원하는 프리셋을 선택하세요
CURRENT_SCENARIO = REALISTIC_TRAFFIC  # ✅ 기본값: 현실적 트래픽


# =============================================================================
# 메인 User 클래스
# =============================================================================


class ReturnExchangeUser(HttpUser):
    """
    환불/교환 혼합 시나리오 User

    선택한 프리셋에 따라 고객/판매자 비율이 결정됩니다.
    """

    tasks = CURRENT_SCENARIO
    wait_time = between(2, 6)  # 현실적인 대기 시간
    host = "http://localhost:8000"


class CustomerOnlyUser(HttpUser):
    """
    고객 전용 User

    고객 시나리오만 독립적으로 테스트할 때 사용:
    - 환불/교환 목록 조회
    - 환불/교환 신청
    - 송장번호 입력
    - 신청 취소
    """

    tasks = [CustomerReturnBehavior]
    wait_time = between(2, 5)
    host = "http://localhost:8000"


class SellerOnlyUser(HttpUser):
    """
    판매자 전용 User

    판매자 시나리오만 독립적으로 테스트할 때 사용:
    - 환불/교환 목록 조회
    - 승인/거부
    - 도착 확인
    - 완료 처리
    """

    tasks = [SellerReturnBehavior]
    wait_time = between(1, 3)
    host = "http://localhost:8000"


class ReturnE2EStressUser(HttpUser):
    """
    E2E 스트레스 테스트 User

    전체 환불 플로우를 한 번에 테스트:
    주문 확인 → 환불 신청 → 승인 → 송장 입력 → 도착 확인 → 완료

    ⚠️  주의: 이 시나리오는 부하가 높습니다.
    적정 유저: 10 ~ 50명
    """

    tasks = [ReturnE2EBehavior]
    wait_time = between(5, 10)  # E2E는 더 긴 대기시간
    host = "http://localhost:8000"


# =============================================================================
# 통계 수집 및 결과 출력
# =============================================================================

# 통계 저장
return_stats = {
    "customer_requests": 0,
    "customer_success": 0,
    "seller_requests": 0,
    "seller_success": 0,
    "e2e_complete": 0,
}


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, exception, **kwargs):
    """요청별 통계 수집"""
    if exception:
        return

    # 고객 요청 추적 (seller가 포함되지 않은 /api/returns/ 요청)
    if "/api/returns/" in name and "/api/seller/" not in name:
        return_stats["customer_requests"] += 1
        if response and response.status_code in [200, 201]:
            return_stats["customer_success"] += 1

    # 판매자 요청 추적
    if "/api/seller/returns/" in name:
        return_stats["seller_requests"] += 1
        if response and response.status_code == 200:
            return_stats["seller_success"] += 1

    # E2E 완료 추적 (E2E 태그가 있는 complete 요청)
    if "/complete/" in name and "[E2E]" in name:
        if response and response.status_code == 200:
            return_stats["e2e_complete"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력"""
    print("\n" + "=" * 70)
    print("📊 환불/교환 부하 테스트 결과")
    print("=" * 70)

    print(f"\n👤 고객 요청:")
    print(f"   총 요청: {return_stats['customer_requests']}")
    print(f"   성공: {return_stats['customer_success']}")
    if return_stats["customer_requests"] > 0:
        rate = (return_stats["customer_success"] / return_stats["customer_requests"]) * 100
        print(f"   성공률: {rate:.1f}%")

    print(f"\n🏪 판매자 요청:")
    print(f"   총 요청: {return_stats['seller_requests']}")
    print(f"   성공: {return_stats['seller_success']}")
    if return_stats["seller_requests"] > 0:
        rate = (return_stats["seller_success"] / return_stats["seller_requests"]) * 100
        print(f"   성공률: {rate:.1f}%")

    print(f"\n🔄 E2E 완료: {return_stats['e2e_complete']}건")

    print("\n" + "=" * 70)
    print("📌 권장 목표:")
    print("   - 응답 시간 (P95): < 500ms")
    print("   - 성공률: > 95%")
    print("   - 처리량: > 50 RPS")
    print("=" * 70 + "\n")


# =============================================================================
# 점진적 부하 증가 (Optional)
# =============================================================================
# 사용하려면 아래 주석을 해제하세요.

# from locust import LoadTestShape
#
# class ReturnLoadShape(LoadTestShape):
#     """
#     환불/교환 점진적 부하 증가
#
#     - 1분: 20명 (워밍업)
#     - 3분: 50명 (중간 부하)
#     - 5분: 100명 (피크)
#     - 7분: 50명 (감소)
#     - 8분: 종료
#     """
#
#     stages = [
#         {"duration": 60, "users": 20, "spawn_rate": 5},
#         {"duration": 180, "users": 50, "spawn_rate": 10},
#         {"duration": 300, "users": 100, "spawn_rate": 10},
#         {"duration": 420, "users": 50, "spawn_rate": 10},
#         {"duration": 480, "users": 10, "spawn_rate": 5},
#     ]
#
#     def tick(self):
#         run_time = self.get_run_time()
#
#         for stage in self.stages:
#             if run_time < stage["duration"]:
#                 return (stage["users"], stage["spawn_rate"])
#
#         return None  # 테스트 종료

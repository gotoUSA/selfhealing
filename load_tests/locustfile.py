"""
Locust 부하 테스트 - 메인 진입점

실행 방법:
    # 웹 UI 모드
    locust -f load_tests/locustfile.py --host=http://localhost:8000

    # CLI 모드 (자동 실행) - Windows는 한 줄 명령어 권장
    PYTHONUTF8=1 locust -f load_tests/locustfile.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=5m --headless --html=report.html

웹 UI 접속:
    http://localhost:8089

사용자 비율:
    Browser (65%): 조회만 하는 방문자
    Shopper (25%): 장바구니까지 담는 사용자
    Buyer (10%): 결제까지 완료하는 구매자
"""

import os
import sys

# 독립 실행 시 load_tests 패키지 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import events

from load_tests.users import BrowserUser, ShopperUser, BuyerUser
from load_tests.config import USER_WEIGHTS


# =============================================================================
# 사용자 클래스에 Weight 설정
# =============================================================================


class Browser(BrowserUser):
    """브라우징 사용자 (65%)"""

    weight = USER_WEIGHTS["browser"]


class Shopper(ShopperUser):
    """장바구니 사용자 (25%)"""

    weight = USER_WEIGHTS["shopper"]


class Buyer(BuyerUser):
    """구매 완료 사용자 (10%)"""

    weight = USER_WEIGHTS["buyer"]


# =============================================================================
# 테스트 이벤트 훅
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 실행"""
    print("\n" + "=" * 60)
    print("🚀 부하 테스트 시작")
    print("=" * 60)
    print(f"📊 사용자 비율:")
    print(f"   - Browser (조회): {USER_WEIGHTS['browser']}%")
    print(f"   - Shopper (장바구니): {USER_WEIGHTS['shopper']}%")
    print(f"   - Buyer (구매): {USER_WEIGHTS['buyer']}%")
    print("=" * 60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 실행"""
    print("\n" + "=" * 60)
    print("✅ 부하 테스트 완료")
    print("=" * 60)

    # 통계 요약
    stats = environment.stats

    print(f"\n📈 요약:")
    print(f"   - 총 요청 수: {stats.total.num_requests}")
    print(f"   - 실패 수: {stats.total.num_failures}")
    print(f"   - 평균 응답시간: {stats.total.avg_response_time:.2f}ms")

    if stats.total.num_requests > 0:
        error_rate = (stats.total.num_failures / stats.total.num_requests) * 100
        print(f"   - 에러율: {error_rate:.2f}%")

    print("\n" + "=" * 60 + "\n")

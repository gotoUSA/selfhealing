"""
HPA 플래핑 방지 테스트 (Locust 기반)

목표:
    - Pod가 급격히 생성/삭제되지 않고 부드럽게 스케일링되는지 검증
    - 계단식 부하 패턴으로 HPA 안정성 테스트

검증 기준:
    - 5분당 최대 스케일 이벤트 2회 이하
    - Pod 최소 생존 시간 60초 이상
    - 5분 안정화 윈도우 준수

실행 방법:
    locust -f load_tests/scenarios/hpa/test_hpa_flapping.py \
        --host=http://django-api.production \
        --headless \
        --run-time=10m

동시 모니터링:
    kubectl get events --watch \
        --field-selector reason=SuccessfulRescale \
        -n production

    kubectl get pods -l app=django-api -w
"""

from locust import HttpUser, task, between
from locust import LoadTestShape


# =============================================================================
# 플래핑 검증 기준
# =============================================================================
FLAPPING_CRITERIA = {
    # 5분당 최대 스케일 이벤트 횟수
    "max_scale_events_per_5min": 2,
    # Pod 최소 생존 시간 (초)
    "min_pod_lifetime_seconds": 60,
    # 안정화 윈도우 (초)
    "stabilization_window": 300,
}


class StepLoadShape(LoadTestShape):
    """
    계단식 부하 패턴 (Step Load Pattern)

    부하 단계:
        1. 0-2분: 10 users (baseline)
        2. 2-4분: 50 users (step up)
        3. 4-6분: 100 users (peak)
        4. 6-8분: 50 users (step down)
        5. 8-10분: 10 users (return to baseline)

    이 패턴으로 HPA가 부드럽게 스케일링하는지 검증
    급격한 스케일 업/다운이 발생하면 플래핑으로 판단
    """

    # 부하 단계 정의 (duration: 누적 시간)
    stages = [
        {"duration": 120, "users": 10, "spawn_rate": 5},  # 0-2분: baseline
        {"duration": 240, "users": 50, "spawn_rate": 10},  # 2-4분: step up
        {"duration": 360, "users": 100, "spawn_rate": 20},  # 4-6분: peak
        {"duration": 480, "users": 50, "spawn_rate": 10},  # 6-8분: step down
        {"duration": 600, "users": 10, "spawn_rate": 5},  # 8-10분: baseline
    ]

    def tick(self):
        """현재 시간에 따른 사용자 수 및 스폰 레이트 반환"""
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])

        # 모든 단계 완료 시 테스트 종료
        return None


class WaveLoadShape(LoadTestShape):
    """
    파형 부하 패턴 (Wave Load Pattern)

    부하 단계:
        1. 0-1분: 20 users (base)
        2. 1-2분: 60 users (peak 1)
        3. 2-3분: 20 users (trough 1)
        4. 3-4분: 80 users (peak 2)
        5. 4-5분: 30 users (trough 2)
        6. 5-6분: 100 users (peak 3)
        7. 6-8분: 20 users (cooldown)

    이 패턴으로 반복적인 부하 변화에 대한 HPA 안정성 검증
    """

    stages = [
        {"duration": 60, "users": 20, "spawn_rate": 10},  # base
        {"duration": 120, "users": 60, "spawn_rate": 20},  # peak 1
        {"duration": 180, "users": 20, "spawn_rate": 10},  # trough 1
        {"duration": 240, "users": 80, "spawn_rate": 20},  # peak 2
        {"duration": 300, "users": 30, "spawn_rate": 10},  # trough 2
        {"duration": 360, "users": 100, "spawn_rate": 25},  # peak 3
        {"duration": 480, "users": 20, "spawn_rate": 10},  # cooldown
    ]

    def tick(self):
        """현재 시간에 따른 사용자 수 및 스폰 레이트 반환"""
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])

        return None


class HPATestUser(HttpUser):
    """
    HPA 테스트용 사용자 시뮬레이션

    다양한 엔드포인트에 요청을 보내 실제 사용 패턴 시뮬레이션
    """

    # 요청 간 대기 시간 (0.5~1.5초)
    wait_time = between(0.5, 1.5)

    @task(10)
    def health_check(self):
        """헬스체크 엔드포인트 (가장 빈번)"""
        with self.client.get("/health/ready/", catch_response=True, name="/health/ready/") as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    @task(5)
    def api_products_list(self):
        """상품 목록 조회 (일반 API)"""
        with self.client.get("/api/products/", catch_response=True, name="/api/products/") as response:
            if response.status_code in [200, 401]:
                response.success()
            else:
                response.failure(f"Products API failed: {response.status_code}")

    @task(3)
    def api_categories(self):
        """카테고리 조회 (가벼운 API)"""
        with self.client.get("/api/categories/", catch_response=True, name="/api/categories/") as response:
            if response.status_code in [200, 401, 404]:
                response.success()
            else:
                response.failure(f"Categories API failed: {response.status_code}")

    @task(2)
    def api_search(self):
        """검색 API (무거운 쿼리)"""
        with self.client.get(
            "/api/products/?search=test", catch_response=True, name="/api/products/?search=[query]"
        ) as response:
            if response.status_code in [200, 401]:
                response.success()
            else:
                response.failure(f"Search API failed: {response.status_code}")


class CeleryLoadUser(HttpUser):
    """
    Celery 큐 부하 테스트용 사용자

    비동기 작업을 트리거하는 엔드포인트 호출
    KEDA 기반 Celery Worker 스케일링 테스트
    """

    wait_time = between(1.0, 3.0)

    @task(5)
    def trigger_notification(self):
        """알림 작업 트리거"""
        with self.client.post(
            "/api/notifications/test/",
            json={"type": "test", "message": "HPA test notification"},
            catch_response=True,
            name="/api/notifications/test/",
        ) as response:
            if response.status_code in [200, 201, 202, 401, 404]:
                response.success()
            else:
                response.failure(f"Notification trigger failed: {response.status_code}")

    @task(2)
    def trigger_email(self):
        """이메일 작업 트리거"""
        with self.client.post(
            "/api/emails/test/",
            json={"to": "test@example.com", "subject": "HPA Test"},
            catch_response=True,
            name="/api/emails/test/",
        ) as response:
            if response.status_code in [200, 201, 202, 401, 404]:
                response.success()
            else:
                response.failure(f"Email trigger failed: {response.status_code}")


# =============================================================================
# 검증 쿼리 (Prometheus)
# =============================================================================
PROMETHEUS_QUERIES = {
    # 5분간 스케일 이벤트 횟수 (2회 이하여야 정상)
    "scale_events_5m": """
        changes(kube_hpa_status_current_replicas{hpa="django-api-hpa"}[5m])
    """,
    # Pod 평균 생존 시간 (60초 이상이어야 정상)
    "avg_pod_lifetime": """
        avg(time() - kube_pod_start_time{pod=~"django-api-.*"})
    """,
    # 현재 replica 수
    "current_replicas": """
        kube_hpa_status_current_replicas{hpa="django-api-hpa"}
    """,
    # 목표 replica 수
    "desired_replicas": """
        kube_hpa_status_desired_replicas{hpa="django-api-hpa"}
    """,
}


if __name__ == "__main__":
    print("HPA Flapping Test")
    print("=" * 60)
    print("플래핑 검증 기준:")
    for key, value in FLAPPING_CRITERIA.items():
        print(f"  - {key}: {value}")
    print()
    print("Prometheus 검증 쿼리:")
    for name, query in PROMETHEUS_QUERIES.items():
        print(f"  - {name}:")
        print(f"    {query.strip()}")
    print()
    print("실행 방법:")
    print("  locust -f test_hpa_flapping.py --host=http://your-api-host")

"""운영 엔드포인트 테스트 (헬스 체크, Prometheus 메트릭)"""

import pytest


@pytest.mark.django_db
class TestOperationalEndpoints:
    """루트 urlconf에 배선된 운영용 엔드포인트"""

    def test_health_endpoint_returns_ok(self, api_client):
        response = api_client.get("/health/")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_metrics_endpoint_exposes_django_request_metrics(self, api_client):
        """django_prometheus 미들웨어가 수집한 메트릭이 /metrics 로 노출된다

        (otel-collector 가 이 경로를 스크랩한다 — URL 배선이 빠지면 404 만 남는다)
        """
        # 미들웨어가 최소 한 건은 집계하도록 먼저 한 번 호출
        api_client.get("/health/")

        response = api_client.get("/metrics")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert "django_http_requests_total" in response.content.decode()

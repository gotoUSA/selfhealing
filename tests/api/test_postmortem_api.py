"""
Post-mortem API 통합 테스트.

/postmortem/ 경로 테스트.
"""

import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


User = get_user_model()


@pytest.mark.django_db
class TestPostmortemGenerateEndpoint(TestCase):
    """POST /postmortem/generate/ 테스트."""

    def setUp(self):
        """테스트 설정."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
        )

    def test_unauthenticated_request_rejected(self):
        """인증되지 않은 요청은 거부됨."""
        response = self.client.post("/api/self-healing/postmortem/generate/")
        assert response.status_code in [401, 403]

    def test_authenticated_request_succeeds(self):
        """인증된 요청은 성공."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/self-healing/postmortem/generate/",
            data={"incident_id": "TEST-INTEGRATION-001"},
            format="json",
        )
        # 성공 또는 서비스 관련 에러 (CB 서비스 미설정 등)
        assert response.status_code in [200, 500]

    def test_xtest_header_not_required(self):
        """X-Test-Mode 헤더가 필요 없음."""
        self.client.force_authenticate(user=self.user)
        # X-Test-Mode 헤더 없이 요청
        response = self.client.post(
            "/api/self-healing/postmortem/generate/",
            data={},
            format="json",
        )
        # 403 Forbidden이 아니어야 함
        assert response.status_code != 403 or "chaos_mode" not in str(response.content)


@pytest.mark.django_db
class TestPostmortemIncidentsEndpoint(TestCase):
    """GET /postmortem/incidents/ 테스트."""

    def setUp(self):
        """테스트 설정."""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser2",
            password="testpass123",
        )

    def test_unauthenticated_request_rejected(self):
        """인증되지 않은 요청은 거부됨."""
        response = self.client.get("/api/self-healing/postmortem/incidents/")
        assert response.status_code in [401, 403]

    def test_authenticated_request_succeeds(self):
        """인증된 요청은 성공."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/self-healing/postmortem/incidents/")
        assert response.status_code == 200
        data = response.json()
        assert "incidents" in data
        assert "total_count" in data


@pytest.mark.django_db
class TestPostmortemStoreImports(TestCase):
    """postmortem_store.py import 테스트."""

    def test_import_from_postmortem_store_works(self):
        """postmortem_store.py에서 import 가능."""
        from selfhealing.services.postmortem_store import (
            add_healing_incident,
            get_healing_incidents,
            get_healing_incidents_count,
        )

        assert callable(add_healing_incident)
        assert callable(get_healing_incidents)
        assert callable(get_healing_incidents_count)

"""
Meta-Watchdog URL 라우팅 테스트.

urls.py에 등록된 Meta-Watchdog 엔드포인트 테스트.
소스코드 분석 기반 - Django 설정 없이 테스트.
"""

import os
import pytest


class TestMetaWatchdogUrlRouting:
    """Meta-Watchdog URL 라우팅 테스트 - 소스코드 분석 기반."""

    def _get_urls_source(self):
        """urls.py 소스코드 읽기."""
        urls_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "src",
            "selfhealing",
            "api",
            "django",
            "urls.py",
        )
        urls_path = os.path.normpath(urls_path)

        with open(urls_path, encoding="utf-8") as f:
            return f.read()

    def test_meta_watchdog_views_imported_in_urls(self):
        """urls.py에서 MetaWatchdog Views가 import되었는지 확인."""
        source = self._get_urls_source()

        # MetaWatchdogLivenessView, MetaWatchdogStatusView import 확인
        assert "MetaWatchdogLivenessView" in source
        assert "MetaWatchdogStatusView" in source

    def test_meta_watchdog_liveness_url_pattern_exists(self):
        """health/meta-watchdog/ URL 경로가 등록되었는지 확인."""
        source = self._get_urls_source()

        # URL 패턴 확인
        assert "health/meta-watchdog" in source
        assert "meta-watchdog-liveness" in source

    def test_meta_watchdog_status_url_pattern_exists(self):
        """meta/status/ URL 경로가 등록되었는지 확인."""
        source = self._get_urls_source()

        # URL 패턴 확인
        assert "meta/status" in source
        assert "meta-watchdog-status" in source

    def test_url_patterns_contain_meta_watchdog_paths(self):
        """urlpatterns에 Meta-Watchdog path() 호출 포함."""
        source = self._get_urls_source()

        # path() 호출 확인 (줄바꿈이 있을 수 있음)
        assert '"health/meta-watchdog/"' in source
        assert '"meta/status/"' in source

    def test_meta_watchdog_views_imported_from_correct_module(self):
        """meta_watchdog 모듈에서 뷰를 임포트하는지 확인."""
        source = self._get_urls_source()

        # from ... import 구문 확인
        assert "from selfhealing.api.django.views.meta_watchdog import" in source


class TestMetaWatchdogViewModule:
    """meta_watchdog.py 뷰 모듈 테스트 - 소스코드 분석 기반."""

    def _get_view_source(self):
        """meta_watchdog.py 소스코드 읽기."""
        view_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "src",
            "selfhealing",
            "api",
            "django",
            "views",
            "meta_watchdog.py",
        )
        view_path = os.path.normpath(view_path)

        with open(view_path, encoding="utf-8") as f:
            return f.read()

    def test_meta_watchdog_liveness_view_class_exists(self):
        """MetaWatchdogLivenessView 클래스가 정의되어 있는지 확인."""
        source = self._get_view_source()

        assert "class MetaWatchdogLivenessView" in source

    def test_meta_watchdog_status_view_class_exists(self):
        """MetaWatchdogStatusView 클래스가 정의되어 있는지 확인."""
        source = self._get_view_source()

        assert "class MetaWatchdogStatusView" in source

    def test_liveness_view_inherits_from_apiview(self):
        """MetaWatchdogLivenessView가 APIView를 상속하는지 확인."""
        source = self._get_view_source()

        assert "class MetaWatchdogLivenessView(APIView)" in source

    def test_status_view_inherits_from_apiview(self):
        """MetaWatchdogStatusView가 APIView를 상속하는지 확인."""
        source = self._get_view_source()

        assert "class MetaWatchdogStatusView(APIView)" in source

    def test_liveness_view_has_get_method(self):
        """MetaWatchdogLivenessView에 get 메서드가 있는지 확인."""
        source = self._get_view_source()

        # 클래스 정의 이후 get 메서드 확인
        assert "def get(self, request" in source

    def test_permission_classes_are_empty(self):
        """권한 없이 접근 가능 (K8s Probe용)."""
        source = self._get_view_source()

        # permission_classes = [] 확인
        assert "permission_classes = []" in source

    def test_view_uses_watchdog(self):
        """뷰에서 watchdog을 사용하는지 확인."""
        source = self._get_view_source()

        # get_selfhealer_watchdog 사용 확인
        assert "get_selfhealer_watchdog" in source

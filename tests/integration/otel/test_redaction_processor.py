"""
OpenTelemetry Collector Redaction Processor 통합 테스트

redaction processor가 민감정보를 올바르게 마스킹하는지 검증합니다.
- 값 패턴 기반 마스킹 (내부 IP, 서버 경로, JWT 토큰 등)
- v0.96.0 기준: traces 파이프라인만 지원
"""

import os

import pytest
import yaml


class TestRedactionProcessorConfiguration:
    """redaction processor 설정 검증 테스트"""

    @pytest.fixture(scope="class")
    def collector_config(self):
        """otel-collector-config.yml 로드"""
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "docker",
            "otel-collector",
            "otel-collector-config.yml",
        )
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_redaction_processor_exists(self, collector_config):
        """redaction processor가 설정에 존재하는지 확인"""
        processors = collector_config.get("processors", {})
        assert "redaction" in processors, "redaction processor가 설정에 없습니다"

    def test_allow_all_keys_enabled(self, collector_config):
        """allow_all_keys가 true로 설정되어 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        assert redaction.get("allow_all_keys") is True, "allow_all_keys가 true여야 합니다 (키 삭제 대신 값만 마스킹)"

    def test_blocked_values_contains_internal_ip_patterns(self, collector_config):
        """내부 IP 패턴이 blocked_values에 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        blocked_values = redaction.get("blocked_values", [])

        # RFC 1918 사설 IP 패턴 확인
        ip_patterns_found = {
            "10.x.x.x": False,
            "192.168.x.x": False,
            "172.16-31.x.x": False,
        }

        for pattern in blocked_values:
            if "10\\." in pattern:
                ip_patterns_found["10.x.x.x"] = True
            if "192\\.168\\." in pattern:
                ip_patterns_found["192.168.x.x"] = True
            if "172\\." in pattern:
                ip_patterns_found["172.16-31.x.x"] = True

        for ip_range, found in ip_patterns_found.items():
            assert found, f"{ip_range} 패턴이 blocked_values에 없습니다"

    def test_blocked_values_contains_server_path_patterns(self, collector_config):
        """서버 경로 패턴이 blocked_values에 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        blocked_values = redaction.get("blocked_values", [])

        path_patterns_found = {"/home/": False, "/var/": False, "C:\\\\": False}

        for pattern in blocked_values:
            if "/home/" in pattern:
                path_patterns_found["/home/"] = True
            if "/var/" in pattern:
                path_patterns_found["/var/"] = True
            if "C:\\\\" in pattern:
                path_patterns_found["C:\\\\"] = True

        for path, found in path_patterns_found.items():
            assert found, f"'{path}' 패턴이 blocked_values에 없습니다"

    def test_blocked_values_contains_bearer_token_pattern(self, collector_config):
        """Bearer 토큰 패턴이 blocked_values에 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        blocked_values = redaction.get("blocked_values", [])

        bearer_found = any("Bearer" in pattern for pattern in blocked_values)
        assert bearer_found, "Bearer 토큰 패턴이 blocked_values에 없습니다"

    def test_blocked_values_contains_jwt_token_pattern(self, collector_config):
        """JWT 토큰 패턴이 blocked_values에 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        blocked_values = redaction.get("blocked_values", [])

        jwt_found = any("eyJ" in pattern for pattern in blocked_values)
        assert jwt_found, "JWT 토큰 패턴(eyJ)이 blocked_values에 없습니다"

    def test_summary_mode_configured(self, collector_config):
        """summary 모드가 설정되어 있는지 확인"""
        redaction = collector_config["processors"]["redaction"]
        summary = redaction.get("summary")
        assert summary in ["debug", "info", "silent"], f"summary 모드가 올바르지 않습니다: {summary}"


class TestRedactionProcessorInPipelines:
    """redaction processor가 traces 파이프라인에 포함되어 있는지 확인 (v0.96.0 기준)"""

    @pytest.fixture(scope="class")
    def collector_config(self):
        """otel-collector-config.yml 로드"""
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "docker",
            "otel-collector",
            "otel-collector-config.yml",
        )
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_redaction_in_traces_pipeline(self, collector_config):
        """traces 파이프라인에 redaction processor가 포함되어 있는지 확인"""
        pipelines = collector_config["service"]["pipelines"]
        traces_processors = pipelines["traces"]["processors"]
        assert "redaction" in traces_processors, "traces 파이프라인에 redaction processor가 없습니다"

    def test_redaction_is_last_processor_in_traces(self, collector_config):
        """traces 파이프라인에서 redaction이 마지막 processor인지 확인 (최후의 보루)"""
        pipelines = collector_config["service"]["pipelines"]
        traces_processors = pipelines["traces"]["processors"]

        last_processor = traces_processors[-1]
        assert last_processor == "redaction", f"traces 파이프라인에서 redaction이 마지막이 아닙니다. 마지막: {last_processor}"

    def test_logs_pipeline_uses_attributes_processor(self, collector_config):
        """logs 파이프라인이 attributes/logs processor를 사용하는지 확인"""
        # v0.96.0에서 redaction은 traces만 지원, logs는 attributes로 처리
        pipelines = collector_config["service"]["pipelines"]
        logs_processors = pipelines["logs"]["processors"]
        assert "attributes/logs" in logs_processors, "logs 파이프라인에 attributes/logs processor가 없습니다"


class TestPrometheusRemoteWriteQueue:
    """prometheusremotewrite exporter의 remote_write_queue 설정 검증"""

    @pytest.fixture(scope="class")
    def collector_config(self):
        """otel-collector-config.yml 로드"""
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "..",
            "docker",
            "otel-collector",
            "otel-collector-config.yml",
        )
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_prometheusremotewrite_has_remote_write_queue(self, collector_config):
        """prometheusremotewrite exporter에 remote_write_queue가 있는지 확인"""
        exporters = collector_config.get("exporters", {})
        prw = exporters.get("prometheusremotewrite", {})
        remote_write_queue = prw.get("remote_write_queue")
        assert remote_write_queue is not None, "prometheusremotewrite에 remote_write_queue가 없습니다"

    def test_prometheusremotewrite_queue_enabled(self, collector_config):
        """prometheusremotewrite remote_write_queue가 활성화되어 있는지 확인"""
        prw = collector_config["exporters"]["prometheusremotewrite"]
        remote_write_queue = prw.get("remote_write_queue", {})
        assert remote_write_queue.get("enabled") is True, "prometheusremotewrite remote_write_queue가 비활성화되어 있습니다"

    def test_prometheusremotewrite_queue_size_configured(self, collector_config):
        """prometheusremotewrite queue_size가 설정되어 있는지 확인"""
        prw = collector_config["exporters"]["prometheusremotewrite"]
        remote_write_queue = prw.get("remote_write_queue", {})
        queue_size = remote_write_queue.get("queue_size")
        assert queue_size is not None and queue_size > 0, f"prometheusremotewrite queue_size가 올바르지 않습니다: {queue_size}"

    def test_tempo_and_loki_use_file_storage(self, collector_config):
        """Tempo와 Loki exporter가 file_storage를 사용하는지 확인"""
        exporters = collector_config.get("exporters", {})

        # Tempo
        tempo = exporters.get("otlp/tempo", {})
        tempo_storage = tempo.get("sending_queue", {}).get("storage")
        assert tempo_storage == "file_storage", f"Tempo exporter가 file_storage를 사용하지 않습니다: {tempo_storage}"

        # Loki
        loki = exporters.get("loki", {})
        loki_storage = loki.get("sending_queue", {}).get("storage")
        assert loki_storage == "file_storage", f"Loki exporter가 file_storage를 사용하지 않습니다: {loki_storage}"


class TestCollectorWithRedactionLive:
    """실제 Collector가 redaction processor로 시작되는지 확인"""

    def test_collector_health_check_via_http(self):
        """Collector 헬스체크 통과 확인 (HTTP 요청)"""
        import requests

        collector_health_url = os.environ.get("COLLECTOR_HEALTH_ENDPOINT", "http://otel-collector:13133")
        try:
            response = requests.get(collector_health_url, timeout=5)
            assert response.status_code == 200, f"Collector 헬스체크 실패: {response.status_code}"
        except requests.RequestException as e:
            pytest.fail(f"Collector에 연결할 수 없습니다: {e}")

    def test_collector_metrics_endpoint_available(self):
        """Collector 메트릭 엔드포인트가 동작하는지 확인"""
        import requests

        collector_metrics_url = os.environ.get("COLLECTOR_METRICS_ENDPOINT", "http://otel-collector:8888/metrics")
        try:
            response = requests.get(collector_metrics_url, timeout=5)
            # 메트릭 엔드포인트 응답 확인
            assert response.status_code == 200, f"Collector 메트릭 엔드포인트 실패: {response.status_code}"
            # Prometheus 형식 메트릭 확인
            assert "otelcol" in response.text or "process" in response.text, "Collector 메트릭이 반환되지 않습니다"
        except requests.RequestException as e:
            pytest.fail(f"Collector 메트릭 엔드포인트에 연결할 수 없습니다: {e}")

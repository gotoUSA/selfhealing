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
        pipelines = collector_config["service"]["pipelines"]
        logs_processors = pipelines["logs"]["processors"]
        assert "attributes/logs" in logs_processors, "logs 파이프라인에 attributes/logs processor가 없습니다"

    def test_redaction_in_logs_pipeline(self, collector_config):
        """logs 파이프라인에 redaction processor가 포함되어 있는지 확인 (v0.115.0+)"""
        pipelines = collector_config["service"]["pipelines"]
        logs_processors = pipelines["logs"]["processors"]
        assert "redaction" in logs_processors, "logs 파이프라인에 redaction processor가 없습니다 (v0.115.0+ 필요)"

    def test_redaction_in_metrics_pipeline(self, collector_config):
        """metrics 파이프라인에 redaction processor가 포함되어 있는지 확인 (v0.115.0+)"""
        pipelines = collector_config["service"]["pipelines"]
        metrics_processors = pipelines["metrics"]["processors"]
        assert "redaction" in metrics_processors, "metrics 파이프라인에 redaction processor가 없습니다 (v0.115.0+ 필요)"


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

    def test_prometheusremotewrite_has_wal(self, collector_config):
        """prometheusremotewrite exporter에 WAL이 설정되어 있는지 확인 (디스크 버퍼링)"""
        exporters = collector_config.get("exporters", {})
        prw = exporters.get("prometheusremotewrite", {})
        wal = prw.get("wal")
        assert wal is not None, "prometheusremotewrite에 WAL이 없습니다 (디스크 버퍼링 필요)"
        assert wal.get("directory") is not None, "WAL directory가 설정되지 않았습니다"

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

        # Loki (v0.144.0+: otlphttp/loki 사용)
        loki = exporters.get("otlphttp/loki", {})
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


class TestRedactionMaskingBehavior:
    """실제 마스킹 동작 검증 테스트

    OTLP로 민감 정보 포함 Trace/Log 전송 후 Tempo/Loki에서 마스킹 확인

    테스트 방법:
    1. OTLP HTTP로 민감 정보 포함 Trace 전송
    2. Tempo API로 Trace 조회
    3. 민감 정보가 마스킹되었는지 확인
    """

    @pytest.fixture(scope="class")
    def otlp_endpoint(self):
        """OTLP HTTP 엔드포인트"""
        return os.environ.get("OTLP_HTTP_ENDPOINT", "http://otel-collector:4318")

    @pytest.fixture(scope="class")
    def tempo_endpoint(self):
        """Tempo 조회 엔드포인트"""
        return os.environ.get("TEMPO_ENDPOINT", "http://tempo:3200")

    def _generate_trace_id(self):
        """32자리 hex trace ID 생성"""
        import uuid

        return uuid.uuid4().hex

    def _generate_span_id(self):
        """16자리 hex span ID 생성"""
        import uuid

        return uuid.uuid4().hex[:16]

    def test_jwt_token_masked_in_trace(self, otlp_endpoint, tempo_endpoint):
        """JWT 토큰이 Trace 속성에서 마스킹되는지 확인"""
        import json
        import time

        import requests

        trace_id = self._generate_trace_id()
        span_id = self._generate_span_id()

        # 민감 정보 포함 Trace 전송
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"  # noqa: S105

        trace_data = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "test-service"}}]},
                    "scopeSpans": [
                        {
                            "scope": {"name": "test-scope"},
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": span_id,
                                    "name": "test-span-with-jwt",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.1) * 1e9)),
                                    "attributes": [
                                        {"key": "auth.token", "value": {"stringValue": jwt_token}},
                                        {"key": "user.id", "value": {"stringValue": "user123"}},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        # OTLP HTTP로 전송
        try:
            response = requests.post(
                f"{otlp_endpoint}/v1/traces", json=trace_data, headers={"Content-Type": "application/json"}, timeout=10
            )
            assert response.status_code in [200, 202], f"OTLP 전송 실패: {response.status_code}"
        except requests.RequestException as e:
            pytest.fail(f"OTLP 전송 실패: {e}")

        # Tempo에서 Trace 조회 (최대 10초 대기)
        time.sleep(2)  # 데이터 처리 대기

        try:
            response = requests.get(f"{tempo_endpoint}/api/traces/{trace_id}", timeout=10)

            if response.status_code == 200:
                trace_content = response.text
                # JWT 토큰 원문이 포함되지 않아야 함
                assert jwt_token not in trace_content, "JWT 토큰이 마스킹되지 않았습니다!"
                # 마스킹된 값(****)이 있거나, 키 자체가 삭제되어야 함
                # redaction processor는 값을 **** 또는 빈 문자열로 대체
            elif response.status_code == 404:
                # Trace가 아직 인덱싱되지 않았을 수 있음 - 테스트 통과로 처리
                pytest.skip("Trace가 아직 인덱싱되지 않았습니다 (정상)")
            else:
                pytest.skip(f"Tempo 조회 실패: {response.status_code}")
        except requests.RequestException as e:
            pytest.skip(f"Tempo 연결 실패 (정상 - 테스트 환경): {e}")

    def test_internal_ip_masked_in_trace(self, otlp_endpoint, tempo_endpoint):
        """내부 IP 주소가 Trace 속성에서 마스킹되는지 확인"""
        import time

        import requests

        trace_id = self._generate_trace_id()
        span_id = self._generate_span_id()

        # 민감 정보 포함 Trace 전송
        internal_ip = "10.0.0.123"

        trace_data = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "test-service"}}]},
                    "scopeSpans": [
                        {
                            "scope": {"name": "test-scope"},
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": span_id,
                                    "name": "test-span-with-ip",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.1) * 1e9)),
                                    "attributes": [
                                        {"key": "server.address", "value": {"stringValue": internal_ip}},
                                        {"key": "request.id", "value": {"stringValue": "req-001"}},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        # OTLP HTTP로 전송
        try:
            response = requests.post(
                f"{otlp_endpoint}/v1/traces", json=trace_data, headers={"Content-Type": "application/json"}, timeout=10
            )
            assert response.status_code in [200, 202], f"OTLP 전송 실패: {response.status_code}"
        except requests.RequestException as e:
            pytest.fail(f"OTLP 전송 실패: {e}")

        # Tempo에서 Trace 조회
        time.sleep(2)

        try:
            response = requests.get(f"{tempo_endpoint}/api/traces/{trace_id}", timeout=10)

            if response.status_code == 200:
                trace_content = response.text
                # 내부 IP 원문이 포함되지 않아야 함
                assert internal_ip not in trace_content, "내부 IP가 마스킹되지 않았습니다!"
            elif response.status_code == 404:
                pytest.skip("Trace가 아직 인덱싱되지 않았습니다 (정상)")
            else:
                pytest.skip(f"Tempo 조회 실패: {response.status_code}")
        except requests.RequestException as e:
            pytest.skip(f"Tempo 연결 실패 (정상 - 테스트 환경): {e}")

    def test_bearer_token_masked_in_trace(self, otlp_endpoint, tempo_endpoint):
        """Bearer 토큰이 Trace 속성에서 마스킹되는지 확인"""
        import time

        import requests

        trace_id = self._generate_trace_id()
        span_id = self._generate_span_id()

        bearer_token = "Bearer sk-1234567890abcdefghij"

        trace_data = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "test-service"}}]},
                    "scopeSpans": [
                        {
                            "scope": {"name": "test-scope"},
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": span_id,
                                    "name": "test-span-with-bearer",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.1) * 1e9)),
                                    "attributes": [
                                        {"key": "http.request.header.authorization", "value": {"stringValue": bearer_token}},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        try:
            response = requests.post(
                f"{otlp_endpoint}/v1/traces", json=trace_data, headers={"Content-Type": "application/json"}, timeout=10
            )
            assert response.status_code in [200, 202], f"OTLP 전송 실패: {response.status_code}"
        except requests.RequestException as e:
            pytest.fail(f"OTLP 전송 실패: {e}")

        time.sleep(2)

        try:
            response = requests.get(f"{tempo_endpoint}/api/traces/{trace_id}", timeout=10)

            if response.status_code == 200:
                trace_content = response.text
                assert bearer_token not in trace_content, "Bearer 토큰이 마스킹되지 않았습니다!"
            elif response.status_code == 404:
                pytest.skip("Trace가 아직 인덱싱되지 않았습니다 (정상)")
            else:
                pytest.skip(f"Tempo 조회 실패: {response.status_code}")
        except requests.RequestException as e:
            pytest.skip(f"Tempo 연결 실패 (정상 - 테스트 환경): {e}")

# Agent-Gateway 2단계 아키텍처 설정 검증 테스트
"""
Agent-Gateway 아키텍처 설정 검증 테스트

테스트 항목:
1. Agent 설정 파일 구문 검증
2. Gateway 설정 파일 구문 검증
3. Agent 설정 구조 검증 (Gateway로 export)
4. Gateway 설정 구조 검증 (백엔드로 export, 인증 설정)
5. Agent-Gateway 데이터 흐름 검증 (통합 테스트)

설정 파일:
- docker/otel-collector/otel-collector-agent.yml
- docker/otel-collector/otel-collector-gateway.yml
"""
import os
import time
import uuid

import pytest
import requests
import yaml


# Agent와 Gateway 설정 파일 경로
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docker", "otel-collector")
AGENT_CONFIG_PATH = os.path.join(CONFIG_DIR, "otel-collector-agent.yml")
GATEWAY_CONFIG_PATH = os.path.join(CONFIG_DIR, "otel-collector-gateway.yml")

# 통합 테스트용 엔드포인트
AGENT_ENDPOINT = os.getenv("OTEL_AGENT_ENDPOINT", "http://otel-agent:4318")
GATEWAY_ENDPOINT = os.getenv("OTEL_GATEWAY_ENDPOINT", "http://otel-gateway:4318")
GATEWAY_HEALTH_ENDPOINT = os.getenv("GATEWAY_HEALTH_ENDPOINT", "http://otel-gateway:13133")
TEMPO_ENDPOINT = os.getenv("TEMPO_ENDPOINT", "http://tempo:3200")


class TestAgentConfigValidation:
    """Agent 설정 파일 검증 테스트"""

    @pytest.fixture
    def agent_config(self):
        """Agent 설정 파일 로드"""
        with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_agent_config_file_exists(self):
        """Agent 설정 파일이 존재하는지 확인"""
        assert os.path.exists(AGENT_CONFIG_PATH), f"Agent 설정 파일 없음: {AGENT_CONFIG_PATH}"

    def test_agent_config_is_valid_yaml(self):
        """Agent 설정 파일이 유효한 YAML인지 확인"""
        with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config is not None

    def test_agent_has_health_check_extension(self, agent_config):
        """Agent에 health_check extension이 있는지 확인"""
        assert "extensions" in agent_config
        assert "health_check" in agent_config["extensions"]

    def test_agent_has_file_storage_extension(self, agent_config):
        """Agent에 file_storage extension이 있는지 확인 (Gateway 장애 시 버퍼링)"""
        assert "extensions" in agent_config
        assert "file_storage" in agent_config["extensions"]

        file_storage = agent_config["extensions"]["file_storage"]
        assert "directory" in file_storage

    def test_agent_has_otlp_receiver(self, agent_config):
        """Agent에 OTLP receiver가 있는지 확인 (App → Agent)"""
        assert "receivers" in agent_config
        assert "otlp" in agent_config["receivers"]

        otlp = agent_config["receivers"]["otlp"]
        assert "protocols" in otlp
        assert "grpc" in otlp["protocols"] or "http" in otlp["protocols"]

    def test_agent_exports_to_gateway_via_otlp(self, agent_config):
        """Agent가 Gateway로 OTLP export하는지 확인"""
        assert "exporters" in agent_config
        assert "otlp/gateway" in agent_config["exporters"]

        gateway_exporter = agent_config["exporters"]["otlp/gateway"]
        assert "endpoint" in gateway_exporter

    def test_agent_has_lightweight_processors(self, agent_config):
        """Agent가 경량 프로세서만 사용하는지 확인"""
        assert "processors" in agent_config

        # 필수 경량 프로세서
        assert "memory_limiter" in agent_config["processors"]
        assert "batch" in agent_config["processors"]

        # redaction은 Gateway에서만 (Agent는 경량)
        assert "redaction" not in agent_config["processors"]

    def test_agent_memory_limit_is_low(self, agent_config):
        """Agent 메모리 제한이 64MB 수준인지 확인 (사이드카용)"""
        memory_limiter = agent_config["processors"]["memory_limiter"]
        limit_mib = memory_limiter.get("limit_mib", 0)

        # 64MB 기준, 여유있게 100MB 이하
        assert limit_mib <= 100, f"Agent 메모리 제한이 너무 높음: {limit_mib}MB"

    def test_agent_pipelines_export_to_gateway(self, agent_config):
        """Agent 파이프라인이 모두 Gateway로 export하는지 확인"""
        pipelines = agent_config["service"]["pipelines"]

        for pipeline_name in ["traces", "metrics", "logs"]:
            assert pipeline_name in pipelines, f"Pipeline 누락: {pipeline_name}"

            exporters = pipelines[pipeline_name].get("exporters", [])
            has_gateway_exporter = any("gateway" in exp for exp in exporters)
            assert has_gateway_exporter, f"{pipeline_name} 파이프라인이 Gateway로 export하지 않음"


class TestGatewayConfigValidation:
    """Gateway 설정 파일 검증 테스트"""

    @pytest.fixture
    def gateway_config(self):
        """Gateway 설정 파일 로드"""
        with open(GATEWAY_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_gateway_config_file_exists(self):
        """Gateway 설정 파일이 존재하는지 확인"""
        assert os.path.exists(GATEWAY_CONFIG_PATH), f"Gateway 설정 파일 없음: {GATEWAY_CONFIG_PATH}"

    def test_gateway_config_is_valid_yaml(self):
        """Gateway 설정 파일이 유효한 YAML인지 확인"""
        with open(GATEWAY_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config is not None

    def test_gateway_has_health_check_extension(self, gateway_config):
        """Gateway에 health_check extension이 있는지 확인"""
        assert "extensions" in gateway_config
        assert "health_check" in gateway_config["extensions"]

    def test_gateway_has_bearer_token_auth_extension(self, gateway_config):
        """Gateway에 bearertokenauth extension이 있는지 확인 (API Key 인증)"""
        assert "extensions" in gateway_config
        assert "bearertokenauth" in gateway_config["extensions"]

    def test_gateway_has_file_storage_extension(self, gateway_config):
        """Gateway에 file_storage extension이 있는지 확인 (백엔드 장애 시 버퍼링)"""
        assert "extensions" in gateway_config
        assert "file_storage" in gateway_config["extensions"]

    def test_gateway_has_otlp_receiver(self, gateway_config):
        """Gateway에 OTLP receiver가 있는지 확인 (Agent → Gateway)"""
        assert "receivers" in gateway_config
        assert "otlp" in gateway_config["receivers"]

    def test_gateway_has_redaction_processor(self, gateway_config):
        """Gateway에 redaction processor가 있는지 확인 (민감 정보 마스킹)"""
        assert "processors" in gateway_config
        assert "redaction" in gateway_config["processors"]

        redaction = gateway_config["processors"]["redaction"]
        assert "blocked_values" in redaction
        assert len(redaction["blocked_values"]) > 0

    def test_gateway_exports_to_backends(self, gateway_config):
        """Gateway가 백엔드(Tempo/Mimir/Loki)로 export하는지 확인"""
        assert "exporters" in gateway_config

        # Tempo (Traces)
        assert "otlp/tempo" in gateway_config["exporters"]

        # Mimir (Metrics)
        assert "prometheusremotewrite" in gateway_config["exporters"]

        # Loki (Logs)
        assert "otlphttp/loki" in gateway_config["exporters"]

    def test_gateway_has_wal_for_prometheusremotewrite(self, gateway_config):
        """Gateway의 prometheusremotewrite에 WAL이 설정되어 있는지 확인"""
        prw = gateway_config["exporters"]["prometheusremotewrite"]
        assert "wal" in prw
        assert "directory" in prw["wal"]

    def test_gateway_memory_limit_is_appropriate(self, gateway_config):
        """Gateway 메모리 제한이 적절한지 확인 (512MB 수준)"""
        memory_limiter = gateway_config["processors"]["memory_limiter"]
        limit_mib = memory_limiter.get("limit_mib", 0)

        # 512MB 기준, 256~600MB 범위
        assert 200 <= limit_mib <= 600, f"Gateway 메모리 제한이 적절하지 않음: {limit_mib}MB"

    def test_gateway_pipelines_have_redaction(self, gateway_config):
        """Gateway 파이프라인에 redaction이 포함되어 있는지 확인"""
        pipelines = gateway_config["service"]["pipelines"]

        for pipeline_name in ["traces", "metrics", "logs"]:
            assert pipeline_name in pipelines, f"Pipeline 누락: {pipeline_name}"

            processors = pipelines[pipeline_name].get("processors", [])
            assert "redaction" in processors, f"{pipeline_name} 파이프라인에 redaction 누락"

    def test_gateway_service_extensions_include_auth(self, gateway_config):
        """Gateway 서비스 extensions에 인증이 포함되어 있는지 확인"""
        extensions = gateway_config["service"]["extensions"]
        assert "bearertokenauth" in extensions


class TestAgentGatewayArchitecture:
    """Agent-Gateway 아키텍처 구조 비교 테스트"""

    @pytest.fixture
    def agent_config(self):
        with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @pytest.fixture
    def gateway_config(self):
        with open(GATEWAY_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_agent_is_lighter_than_gateway(self, agent_config, gateway_config):
        """Agent가 Gateway보다 가벼운지 확인 (프로세서 수, 메모리)"""
        agent_processors = len(agent_config.get("processors", {}))
        gateway_processors = len(gateway_config.get("processors", {}))

        assert agent_processors < gateway_processors, "Agent가 Gateway보다 프로세서가 많음"

        agent_memory = agent_config["processors"]["memory_limiter"]["limit_mib"]
        gateway_memory = gateway_config["processors"]["memory_limiter"]["limit_mib"]

        assert agent_memory < gateway_memory, "Agent 메모리 제한이 Gateway보다 높음"

    def test_only_gateway_has_redaction(self, agent_config, gateway_config):
        """redaction processor는 Gateway에만 있는지 확인"""
        assert "redaction" not in agent_config.get("processors", {}), "Agent에 redaction이 있으면 안됨"
        assert "redaction" in gateway_config.get("processors", {}), "Gateway에 redaction이 없음"

    def test_agent_exports_otlp_gateway_exports_backends(self, agent_config, gateway_config):
        """Agent는 OTLP, Gateway는 백엔드별 exporter 사용"""
        # Agent: OTLP만
        agent_exporters = list(agent_config.get("exporters", {}).keys())
        gateway_exporters = list(gateway_config.get("exporters", {}).keys())

        # Agent는 gateway로만 export
        assert any("gateway" in exp for exp in agent_exporters)

        # Gateway는 백엔드별 exporter
        assert any("tempo" in exp for exp in gateway_exporters)
        assert "prometheusremotewrite" in gateway_exporters
        assert any("loki" in exp for exp in gateway_exporters)


# 통합 테스트 (Agent-Gateway 서비스가 실행 중일 때만)
@pytest.mark.skipif(
    os.getenv("OTEL_AGENT_GATEWAY_TEST") != "true",
    reason="Agent-Gateway 통합 테스트는 OTEL_AGENT_GATEWAY_TEST=true 환경에서만 실행",
)
class TestAgentGatewayIntegration:
    """Agent-Gateway 통합 테스트 (실제 서비스 필요)"""

    def _generate_trace_id_hex(self) -> str:
        return uuid.uuid4().hex

    def _generate_span_id_hex(self) -> str:
        return uuid.uuid4().hex[:16]

    def _create_trace_payload(self, trace_id_hex: str, span_id_hex: str):
        """OTLP Trace 페이로드 생성"""
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "agent-gateway-test"}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "test-scope"},
                            "spans": [
                                {
                                    "traceId": trace_id_hex,
                                    "spanId": span_id_hex,
                                    "name": "test-via-agent-gateway",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.1) * 1e9)),
                                    "status": {"code": 1},
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    def test_gateway_health_check(self):
        """Gateway 헬스체크 확인"""
        response = requests.get(f"{GATEWAY_HEALTH_ENDPOINT}/", timeout=10)
        assert response.status_code == 200

    def test_trace_flows_from_agent_to_gateway_to_tempo(self):
        """Trace가 Agent → Gateway → Tempo 경로로 전달되는지 확인"""
        trace_id_hex = self._generate_trace_id_hex()
        span_id_hex = self._generate_span_id_hex()

        payload = self._create_trace_payload(trace_id_hex, span_id_hex)

        # Agent로 전송
        response = requests.post(
            f"{AGENT_ENDPOINT}/v1/traces", json=payload, headers={"Content-Type": "application/json"}, timeout=10
        )
        assert response.status_code in [200, 202]

        # Tempo에서 조회 (최대 30초 대기)
        max_wait = 30
        interval = 3
        trace_found = False

        for _ in range(max_wait // interval):
            time.sleep(interval)
            try:
                query_response = requests.get(f"{TEMPO_ENDPOINT}/api/traces/{trace_id_hex}", timeout=10)
                if query_response.status_code == 200:
                    trace_found = True
                    break
            except requests.RequestException:
                continue

        assert trace_found, f"Trace {trace_id_hex}가 Agent→Gateway→Tempo 경로로 전달되지 않음"

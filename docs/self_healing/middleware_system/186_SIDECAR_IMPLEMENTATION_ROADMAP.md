# 186. 사이드카 패턴 구현 로드맵

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-05
> **작성 근거**: `selfhealing` 패키지 코드 분석

## 1. 개요

본 문서는 Self-Healing 시스템의 사이드카 패턴 구현을 위한 **단계별 로드맵**을 정의합니다.

---

## 2. 현재 상태 vs 목표 상태

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              현재 상태                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────┐                                                            │
│  │  Python App     │                                                            │
│  │                 │     Direct Import                                          │
│  │  from selfheal  │ ─────────────────────────────────►  ┌──────────────────┐  │
│  │  ing import ... │                                     │ selfhealing pkg  │  │
│  └─────────────────┘                                     └──────────────────┘  │
│                                                                                 │
│  ┌─────────────────┐                                                            │
│  │  Go/Java App    │                                                            │
│  │                 │     ❌ Not Supported                                       │
│  │                 │                                                            │
│  └─────────────────┘                                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              목표 상태                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────┐                                                            │
│  │  Python App     │     Direct Import (기존 유지)                              │
│  │                 │ ─────────────────────────────────►  ┌──────────────────┐  │
│  │                 │                                     │ selfhealing pkg  │  │
│  └─────────────────┘                                     │                  │  │
│                                                          │  ┌────────────┐  │  │
│  ┌─────────────────┐     UDS / gRPC                      │  │   Core     │  │  │
│  │  Go/Java/       │ ─────────────────────────────────►  │  │   Engine   │  │  │
│  │  Node.js App    │                                     │  └────────────┘  │  │
│  │                 │     ✅ Sidecar Pattern              │                  │  │
│  └─────────────────┘                                     └──────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 Phase

### Phase 1: IPC 인터페이스 레이어 (2주)

#### 3.1.1 목표
- UDS 서버/클라이언트 기본 구현
- JSON-RPC 프로토콜 정의
- 기존 서비스와 연동

#### 3.1.2 구현 파일 구조

```
selfhealing/adapters/ipc/           # 신규 생성
├── __init__.py
├── protocol.py                     # JSON-RPC 프로토콜 정의
├── uds_server.py                   # UDS 서버 구현
├── uds_client.py                   # UDS 클라이언트 (테스트용)
├── request_handler.py              # 요청 라우팅 및 처리
└── exceptions.py                   # IPC 관련 예외
```

#### 3.1.3 구현 코드 (예시)

**protocol.py**:
```python
"""
JSON-RPC 2.0 프로토콜 정의.

코드 근거: selfhealing/interfaces/ 디렉토리의 인터페이스 패턴 활용
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import json

@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 요청"""
    method: str
    params: dict[str, Any]
    id: str
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "JsonRpcRequest":
        parsed = json.loads(data)
        return cls(
            method=parsed["method"],
            params=parsed.get("params", {}),
            id=parsed["id"],
            jsonrpc=parsed.get("jsonrpc", "2.0"),
        )

@dataclass
class JsonRpcResponse:
    """JSON-RPC 2.0 응답"""
    id: str
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        data = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error:
            data["error"] = self.error
        else:
            data["result"] = self.result
        return json.dumps(data)
```

**request_handler.py**:
```python
"""
요청 핸들러 - 기존 서비스 래핑.

코드 근거: factory.py의 ProviderRegistry 패턴 활용
"""
from __future__ import annotations
import logging
from typing import Any, Callable

from selfhealing.services import (
    get_circuit_breaker_service,
    get_dlq_service,
)
from selfhealing.services.learning import LearningService

logger = logging.getLogger(__name__)

class RequestHandler:
    """
    IPC 요청을 기존 selfhealing 서비스로 라우팅.

    ProviderRegistry 패턴을 따라 플러그인 방식으로 확장 가능.
    """

    def __init__(self):
        self._cb_service = None
        self._dlq_service = None
        self._learning_service = None
        self._handlers: dict[str, Callable] = {}
        self._register_handlers()

    @property
    def cb_service(self):
        """Lazy initialization - CircuitBreakerService"""
        if self._cb_service is None:
            self._cb_service = get_circuit_breaker_service()
        return self._cb_service

    @property
    def dlq_service(self):
        """Lazy initialization - DLQService"""
        if self._dlq_service is None:
            self._dlq_service = get_dlq_service()
        return self._dlq_service

    @property
    def learning_service(self):
        """Lazy initialization - LearningService"""
        if self._learning_service is None:
            self._learning_service = LearningService()
        return self._learning_service

    def _register_handlers(self):
        """핸들러 등록"""
        self._handlers = {
            # Circuit Breaker
            "circuit_breaker.should_allow": self._cb_should_allow,
            "circuit_breaker.get_state": self._cb_get_state,
            "circuit_breaker.force_open": self._cb_force_open,
            "circuit_breaker.force_close": self._cb_force_close,

            # DLQ
            "dlq.store": self._dlq_store,
            "dlq.is_enabled": self._dlq_is_enabled,

            # Learning
            "learning.get_suggestions": self._learning_suggestions,
        }

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        """요청 처리"""
        handler = self._handlers.get(method)
        if not handler:
            raise ValueError(f"Unknown method: {method}")
        return handler(**params)

    # === Circuit Breaker Handlers ===

    def _cb_should_allow(self, service_name: str) -> dict[str, Any]:
        allowed = self.cb_service.should_allow(service_name)
        state = self.cb_service.get_state(service_name)
        return {
            "allowed": allowed,
            "state": state,
        }

    def _cb_get_state(self, service_name: str) -> dict[str, Any]:
        state = self.cb_service.get_state(service_name)
        return {"state": state}

    def _cb_force_open(
        self,
        service_name: str,
        reason: str,
        controlled_by: str,
    ) -> dict[str, Any]:
        result = self.cb_service.force_open(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
        )
        return {
            "success": result.success,
            "message": result.message,
            "state": result.state,
        }

    def _cb_force_close(
        self,
        service_name: str,
        reason: str,
        controlled_by: str,
        trigger_replay: bool = False,
    ) -> dict[str, Any]:
        result = self.cb_service.force_close(
            service_name=service_name,
            reason=reason,
            controlled_by=controlled_by,
            trigger_replay=trigger_replay,
        )
        return {
            "success": result.success,
            "message": result.message,
            "state": result.state,
        }

    # === DLQ Handlers ===

    def _dlq_store(
        self,
        domain: str,
        failure_type: str,
        error_message: str,
        snapshot_data: dict,
        **kwargs,
    ) -> dict[str, Any]:
        result = self.dlq_service.store_with_snapshot(
            domain=domain,
            failure_type=failure_type,
            error_code=kwargs.get("error_code", ""),
            error_message=error_message,
            snapshot_data=snapshot_data,
            request_data=kwargs.get("request_data"),
            entity_type=kwargs.get("entity_type"),
            entity_id=kwargs.get("entity_id"),
        )
        return {
            "success": result.success,
            "dlq_id": result.dlq_id,
            "message": result.message,
        }

    def _dlq_is_enabled(self) -> dict[str, Any]:
        return {"enabled": self.dlq_service.is_enabled}

    # === Learning Handlers ===

    def _learning_suggestions(self, limit: int = 10) -> dict[str, Any]:
        suggestions = self.learning_service.get_suggestions(limit=limit)
        return {
            "suggestions": [
                {
                    "id": s.id,
                    "type": s.suggestion_type,
                    "description": s.description,
                    "priority": s.priority.value,
                    "confidence": s.confidence,
                }
                for s in suggestions
            ]
        }
```

---

### Phase 2: gRPC 서버 구현 (2주)

#### 3.2.1 목표
- Protobuf 스키마 정의
- gRPC 서버 구현
- 양방향 스트리밍 지원 (이벤트 구독)

#### 3.2.2 구현 파일 구조

```
selfhealing/adapters/grpc/          # 신규 생성
├── __init__.py
├── proto/
│   └── selfhealing.proto           # Protobuf 정의
├── generated/
│   ├── selfhealing_pb2.py          # 생성된 메시지
│   └── selfhealing_pb2_grpc.py     # 생성된 서비스
├── server.py                       # gRPC 서버
└── servicers/
    ├── circuit_breaker.py          # CB 서비스 구현
    ├── dlq.py                      # DLQ 서비스 구현
    └── learning.py                 # Learning 서비스 구현
```

#### 3.2.3 Protobuf 스키마

**selfhealing.proto**:
```protobuf
syntax = "proto3";

package selfhealing;

// ============================================================================
// Circuit Breaker Service
// ============================================================================

service CircuitBreakerService {
  rpc ShouldAllow(ShouldAllowRequest) returns (ShouldAllowResponse);
  rpc GetState(GetStateRequest) returns (GetStateResponse);
  rpc ForceOpen(ForceOpenRequest) returns (OperationResult);
  rpc ForceClose(ForceCloseRequest) returns (OperationResult);

  // Event streaming (server-side)
  rpc SubscribeStateChanges(SubscribeRequest) returns (stream StateChangeEvent);
}

message ShouldAllowRequest {
  string service_name = 1;
}

message ShouldAllowResponse {
  bool allowed = 1;
  string state = 2;
  int32 failure_count = 3;
  int32 success_count = 4;
}

message GetStateRequest {
  string service_name = 1;
}

message GetStateResponse {
  string state = 1;
  string opened_at = 2;
  string last_failure_at = 3;
}

message ForceOpenRequest {
  string service_name = 1;
  string reason = 2;
  string controlled_by = 3;
}

message ForceCloseRequest {
  string service_name = 1;
  string reason = 2;
  string controlled_by = 3;
  bool trigger_replay = 4;
}

message OperationResult {
  bool success = 1;
  string message = 2;
  string state = 3;
}

message SubscribeRequest {
  repeated string service_names = 1;  // empty = all services
}

message StateChangeEvent {
  string service_name = 1;
  string old_state = 2;
  string new_state = 3;
  string reason = 4;
  string timestamp = 5;
}

// ============================================================================
// DLQ Service
// ============================================================================

service DLQService {
  rpc Store(StoreRequest) returns (StoreResponse);
  rpc IsEnabled(Empty) returns (IsEnabledResponse);
}

message Empty {}

message StoreRequest {
  string domain = 1;
  string failure_type = 2;
  string error_code = 3;
  string error_message = 4;
  bytes snapshot_data = 5;    // JSON serialized
  bytes request_data = 6;     // JSON serialized
  optional string entity_type = 7;
  optional string entity_id = 8;
  optional int64 user_id = 9;
}

message StoreResponse {
  bool success = 1;
  int64 dlq_id = 2;
  string message = 3;
}

message IsEnabledResponse {
  bool enabled = 1;
}

// ============================================================================
// Learning Service
// ============================================================================

service LearningService {
  rpc GetSuggestions(GetSuggestionsRequest) returns (SuggestionList);
  rpc RecordSuccess(RecordRequest) returns (Empty);
  rpc RecordFailure(RecordFailureRequest) returns (Empty);
}

message GetSuggestionsRequest {
  int32 limit = 1;
}

message SuggestionList {
  repeated Suggestion suggestions = 1;
}

message Suggestion {
  string id = 1;
  string type = 2;
  string description = 3;
  string priority = 4;
  float confidence = 5;
}

message RecordRequest {
  string pattern_type = 1;
  bytes context = 2;  // JSON serialized
}

message RecordFailureRequest {
  string pattern_type = 1;
  bytes context = 2;
  string error = 3;
}
```

---

### Phase 3: 사이드카 컨테이너 (1주)

#### 3.3.1 Dockerfile

```dockerfile
# Dockerfile.sidecar
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy selfhealing package
COPY packages/selfhealing-python/src/selfhealing /app/selfhealing

# Copy sidecar entrypoint
COPY sidecar/entrypoint.py /app/entrypoint.py

# Expose ports
EXPOSE 50051  # gRPC
# UDS is mounted via volume

# Health check
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import grpc; ch = grpc.insecure_channel('localhost:50051'); grpc.channel_ready_future(ch).result(timeout=1)"

ENTRYPOINT ["python", "entrypoint.py"]
```

#### 3.3.2 Kubernetes Sidecar 설정

```yaml
# k8s/sidecar-injection.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-go-app
spec:
  template:
    spec:
      containers:
      # Main application
      - name: app
        image: my-go-app:latest
        volumeMounts:
        - name: selfhealing-socket
          mountPath: /tmp
        env:
        - name: SELFHEALING_SOCKET
          value: "/tmp/selfhealing.sock"

      # Selfhealing sidecar
      - name: selfhealing-sidecar
        image: selfhealing-sidecar:latest
        ports:
        - containerPort: 50051
          name: grpc
        volumeMounts:
        - name: selfhealing-socket
          mountPath: /tmp
        env:
        - name: SELFHEALING_MODE
          value: "sidecar"
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: selfhealing-secrets
              key: redis-url
        resources:
          requests:
            memory: "64Mi"
            cpu: "50m"
          limits:
            memory: "128Mi"
            cpu: "200m"

      volumes:
      - name: selfhealing-socket
        emptyDir: {}
```

---

### Phase 4: 클라이언트 SDK (2주)

#### 3.4.1 언어별 SDK 구조

```
selfhealing-sdks/
├── go/
│   ├── go.mod
│   ├── client.go
│   ├── circuit_breaker.go
│   ├── dlq.go
│   └── learning.go
├── java/
│   ├── pom.xml
│   └── src/main/java/com/selfhealing/
│       ├── SelfHealingClient.java
│       ├── CircuitBreakerClient.java
│       └── DLQClient.java
└── nodejs/
    ├── package.json
    ├── src/
    │   ├── client.ts
    │   └── types.ts
    └── dist/
```

---

## 4. 타임라인

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           구현 타임라인 (7주)                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Week 1-2: Phase 1 - IPC 인터페이스                                             │
│  ┌────────────────────────────────────────┐                                     │
│  │ • UDS 서버/클라이언트                  │                                     │
│  │ • JSON-RPC 프로토콜                    │                                     │
│  │ • RequestHandler 구현                  │                                     │
│  │ • 단위 테스트                          │                                     │
│  └────────────────────────────────────────┘                                     │
│                                                                                 │
│  Week 3-4: Phase 2 - gRPC 서버                                                  │
│  ┌────────────────────────────────────────┐                                     │
│  │ • Protobuf 스키마 정의                 │                                     │
│  │ • gRPC 서비스 구현                     │                                     │
│  │ • 스트리밍 이벤트 구독                 │                                     │
│  │ • 통합 테스트                          │                                     │
│  └────────────────────────────────────────┘                                     │
│                                                                                 │
│  Week 5: Phase 3 - 사이드카 컨테이너                                            │
│  ┌────────────────────────────────────────┐                                     │
│  │ • Dockerfile 작성                      │                                     │
│  │ • K8s 매니페스트                       │                                     │
│  │ • Health check / Readiness             │                                     │
│  │ • E2E 테스트                           │                                     │
│  └────────────────────────────────────────┘                                     │
│                                                                                 │
│  Week 6-7: Phase 4 - 클라이언트 SDK                                             │
│  ┌────────────────────────────────────────┐                                     │
│  │ • Go SDK                               │                                     │
│  │ • Java SDK                             │                                     │
│  │ • Node.js SDK                          │                                     │
│  │ • 문서화                               │                                     │
│  └────────────────────────────────────────┘                                     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 테스트 전략

### 5.1 단위 테스트

```python
# tests/unit/adapters/ipc/test_request_handler.py
import pytest
from selfhealing.adapters.ipc.request_handler import RequestHandler

class TestRequestHandler:
    def test_cb_should_allow_returns_dict(self):
        handler = RequestHandler()
        result = handler.handle(
            "circuit_breaker.should_allow",
            {"service_name": "test_service"}
        )
        assert "allowed" in result
        assert "state" in result

    def test_unknown_method_raises_error(self):
        handler = RequestHandler()
        with pytest.raises(ValueError, match="Unknown method"):
            handler.handle("unknown.method", {})
```

### 5.2 통합 테스트

```python
# tests/integration/test_sidecar_e2e.py
import subprocess
import time
import socket
import json

class TestSidecarE2E:
    @pytest.fixture(scope="class")
    def sidecar_server(self):
        """사이드카 서버 프로세스 시작"""
        proc = subprocess.Popen(
            ["python", "-m", "selfhealing.adapters.ipc.uds_server"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)  # 서버 시작 대기
        yield proc
        proc.terminate()

    def test_cb_should_allow_via_uds(self, sidecar_server):
        """UDS를 통한 CB 체크"""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect("/tmp/selfhealing.sock")

        request = {
            "jsonrpc": "2.0",
            "method": "circuit_breaker.should_allow",
            "params": {"service_name": "test"},
            "id": "1",
        }
        sock.send(json.dumps(request).encode() + b"\n")

        response = json.loads(sock.recv(4096).decode())
        assert response["result"]["allowed"] is True
```

---

## 6. 관련 문서

| 문서 번호 | 제목 |
|-----------|------|
| [183](183_SIDECAR_PATTERN_OVERVIEW.md) | 사이드카 패턴 개요 |
| [184](184_SIDECAR_COMMUNICATION_LAYER.md) | 통신 레이어 설계 |
| [185](185_SIDECAR_PERFORMANCE_ANALYSIS.md) | 성능 분석 |

---

## 7. 체크리스트

### Phase 1 체크리스트
- [x] `adapters/ipc/protocol.py` 생성
- [x] `adapters/ipc/request_handler.py` 생성
- [x] `adapters/ipc/uds_server.py` 생성
- [x] `adapters/ipc/uds_client.py` 생성 (테스트용)
- [x] `adapters/ipc/exceptions.py` 생성
- [x] 단위 테스트 작성
- [ ] 통합 테스트 작성

### Phase 2 체크리스트
- [x] `adapters/grpc/proto/selfhealing.proto` 생성
- [ ] Protobuf 컴파일 스크립트
- [x] gRPC 서버 구현
- [x] 스트리밍 이벤트 구현
- [ ] 통합 테스트

### Phase 3 체크리스트
- [x] Dockerfile.sidecar 생성
- [x] K8s 매니페스트 작성
- [ ] CI/CD 파이프라인 추가
- [ ] E2E 테스트
- [x] sidecar/entrypoint.py 생성

### Phase 4 체크리스트
- [ ] Go SDK 구현
- [ ] Java SDK 구현
- [ ] Node.js SDK 구현
- [ ] SDK 문서화
- [ ] 예제 코드 작성

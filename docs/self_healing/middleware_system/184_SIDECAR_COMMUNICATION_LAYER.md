# 184. 사이드카 통신 레이어 설계

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-05
> **작성 근거**: `selfhealing` 패키지 인터페이스 분석

## 1. 개요

본 문서는 타언어 애플리케이션이 Self-Healing 시스템을 사용할 수 있도록 하는 **통신 레이어**를 설계합니다.

---

## 2. 통신 방식 비교

### 2.1 UDS (Unix Domain Socket)

```
┌────────────────────────────────────────────────────────────────┐
│                     Same Host / Pod                             │
│                                                                 │
│  ┌─────────────┐                      ┌─────────────────────┐  │
│  │  Go App     │                      │  Sidecar            │  │
│  │             │                      │                     │  │
│  │  ┌───────┐  │   /tmp/selfhealing   │  ┌───────────────┐  │  │
│  │  │ UDS   │  │        .sock         │  │ UDS Server    │  │  │
│  │  │Client │══╬══════════════════════╬══│               │  │  │
│  │  └───────┘  │     (No Network      │  │ ┌───────────┐ │  │  │
│  │             │      Stack)          │  │ │selfhealing│ │  │  │
│  │             │                      │  │ │  core     │ │  │  │
│  └─────────────┘                      │  │ └───────────┘ │  │  │
│                                       │  └───────────────┘  │  │
│                                       └─────────────────────┘  │
└────────────────────────────────────────────────────────────────┘

장점:
- 네트워크 스택 우회 → 50% 이상 오버헤드 절감
- 커널 내부 파이프라인으로 데이터 전송
- 파일 시스템 권한으로 보안 (소켓 파일 접근 제어)

단점:
- 동일 머신에서만 사용 가능
- Windows는 네이티브 지원 없음 (WSL 필요)
```

### 2.2 gRPC (localhost)

```
┌────────────────────────────────────────────────────────────────┐
│                     Same Host / Pod                             │
│                                                                 │
│  ┌─────────────┐                      ┌─────────────────────┐  │
│  │  Java App   │                      │  Sidecar            │  │
│  │             │                      │                     │  │
│  │  ┌───────┐  │   localhost:50051    │  ┌───────────────┐  │  │
│  │  │ gRPC  │  │      (HTTP/2)        │  │ gRPC Server   │  │  │
│  │  │Stub   │──┼──────────────────────┼──│ (Protobuf)    │  │  │
│  │  └───────┘  │                      │  │ ┌───────────┐ │  │  │
│  │             │                      │  │ │selfhealing│ │  │  │
│  │             │                      │  │ │  core     │ │  │  │
│  └─────────────┘                      │  │ └───────────┘ │  │  │
│                                       │  └───────────────┘  │  │
└────────────────────────────────────────────────────────────────┘

장점:
- 언어 독립적 (모든 주요 언어 지원)
- Protobuf로 효율적 직렬화
- 양방향 스트리밍 지원
- 강타입 API 정의

단점:
- UDS보다 약간 높은 오버헤드
- TLS 설정 복잡도
```

---

## 3. 현재 인터페이스와 매핑

### 3.1 CircuitBreakerService API

**코드 근거** (`services/circuit_breaker/service.py`):
```python
class CircuitBreakerService:
    def should_allow(self, service_name: str) -> bool: ...
    def get_state(self, service_name: str) -> str: ...
    def force_open(self, service_name: str, reason: str, controlled_by: str) -> CircuitBreakerResult: ...
    def force_close(self, service_name: str, reason: str, controlled_by: str, trigger_replay: bool) -> CircuitBreakerResult: ...
```

**gRPC 매핑** (제안):
```protobuf
service CircuitBreakerService {
  rpc ShouldAllow(ShouldAllowRequest) returns (ShouldAllowResponse);
  rpc GetState(GetStateRequest) returns (GetStateResponse);
  rpc ForceOpen(ForceOpenRequest) returns (CircuitBreakerResult);
  rpc ForceClose(ForceCloseRequest) returns (CircuitBreakerResult);
}

message ShouldAllowRequest {
  string service_name = 1;
}

message ShouldAllowResponse {
  bool allowed = 1;
  string state = 2;  // "closed", "open", "half_open"
}
```

### 3.2 DLQService API

**코드 근거** (`services/dlq/base.py`):
```python
class DLQServiceBase:
    def is_enabled(self) -> bool: ...

# store_operations.py
class StoreOperationsMixin:
    def store_with_snapshot(
        self,
        domain: str,
        failure_type: str,
        error_code: str,
        error_message: str,
        snapshot_data: dict,
        request_data: dict | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
        max_retries: int | None = None,
        metadata: dict | None = None,
        request: Any = None,
    ) -> DLQEntryResult: ...
```

**gRPC 매핑** (제안):
```protobuf
service DLQService {
  rpc Store(StoreRequest) returns (StoreResponse);
  rpc Replay(ReplayRequest) returns (ReplayResponse);
  rpc GetEntry(GetEntryRequest) returns (DLQEntry);
}

message StoreRequest {
  string domain = 1;
  string failure_type = 2;
  string error_code = 3;
  string error_message = 4;
  bytes snapshot_data = 5;   // JSON serialized
  bytes request_data = 6;    // JSON serialized
  optional string entity_type = 7;
  optional string entity_id = 8;
  optional int64 user_id = 9;
  optional int32 max_retries = 10;
}
```

### 3.3 LearningService API

**코드 근거** (`services/learning/service.py`):
```python
class LearningService:
    def record_success(self, pattern_type: str, context: dict) -> None: ...
    def record_failure(self, pattern_type: str, context: dict, error: str) -> None: ...
    def get_suggestions(self, limit: int = 10) -> list[Suggestion]: ...
    def analyze_patterns(self) -> list[LearningPattern]: ...
```

**gRPC 매핑** (제안):
```protobuf
service LearningService {
  rpc RecordSuccess(RecordRequest) returns (Empty);
  rpc RecordFailure(RecordFailureRequest) returns (Empty);
  rpc GetSuggestions(GetSuggestionsRequest) returns (SuggestionList);
  rpc AnalyzePatterns(Empty) returns (PatternList);
}
```

---

## 4. 어댑터 계층 구조

### 4.1 제안 아키텍처

```
selfhealing/adapters/
├── ipc/                          # IPC 통신 어댑터 (신규)
│   ├── __init__.py
│   ├── uds_server.py             # UDS 서버
│   ├── uds_client.py             # UDS 클라이언트 (테스트용)
│   ├── grpc_server.py            # gRPC 서버
│   └── protocol/
│       ├── selfhealing.proto     # Protobuf 정의
│       └── selfhealing_pb2.py    # 생성된 Python 코드
│
├── cache/                        # 기존 캐시 어댑터
│   ├── redis_adapter.py
│   └── memory_adapter.py
│
└── kafka/                        # 기존 Kafka 어댑터
    ├── producer.py
    └── consumer.py
```

### 4.2 UDS 서버 설계

**ProviderRegistry 확장 방식** (`factory.py` 패턴 활용):
```python
# adapters/ipc/uds_server.py (신규 구현)
import socket
import json
from typing import Any

class UDSServer:
    """
    Unix Domain Socket 기반 Self-Healing API 서버.

    기존 서비스를 감싸서 IPC로 노출:
    - CircuitBreakerService
    - DLQService
    - LearningService
    """

    SOCKET_PATH = "/tmp/selfhealing.sock"

    def __init__(self):
        # 기존 서비스 인스턴스 재사용
        from selfhealing.services import (
            get_circuit_breaker_service,
            get_dlq_service,
        )
        from selfhealing.services.learning import LearningService

        self.cb_service = get_circuit_breaker_service()
        self.dlq_service = get_dlq_service()
        self.learning_service = LearningService()

    def handle_request(self, request: dict) -> dict:
        """요청 라우팅 및 처리"""
        method = request.get("method")
        params = request.get("params", {})

        handlers = {
            "circuit_breaker.should_allow": self._cb_should_allow,
            "circuit_breaker.get_state": self._cb_get_state,
            "circuit_breaker.force_open": self._cb_force_open,
            "dlq.store": self._dlq_store,
            "learning.get_suggestions": self._learning_suggestions,
        }

        handler = handlers.get(method)
        if not handler:
            return {"error": f"Unknown method: {method}"}

        return handler(**params)
```

---

## 5. 메시지 프로토콜

### 5.1 JSON-RPC 스타일 (UDS용)

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "circuit_breaker.should_allow",
  "params": {
    "service_name": "payment_gateway"
  },
  "id": "req-001"
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "allowed": true,
    "state": "closed",
    "failure_count": 2,
    "success_count": 150
  },
  "id": "req-001"
}
```

### 5.2 에러 응답

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32601,
    "message": "Method not found",
    "data": {
      "method": "unknown.method"
    }
  },
  "id": "req-001"
}
```

---

## 6. 클라이언트 SDK 가이드

### 6.1 Go 클라이언트 (예시)

```go
package selfhealing

import (
    "encoding/json"
    "net"
)

type Client struct {
    conn net.Conn
}

func NewClient(socketPath string) (*Client, error) {
    conn, err := net.Dial("unix", socketPath)
    if err != nil {
        return nil, err
    }
    return &Client{conn: conn}, nil
}

func (c *Client) ShouldAllow(serviceName string) (bool, error) {
    request := map[string]interface{}{
        "jsonrpc": "2.0",
        "method":  "circuit_breaker.should_allow",
        "params":  map[string]string{"service_name": serviceName},
        "id":      "1",
    }

    // Send request
    encoder := json.NewEncoder(c.conn)
    if err := encoder.Encode(request); err != nil {
        return false, err
    }

    // Read response
    var response map[string]interface{}
    decoder := json.NewDecoder(c.conn)
    if err := decoder.Decode(&response); err != nil {
        return false, err
    }

    result := response["result"].(map[string]interface{})
    return result["allowed"].(bool), nil
}
```

### 6.2 Java 클라이언트 (예시)

```java
public class SelfHealingClient {
    private final ManagedChannel channel;
    private final CircuitBreakerServiceGrpc.CircuitBreakerServiceBlockingStub stub;

    public SelfHealingClient(String host, int port) {
        channel = ManagedChannelBuilder.forAddress(host, port)
            .usePlaintext()  // localhost only
            .build();
        stub = CircuitBreakerServiceGrpc.newBlockingStub(channel);
    }

    public boolean shouldAllow(String serviceName) {
        ShouldAllowRequest request = ShouldAllowRequest.newBuilder()
            .setServiceName(serviceName)
            .build();
        ShouldAllowResponse response = stub.shouldAllow(request);
        return response.getAllowed();
    }
}
```

---

## 7. 보안 고려사항

### 7.1 UDS 보안

```bash
# 소켓 파일 권한 설정
chmod 660 /tmp/selfhealing.sock
chown app:app /tmp/selfhealing.sock

# 동일 그룹의 프로세스만 접근 가능
```

### 7.2 gRPC mTLS (프로덕션)

```yaml
# sidecar-config.yaml
grpc:
  port: 50051
  tls:
    enabled: true
    cert_file: /etc/selfhealing/server.crt
    key_file: /etc/selfhealing/server.key
    ca_file: /etc/selfhealing/ca.crt
    client_auth: required
```

---

## 8. 관련 문서

| 문서 번호 | 제목 |
|-----------|------|
| [183](183_SIDECAR_PATTERN_OVERVIEW.md) | 사이드카 패턴 개요 |
| [185](185_SIDECAR_PERFORMANCE_ANALYSIS.md) | 성능 분석 |
| [186](186_SIDECAR_IMPLEMENTATION_ROADMAP.md) | 구현 로드맵 |

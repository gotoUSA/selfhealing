# 183. 사이드카 패턴 아키텍처 개요

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-05
> **작성 근거**: `selfhealing` 패키지 코드 분석

## 1. 개요

본 문서는 Self-Healing 시스템을 **사이드카 패턴**으로 확장하여 다양한 환경(Python, Go, Java, Node.js 등)에서 사용할 수 있도록 하는 아키텍처를 설명합니다.

### 1.1 목표

| 목표 | 설명 |
|------|------|
| **이식성** | 클라우드, 온프레미스, 하이브리드 환경 지원 |
| **언어 독립성** | Python 외 타 언어에서도 동일 기능 사용 |
| **성능 최적화** | UDS, Shared Memory를 통한 오버헤드 최소화 |
| **코어 로직 통합** | 단일 코어 엔진으로 일관성 유지 |

---

## 2. 현재 아키텍처 분석 (코드 근거)

### 2.1 인터페이스 기반 설계

현재 시스템은 이미 **추상화된 인터페이스** 기반으로 설계되어 있습니다:

```
selfhealing/interfaces/
├── cache_provider.py     # CacheProviderInterface, DistributedLock
├── repositories.py       # FailedOperationRepository, CircuitBreakerStateRepository
├── task_queue.py         # TaskQueueInterface
├── statistics.py         # StatisticsRepositoryInterface
└── audit_adapter.py      # AuditLogAdapter
```

**코드 근거** (`interfaces/cache_provider.py`):
```python
class DistributedLock(ABC):
    """
    Distributed lock interface for cross-process synchronization.

    Implementations:
        - RedisDistributedLock (Redis-based)
        - InMemoryLock (for testing - single process only)
    """

    @abstractmethod
    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        pass
```

### 2.2 ProviderRegistry 패턴

**코드 근거** (`factory.py`):
```python
class ProviderRegistry:
    """Central registry for all pluggable components."""

    _cache_providers: dict[str, type] = {}
    _task_queues: dict[str, type] = {}
    _failed_op_repos: dict[str, type] = {}
    _circuit_breaker_repos: dict[str, type] = {}

    @classmethod
    def register_cache(cls, name: str, provider_class: type) -> None:
        cls._cache_providers[name] = provider_class
```

이 구조 덕분에 **새로운 통신 어댑터** 추가가 용이합니다.

### 2.3 비동기 논블로킹 처리

**코드 근거** (`utils/async_logger.py`):
```python
class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거

    주요 특징:
    - Priority Queue: CRITICAL 이벤트 우선 처리
    - ThreadPoolExecutor: CRITICAL 이벤트 스레드 풀 (스레드 폭발 방지)
    - WAL-First: 메모리 큐 전에 WAL 기록 (데이터 유실 방지)
    """

    _critical_executor: ThreadPoolExecutor | None = None
    CRITICAL_EXECUTOR_MAX_WORKERS: int = 5
```

---

## 3. Python vs 타언어 아키텍처 비교

### 3.1 Python 애플리케이션 (직접 호출)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Python Application Process                          │
│                                                                              │
│  ┌──────────────────┐      직접 함수 호출       ┌──────────────────────────┐ │
│  │                  │  ─────────────────────►  │                          │ │
│  │   Python App     │                          │   selfhealing (library)  │ │
│  │   (Django,       │  ◄─────────────────────  │                          │ │
│  │    FastAPI,      │      Python 객체 반환     │   • CircuitBreaker       │ │
│  │    Flask)        │                          │   • DLQ Service          │ │
│  │                  │                          │   • Learning Engine      │ │
│  └──────────────────┘                          │   • Decision Engine      │ │
│           │                                    │                          │ │
│           │                                    └───────────┬──────────────┘ │
│           │                                                │                │
│           │  ┌─────────────────────────────────────────────┼──────────────┐ │
│           │  │              Shared Resources               │              │ │
│           │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐     │              │ │
│           └──┼─►│  Redis  │  │  Kafka  │  │ Postgres│◄────┘              │ │
│              │  └─────────┘  └─────────┘  └─────────┘                    │ │
│              └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘

통신 방식: In-Process (함수 호출)
오버헤드: 없음 (0 latency)
직렬화: 불필요 (Python 객체 직접 전달)
```

### 3.2 타언어 애플리케이션 (사이드카 패턴)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Host Machine / Pod                              │
│                                                                              │
│  ┌──────────────────┐                          ┌──────────────────────────┐ │
│  │                  │         UDS/gRPC         │                          │ │
│  │   Go/Java/       │  ════════════════════►  │   Sidecar Container      │ │
│  │   Node.js App    │                          │                          │ │
│  │                  │  ◄════════════════════  │   ┌──────────────────┐   │ │
│  │                  │      JSON/Protobuf       │   │  gRPC Server     │   │ │
│  └──────────────────┘                          │   └────────┬─────────┘   │ │
│           │                                    │            │             │ │
│           │                                    │   ┌────────▼─────────┐   │ │
│           │                                    │   │  selfhealing     │   │ │
│           │                                    │   │  (Python Core)   │   │ │
│           │                                    │   │                  │   │ │
│           │                                    │   │  • CircuitBreaker│   │ │
│           │                                    │   │  • DLQ Service   │   │ │
│           │                                    │   │  • Learning      │   │ │
│           │                                    │   └────────┬─────────┘   │ │
│           │                                    └────────────┼─────────────┘ │
│           │                                                 │               │
│           │  ┌──────────────────────────────────────────────┼─────────────┐ │
│           │  │              Shared Resources                │             │ │
│           │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐      │             │ │
│           └──┼─►│  Redis  │  │  Kafka  │  │ Postgres│◄─────┘             │ │
│              │  └─────────┘  └─────────┘  └─────────┘                    │ │
│              └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘

통신 방식: UDS (Unix Domain Socket) 또는 localhost gRPC
오버헤드: 직렬화/역직렬화 + IPC
직렬화: JSON 또는 Protobuf
```

---

## 4. 통신 방식별 성능 비교

### 4.1 이론적 오버헤드

| 통신 방식 | Latency (P50) | Latency (P99) | 직렬화 비용 | 적합 환경 |
|-----------|---------------|---------------|-------------|-----------|
| **Direct Call** | ~0.01ms | ~0.1ms | 없음 | Python 앱 |
| **UDS** | ~0.1ms | ~0.5ms | 필요 | 동일 머신 |
| **gRPC (localhost)** | ~0.3ms | ~1.0ms | Protobuf | 컨테이너 |
| **TCP/IP (localhost)** | ~0.5ms | ~2.0ms | JSON | 레거시 |
| **TCP/IP (network)** | ~1-10ms | ~50ms+ | JSON | 원격 |

### 4.2 현재 시스템 병목 분석

**코드 근거** (`core/hedging/executor.py`):
```python
class HedgingExecutor:
    """
    여러 후보 함수를 ThreadPoolExecutor로 병렬 실행하고,
    가장 먼저 성공한 결과를 반환합니다.
    """
    _executor: ThreadPoolExecutor | None = None

    @classmethod
    def _get_executor(cls, max_workers: int = 10) -> ThreadPoolExecutor:
        with cls._lock:
            if cls._executor is None or cls._executor._shutdown:
                cls._executor = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="hedging_"
                )
        return cls._executor
```

현재 시스템의 주요 I/O 병목:
1. **Redis 통신**: 평균 1-5ms
2. **Kafka 발행**: 평균 2-10ms
3. **DB 쿼리**: 평균 5-50ms

→ UDS 오버헤드 (~0.1ms)는 **전체 요청 시간의 1% 미만**

---

## 5. 코어 로직 분리 현황

현재 코드베이스에서 **프레임워크 독립적 핵심 로직**:

```
selfhealing/core/
├── decision_engine.py      # 자가 학습 추천 엔진
├── backoff.py              # 백오프 계산기
├── circuit_breaker.py      # CB 상태 머신 (순수 로직)
├── hedging/                # Hedging 전략
│   ├── executor.py
│   └── result_validator.py
└── safety_bounds.py        # 안전 경계 검증

selfhealing/services/
├── circuit_breaker/        # CB 서비스 (비즈니스 로직)
├── dlq/                    # DLQ 서비스
├── learning/               # 자가 학습 서비스
└── event_bus.py            # 이벤트 버스
```

**코드 근거** (`core/decision_engine.py`):
```python
class DecisionEngine:
    """
    조정 결정 엔진

    메트릭 패턴을 분석하여 파라미터 조정 제안
    Netflix Hystrix, Google Autopilot 스타일의 자율 조정 엔진

    기본 규칙:
    - timeout_ms: P99 레이턴시가 타임아웃의 80% 이상이면 상향
    - retry_count: 재시도 소진율이 10% 이상이면 증가
    - circuit_breaker_threshold: 에러율이 CB 임계값에 근접하면 상향
    """
```

---

## 6. 관련 문서

| 문서 번호 | 제목 | 설명 |
|-----------|------|------|
| [184](184_SIDECAR_COMMUNICATION_LAYER.md) | 사이드카 통신 레이어 | UDS, gRPC 어댑터 설계 |
| [185](185_SIDECAR_PERFORMANCE_ANALYSIS.md) | 성능 분석 | 상세 벤치마크 및 최적화 |
| [186](186_SIDECAR_IMPLEMENTATION_ROADMAP.md) | 구현 로드맵 | 단계별 구현 계획 |

---

## 7. 요약

| 항목 | Python 앱 | 타언어 앱 (사이드카) |
|------|-----------|---------------------|
| **통신 방식** | Direct Call | UDS/gRPC |
| **추가 오버헤드** | 0ms | ~0.1-0.3ms |
| **직렬화** | 불필요 | JSON/Protobuf |
| **코어 로직** | 동일 | 동일 |
| **배포 복잡도** | 낮음 | 중간 (사이드카 컨테이너) |
| **리소스 사용** | 낮음 | 중간 (별도 프로세스) |

**결론**: 현재 아키텍처는 인터페이스 기반 설계로 사이드카 패턴 확장에 **충분히 준비**되어 있으며, 핵심 로직 변경 없이 **통신 어댑터만 추가**하면 됩니다.

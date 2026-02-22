# 267. Cell External API Context — Trust Boundary 제어 및 수신 검증

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Depends**: [263_CELL_TAGGER.md](263_CELL_TAGGER.md), [266_CELL_OTEL_PROPAGATION.md](266_CELL_OTEL_PROPAGATION.md)

---

## 0. 요약

266에서 OTel Baggage 기반 `cell_id` 전파 인프라가 **이미 완성**되었다.
267은 이를 활용하여 다음 3가지 보안·안정성 제어를 구현한다:

1. **Trust Boundary 필터링** — 내부 MSA에만 컨텍스트 전파, 외부 서드파티에는 차단
2. **수신 측 헤더 위조 방지** — CIDR 기반 Trust 검증 후 조건부 수용
3. **Topology Mismatch 방어** — 수신된 `cell_id` 유효성 검증 + 로컬 폴백 + 메트릭

**핵심 결정**: `X-Cell-Id` 수동 헤더 주입은 생략한다. 266의 OTel Baggage가 유일한 전파 수단이다.

---

## 1. 현재 상태 분석 — 266 이후

### 1.1 이미 동작하는 전파 파이프라인

266 구현으로 다음 파이프라인이 **이미 완전 동작** 중이다:

| 구간 | 동작 | 코드 근거 |
|------|------|-----------|
| ContextVar → Baggage 동기화 | `sync_contextvars_to_baggage()` | `observability/baggage.py` L91-116 |
| Baggage 키 매핑 | `_CONTEXTVAR_BAGGAGE_MAP["cell_id"]` 등록됨 | `observability/baggage.py` L29-33 |
| 송신 자동 주입 | `_execute_request()`에서 매 요청 전 Baggage 동기화 | `services/http_client.py` L193-196 |
| `RequestsInstrumentor` inject | `traceparent` + `baggage` 헤더 자동 주입 | `observability/__init__.py` L312 |
| 수신 측 복원 | `BaggageSyncMiddleware.restore_contextvars_from_baggage()` | `api/django/cell/middleware.py` L117 |

### 1.2 X-Cell-Id 수동 주입을 생략하는 근거

| 기준 | 판정 | 근거 |
|------|------|------|
| 266 Baggage 완성 여부 | ✅ 완성 | `_CONTEXTVAR_BAGGAGE_MAP`에 `cell_id` 이미 등록 |
| `_execute_request()` 동기화 | ✅ 동작 | L193 `baggage_token = sync_contextvars_to_baggage()` |
| 수신 측 복원 | ✅ 동작 | `BaggageSyncMiddleware`가 `restore_contextvars_from_baggage()` 호출 |
| OTel 비활성 환경 | 문제없음 | `CellTopologySettings.enabled`도 비활성이므로 전파 불필요 |

따라서 `_get_headers()`에 `X-Cell-Id` 수동 주입 코드를 추가하면
**즉시 deprecated되는 기술 부채**가 된다. Baggage 전파에 일원화한다.

### 1.3 미해결 문제 — 267의 범위

266이 해결하지 않은 3가지 문제가 이 문서의 구현 대상이다:

| 문제 | 현재 상태 | 위험도 |
|------|-----------|--------|
| **외부 서드파티에 내부 토폴로지 노출** | `_execute_request()`가 모든 요청에 무조건 Baggage 동기화 | 높음 |
| **수신 측 헤더 위조** | `CellTaggingMiddleware`가 수신 헤더를 읽지 않음 (안전하나, 267에서 수신 로직 추가 시 위험) | 중간 |
| **서비스 간 Cell 토폴로지 불일치** | `CellRegistry.get_cell_info()`가 None 반환 시 방어 로직 없음 | 중간 |

---

## 2. 송신 측 — Trust Boundary 필터링

### 2.1 문제

`_execute_request()`는 현재 **모든 outgoing 요청**에 `sync_contextvars_to_baggage()`를 호출한다:

```python
# services/http_client.py L191-196 — 현재 코드
from selfhealing.observability.baggage import (
    detach_baggage_token,
    sync_contextvars_to_baggage,
)

baggage_token = sync_contextvars_to_baggage()  # ← 무조건 실행
```

결과적으로 `baggage: selfhealing.cell_id=cell-3, selfhealing.domain=payment`가
Toss PG사, AWS S3 등 **외부 서드파티**에도 전송되어 내부 토폴로지가 노출된다.

### 2.2 설계 — `propagate_context` 플래그 + DNS Suffix Auto-discovery

**선택: 인스턴스 레벨 `propagate_context` 플래그**

3가지 선택지 중 인스턴스 레벨 bool 플래그를 선택한 이유:

| 선택지 | 판정 | 이유 |
|--------|------|------|
| ① URL 화이트리스트 (Settings) | ❌ | 운영 부담 — 내부 서비스 URL 변경 시 Settings도 동기화 필요 |
| ② 요청별 파라미터 | ❌ | 호출 측이 매번 명시해야 하므로 휴먼 에러 위험 |
| **③ 인스턴스 플래그 + DNS Auto-discovery** | ✅ | `suppress_internal_spans` 기존 패턴과 일관. DNS 폴백으로 휴먼 에러 방지 |

기존 `__init__`의 `suppress_internal_spans: bool = False`와 동일한 인스턴스 레벨 플래그 패턴이다.

### 2.3 구현 — `SelfHealingHttpClient` 확장

```python
# services/http_client.py — __init__ 파라미터 추가

# 모듈 레벨 상수
_DEFAULT_INTERNAL_DNS_SUFFIXES = (
    ".svc.cluster.local",  # Kubernetes 서비스
    ".internal",           # 내부 DNS 규약
)

class SelfHealingHttpClient:
    def __init__(
        self,
        base_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        suppress_internal_spans: bool = False,
        propagate_context: bool = True,  # ← 추가
    ):
        # ... 기존 코드 ...
        self._propagate_context = propagate_context
```

```python
# services/http_client.py — _execute_request() 수정

def _execute_request(self, method: str, url: str, **kwargs: Any):
    import requests as req_lib

    headers = self._get_headers(kwargs.pop("headers", None))
    timeout = kwargs.pop("timeout", self.default_timeout)

    # Deadline 기반 timeout 자동 조정 (기존 코드 유지)
    # ...

    request_func = getattr(req_lib, method.lower())

    # Trust Boundary 필터링 — 내부 서비스에만 Baggage 전파
    if self._should_propagate_context(url):
        from selfhealing.observability.baggage import (
            detach_baggage_token,
            sync_contextvars_to_baggage,
        )
        baggage_token = sync_contextvars_to_baggage()
    else:
        baggage_token = None

    try:
        if self._suppress_internal_spans and _is_otel_enabled():
            with suppress_otel_instrumentation():
                return request_func(url, headers=headers, timeout=timeout, **kwargs)
        return request_func(url, headers=headers, timeout=timeout, **kwargs)
    finally:
        if baggage_token is not None:
            from selfhealing.observability.baggage import detach_baggage_token
            detach_baggage_token(baggage_token)
```

```python
# services/http_client.py — DNS Auto-discovery 메서드

def _should_propagate_context(self, url: str) -> bool:
    """
    Trust Boundary 판별 — 내부 서비스에만 컨텍스트 전파.

    계층적 결정:
    1. propagate_context=False (명시적) → 무조건 차단
    2. propagate_context=True → DNS suffix 매칭으로 자동 판별
    """
    if not self._propagate_context:
        return False

    try:
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname
        if not hostname:
            return False

        from selfhealing.settings.cell_topology import get_cell_topology_settings
        settings = get_cell_topology_settings()
        suffixes = getattr(settings, "internal_dns_suffixes", None)
        if suffixes is None:
            suffixes = _DEFAULT_INTERNAL_DNS_SUFFIXES

        return any(hostname.endswith(suffix) for suffix in suffixes)
    except Exception:
        # 파싱 실패 시 안전하게 차단 (Fail-Closed)
        return False
```

### 2.4 Settings 확장

```python
# settings/cell_topology.py — CellTopologySettings 필드 추가

internal_dns_suffixes: list[str] = Field(
    default=[".svc.cluster.local", ".internal"],
    description=(
        "내부 서비스로 간주할 DNS 접미사. "
        "이 접미사에 해당하는 호스트에만 OTel Baggage(cell_id 등)를 전파한다. "
        "예: Kubernetes 환경에서 .svc.cluster.local"
    ),
)
```

**환경 변수**: `SELFHEALING_CELL_TOPOLOGY_INTERNAL_DNS_SUFFIXES='[".svc.cluster.local", ".internal"]'`

### 2.5 사용 예시

```python
# 내부 MSA 호출 — Baggage 자동 전파
internal_client = SelfHealingHttpClient()
internal_client.post("http://order-svc.default.svc.cluster.local/api/v1/orders", ...)
# → baggage: selfhealing.cell_id=cell-3 ✅ 전파됨

# 외부 PG사 호출 — Baggage 차단
pg_client = SelfHealingHttpClient(propagate_context=False)
pg_client.post("https://api.tosspayments.com/v1/payments", ...)
# → baggage 헤더 없음 ✅ 차단됨

# propagate_context=True (기본값)이지만 DNS suffix 불일치 → 자동 차단
default_client = SelfHealingHttpClient()
default_client.post("https://s3.amazonaws.com/bucket/key", ...)
# → hostname "s3.amazonaws.com"이 .svc.cluster.local에 불일치 → 차단
```

---

## 3. 수신 측 — 헤더 위조 방지 + Topology Mismatch 방어

### 3.1 문제

267에서 수신 측 `cell_id` 전파 수용 로직을 추가할 때, 두 가지 위험이 존재한다:

1. **위조**: 악의적 클라이언트가 퍼블릭 인터넷에서 `baggage: selfhealing.cell_id=cell-0` 주입 → Tagger 해싱 우회
2. **불일치**: 상위 서비스가 8-Cell, 하위 서비스가 4-Cell → 존재하지 않는 `cell-5` 수신

### 3.2 설계 — 심층 방어 (Defense-in-Depth)

**Layer 1: API Gateway Strip (인프라 레벨)**

```nginx
# nginx.conf 또는 Kong/Envoy 설정
# 퍼블릭 요청의 baggage 헤더에서 selfhealing 네임스페이스 제거
proxy_set_header baggage "";
```

**Layer 2: CIDR Trust 검증 (앱 레벨)**

**선택: CIDR 기반 Trust 검증**

3가지 선택지 중 CIDR을 선택한 이유:

| 선택지 | 판정 | 이유 |
|--------|------|------|
| **① CIDR 기반** | ✅ | `tiering/models.py` L217-221에 `ip_address`/`ip_network` 패턴 이미 존재. `utils/network.py` `extract_client_ip` 통합 유틸 사용 가능. 표준 라이브러리만으로 구현. |
| ② HMAC 서명 | 보완용 | `audit/masking.py` L164-167에 HMAC 패턴 존재하나, 모든 내부 서비스에 shared secret 배포 필요 — K8s Secret 관리 운영 부담 |
| ③ mTLS/SPIFFE | 장기 | 서비스 메시(Istio) 인프라 의존. `multiregion/secure_client.py`에 mTLS 패턴 있으나 리전 간 전용 |

CIDR은 **추가 인프라 없이** Python 표준 라이브러리 `ipaddress`만으로 구현 가능하고,
Kubernetes Pod CIDR은 이미 결정론적이므로 가장 실용적이다.

**Layer 3: Topology Mismatch 검증 (앱 레벨)**

수신된 `cell_id`가 로컬 `CellRegistry`에 존재하는지 + ACTIVE 상태인지 검증한다.

### 3.3 구현 — `CellTaggingMiddleware` 확장

```python
# api/django/cell/middleware.py — CellTaggingMiddleware.__call__ 교체

from ipaddress import ip_address, ip_network  # 표준 라이브러리

class CellTaggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._tagger = None
        self._trusted_cidrs = None  # 지연 로딩

    def __call__(self, request):
        if not self._check_enabled():
            return self.get_response(request)

        from selfhealing.context.cell_context import _current_cell_id

        tagger = self._get_tagger()

        # ── 수신 측 cell_id 전파 수용 (Trust 검증 + Topology 검증) ──
        cell_id = self._accept_incoming_cell_id(request)

        if cell_id is None:
            # 수신 전파 없음 또는 검증 실패 → 로컬 해싱
            cell_id = tagger.resolve_cell_id_from_request(request)

        request.cell_id = cell_id
        token = _current_cell_id.set(cell_id)

        try:
            response = self.get_response(request)
        finally:
            _current_cell_id.reset(token)

        response["X-Cell-Id"] = cell_id
        return response

    def _accept_incoming_cell_id(self, request) -> str | None:
        """
        수신된 cell_id를 Trust 검증 + Topology 검증 후 수용 또는 거부.

        Returns:
            유효한 cell_id 또는 None (로컬 해싱으로 폴백)
        """
        # BaggageSyncMiddleware가 이미 복원한 ContextVar에서 읽기
        from selfhealing.context.cell_context import get_current_cell_id
        incoming_cell_id = get_current_cell_id()

        if not incoming_cell_id:
            return None

        # Layer 2: CIDR Trust 검증
        if not self._is_trusted_source(request):
            logger.debug(
                "Untrusted source attempted cell_id propagation: %s",
                incoming_cell_id,
            )
            return None

        # Layer 3: Topology Mismatch 검증
        return self._validate_cell_id(incoming_cell_id)

    def _is_trusted_source(self, request) -> bool:
        """
        요청 소스가 신뢰할 수 있는 내부 네트워크인지 CIDR로 검증.

        패턴 참조: tiering/models.py L217-221 matches_ip()
        IP 추출: utils/network.py extract_client_ip()
        """
        from selfhealing.utils.network import extract_client_ip

        client_ip = extract_client_ip(request)
        if not client_ip:
            return False

        try:
            addr = ip_address(client_ip)
            return any(
                addr in ip_network(cidr, strict=False)
                for cidr in self._get_trusted_cidrs()
            )
        except ValueError:
            return False

    def _get_trusted_cidrs(self) -> list[str]:
        """trusted_source_cidrs 지연 로딩."""
        if self._trusted_cidrs is None:
            from selfhealing.settings.cell_topology import get_cell_topology_settings
            settings = get_cell_topology_settings()
            self._trusted_cidrs = settings.trusted_source_cidrs
        return self._trusted_cidrs

    def _validate_cell_id(self, incoming_cell_id: str) -> str | None:
        """
        수신된 cell_id가 로컬 CellRegistry에서 유효한지 검증.

        검증 실패 시 topology_mismatch 메트릭 증가 + 로깅.

        패턴 참조: registry.py L153 get_cell_info() — None 반환 시 존재하지 않는 Cell
        패턴 참조: registry.py L141-145 — DRAINING/ISOLATED Cell 건너뛰기 로직
        """
        from selfhealing.services.cell_topology import get_cell_registry
        from selfhealing.services.cell_topology.models import CellState

        registry = get_cell_registry()
        cell_info = registry.get_cell_info(incoming_cell_id)

        if cell_info is None:
            # 로컬 Registry에 존재하지 않는 Cell
            self._record_topology_mismatch(incoming_cell_id, "cell_not_found")
            logger.warning(
                "Topology mismatch: received '%s' not in local registry "
                "(local cell_count=%d)",
                incoming_cell_id,
                len(registry.get_all_cells()),
            )
            return None

        if cell_info.state not in (CellState.ACTIVE, CellState.WARMUP):
            # DRAINING/ISOLATED Cell — 수용하면 격리 정책 우회됨
            self._record_topology_mismatch(incoming_cell_id, "cell_not_active")
            logger.warning(
                "Topology mismatch: received '%s' is %s (not ACTIVE/WARMUP)",
                incoming_cell_id,
                cell_info.state.value,
            )
            return None

        return incoming_cell_id

    @staticmethod
    def _record_topology_mismatch(incoming_cell_id: str, reason: str) -> None:
        """
        Topology Mismatch Prometheus 카운터 기록.

        메트릭 패턴 참조: core/hedging/metrics.py L103-106
            HEDGING_MISMATCH_TOTAL = Counter(
                "selfhealing_hedging_mismatch_total",
                "...",
                ["mismatch_type"],
            )
        """
        try:
            from prometheus_client import Counter

            counter = Counter(
                "selfhealing_cell_topology_mismatch_total",
                "Cell topology mismatch between upstream and local registry",
                ["incoming_cell_id", "reason"],
            )
            counter.labels(
                incoming_cell_id=incoming_cell_id,
                reason=reason,
            ).inc()
        except Exception:
            pass  # 메트릭 실패가 요청을 중단하지 않음
```

### 3.4 Settings 확장

```python
# settings/cell_topology.py — CellTopologySettings 필드 추가

trusted_source_cidrs: list[str] = Field(
    default=[
        "10.0.0.0/8",       # RFC 1918 Class A — K8s Pod/Service CIDR 기본값
        "172.16.0.0/12",    # RFC 1918 Class B
        "192.168.0.0/16",   # RFC 1918 Class C
        "127.0.0.0/8",      # Loopback (개발 환경)
    ],
    description=(
        "cell_id 전파를 신뢰할 소스 CIDR 목록. "
        "이 대역에서 온 요청만 상위 서비스의 cell_id를 수용한다. "
        "퍼블릭 인터넷에서 온 요청은 로컬 해싱으로 폴백."
    ),
)
```

**환경 변수**: `SELFHEALING_CELL_TOPOLOGY_TRUSTED_SOURCE_CIDRS='["10.0.0.0/8", "172.16.0.0/12"]'`

RFC 1918 사설 대역을 기본값으로 선택한 이유:
- Kubernetes Pod CIDR, Service CIDR 모두 RFC 1918 대역 내 할당
- AWS VPC, GCP VPC의 내부 서브넷도 RFC 1918 대역 사용
- 프로덕션 배포 시 환경 변수로 정확한 Pod CIDR로 축소 가능

### 3.5 Topology Mismatch 메트릭 정의

```
# Prometheus 메트릭
selfhealing_cell_topology_mismatch_total{incoming_cell_id="cell-5", reason="cell_not_found"}
selfhealing_cell_topology_mismatch_total{incoming_cell_id="cell-3", reason="cell_not_active"}
```

| 레이블 | 용도 |
|--------|------|
| `incoming_cell_id` | 어떤 Cell ID가 전파되었는지 |
| `reason` | `cell_not_found` (Registry에 없음) / `cell_not_active` (DRAINING/ISOLATED) |

**Grafana 알림 쿼리**:

```promql
rate(selfhealing_cell_topology_mismatch_total[5m]) > 0
```

이 알림이 발생하면 상위·하위 서비스 간 `cell_count` 설정 불일치를 의미한다.

---

## 4. Blast Radius 추적 흐름 (전체)

```
[퍼블릭 인터넷]
  → [API Gateway: baggage 헤더 Strip]
  → [Service A: CellTaggingMiddleware]
      _accept_incoming_cell_id() → None (Baggage 비어있음)
      → cell_id = tagger.resolve_cell_id_from_request(request)
      → cell_id = "cell-3" (user_id 기반 해싱)
      → _current_cell_id.set("cell-3")
      → BaggageSyncMiddleware: sync_contextvars_to_baggage()
      │
      ├→ SelfHealingHttpClient.post("http://payment-svc.default.svc.cluster.local/...")
      │    _should_propagate_context() → True (.svc.cluster.local 매칭)
      │    → baggage: selfhealing.cell_id=cell-3 ✅ 전파
      │    → [Service B: CellTaggingMiddleware]
      │        _accept_incoming_cell_id()
      │          → _is_trusted_source() → True (10.x.x.x)
      │          → _validate_cell_id("cell-3") → cell_info 존재, ACTIVE → "cell-3"
      │        → cell_id = "cell-3" (재해시 없음, 상위 서비스 결정 존중)
      │
      ├→ SelfHealingHttpClient(propagate_context=False).post("https://api.tosspayments.com/...")
      │    _should_propagate_context() → False (명시적 차단)
      │    → baggage 없음 ✅ 내부 토폴로지 미노출
      │
      └→ force_open_circuit_breaker.delay(service_name="toss_api")
           → Celery headers["cell_id"] = "cell-3" (263 하이브리드)
           → [Worker: cell-3 범위에서 CB 처리]
```

---

## 5. 변경 범위

| 파일 | 변경 | 유형 | 코드량 |
|------|------|------|--------|
| `services/http_client.py` | `propagate_context` 파라미터 추가 + `_should_propagate_context()` | 필수 | ~25줄 |
| `settings/cell_topology.py` | `internal_dns_suffixes`, `trusted_source_cidrs` 필드 추가 | 필수 | ~20줄 |
| `api/django/cell/middleware.py` | `_accept_incoming_cell_id()`, `_is_trusted_source()`, `_validate_cell_id()` | 필수 | ~60줄 |
| ~~`services/http_client.py` `_get_headers()`~~ | ~~X-Cell-Id 수동 주입~~ | ~~삭제~~ | 0줄 (불필요) |

**Settings 환경 변수 추가:**

```dotenv
# .env
SELFHEALING_CELL_TOPOLOGY_INTERNAL_DNS_SUFFIXES='[".svc.cluster.local", ".internal"]'
SELFHEALING_CELL_TOPOLOGY_TRUSTED_SOURCE_CIDRS='["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"]'
```

---

## 6. 미들웨어 배치 순서

```python
MIDDLEWARE = [
    # ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",     # ① cell_id 결정 (Trust + Topology 검증 포함)
    "selfhealing.api.django.cell.middleware.BaggageSyncMiddleware",     # ② ContextVar → Baggage 동기화
    # ...
]
```

**실행 순서**:
1. `BaggageSyncMiddleware`: 수신 Baggage → ContextVar 복원 (`restore_contextvars_from_baggage()`)
2. `CellTaggingMiddleware`: 복원된 ContextVar에서 `incoming_cell_id` 읽기 → Trust/Topology 검증
3. `BaggageSyncMiddleware`: 확정된 ContextVar → Baggage 재동기화 (`sync_contextvars_to_baggage()`)

`BaggageSyncMiddleware`가 `CellTaggingMiddleware` **직후**에 배치되어야
검증 후 확정된 `cell_id`가 Baggage에 반영된다. 이는 기존 배치 순서와 동일하다.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `263_CELL_TAGGER.md` | `_current_cell_id` ContextVar 제공자 (선행 의존) |
| `266_CELL_OTEL_PROPAGATION.md` | OTel Baggage 전파 인프라 (선행 의존 — 이미 완성) |
| `265_CELL_EVACUATION_POLICY.md` | Cell 장애 시 트래픽 대피 정책 |
| `261_CELL_TOPOLOGY_OVERVIEW.md` | Cell 토폴로지 전체 개요 (부모 문서) |

---

## 8. 선택 근거 요약

| 결정 | 선택 | 근거 (코드 참조) |
|------|------|-------------------|
| 전파 수단 | OTel Baggage Only (X-Cell-Id 수동 주입 생략) | `baggage.py` L29-33 매핑 완성, `http_client.py` L193 동기화 동작 |
| Trust Boundary | 인스턴스 플래그 + DNS Auto-discovery | `http_client.py` L109 `suppress_internal_spans` 기존 패턴 일관성 |
| 위조 방지 | CIDR 기반 검증 | `tiering/models.py` L217-221 `ip_network` 패턴 재사용, `utils/network.py` 통합 IP 추출 |
| Topology Mismatch | validate-and-fallback + 메트릭 | `registry.py` L153 `get_cell_info()` 활용, `hedging/metrics.py` L103 Counter 패턴 |
| HMAC 서명 | 267 범위 아님 (선택적 보완) | `audit/masking.py` L164-167 패턴 존재하나, K8s Secret 배포 운영 부담 대비 CIDR로 충분 |

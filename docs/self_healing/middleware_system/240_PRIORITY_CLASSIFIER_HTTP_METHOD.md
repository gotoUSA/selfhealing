# 240. Priority Classifier Enhancement — HTTP Method 기반 Tier 분류

| 항목 | 내용 |
|------|------|
| **문서번호** | 240 |
| **작성일** | 2026-02-18 |
| **상태** | 설계 완료 |
| **선행 문서** | 236, 239 |
| **후속 문서** | 241, 242, 243 |

---

## 1. 배경 및 문제 정의

### 1.1 현재 상태

**TierRegistry** (`api/django/tiering/registry.py`)의 tier 분류는 **URL 경로만** 사용합니다.

`TierMapping.matches()` (`tiering/models.py` L119-136):
```python
def matches(self, path: str) -> bool:
    if self.pattern_type == TierMatchType.EXACT:
        return path == self.pattern
    elif self.pattern_type == TierMatchType.WILDCARD:
        return fnmatch.fnmatch(path, self.pattern)
    elif self.pattern_type == TierMatchType.REGEX:
        if self._compiled_pattern is None:
            self._compiled_pattern = re.compile(self.pattern)
        return bool(self._compiled_pattern.match(path))
    return False
```

`get_tier_for_path()` (`registry.py` L233-253):
```python
def get_tier_for_path(self, path: str) -> TierDefinition | None:
    with self._data_lock:
        if path in self._path_tier_cache:
            return self._path_tier_cache[path]
        result = None
        for mapping in self._mappings:
            if mapping.matches(path):
                result = self._tiers.get(mapping.tier_id)
                break
        # ...
```

`resolve_tier_with_fallback()` (`registry.py` L399-461)도 동일하게 `path`만 전달:
```python
def resolve_tier_with_fallback(
    self,
    path: str,
    client_ip: str | None = None,
    user_id: str | None = None,
    api_key: str | None = None,
) -> TierResult:
```

`AdmissionControlMiddleware._process_request()` (`admission_control.py` L120-163)에서도 `request.method` 미사용:
```python
def _process_request(self, request):
    path = request.path
    client_ip = self._get_client_ip(request)
    user_id = self._get_user_id(request)
    tier_result = self._registry.resolve_tier_with_fallback(
        path=path, client_ip=client_ip, user_id=user_id
    )
```

### 1.2 문제点

동일 URL이라도 HTTP 메서드에 따라 중요도가 다릅니다:

| 엔드포인트 | GET | POST/PUT/DELETE |
|-----------|-----|-----------------|
| `/api/self-healing/config/*` | 설정 **조회** (비필수) | 설정 **변경** (중요) |
| `/api/self-healing/status/*` | 상태 **모니터링** (비필수) | 상태 **갱신** (표준~중요) |
| `/api/self-healing/dlq/*` | DLQ **조회** (표준) | DLQ **재처리** (중요) |

현재는 모두 동일 tier로 분류되어, 백프레셔 시 **읽기 쓰기 구분 없이 동일하게 제한**됩니다.

### 1.3 RESTful 의미론 기반 우선순위

HTTP 메서드의 시스템 영향도:

| 메서드 | 의미 | 기본 Priority Boost |
|--------|------|---------------------|
| `DELETE` | 리소스 제거 → 치유 동작 | +2 (tier 승격 가능) |
| `POST` | 리소스 생성/실행 → 상태 변경 | +1 |
| `PUT/PATCH` | 리소스 수정 → 설정 변경 | +1 |
| `GET` | 조회 전용 → 캐시 가능 | 0 (기본값) |
| `HEAD/OPTIONS` | 메타데이터 → Load Shedding 1순위 | -1 |

---

## 2. 설계

### 2.1 변경 범위

| 파일 | 변경 유형 | 규모 |
|------|-----------|------|
| `tiering/models.py` | `TierMapping` 필드 추가 + `matches()` 수정 | ~15줄 |
| `tiering/defaults.py` | 메서드 인식 매핑 추가 | ~30줄 |
| `tiering/registry.py` | `get_tier_for_path()` → `get_tier_for_request()` 확장 | ~20줄 |
| `api/django/admission_control.py` | `request.method` 전달 | ~3줄 |

### 2.2 설계 원칙

1. **하위 호환**: `method=None`이면 기존처럼 path만으로 매칭 (변경 없음)
2. **Method-specific이 우선**: method + path 매핑이 path-only 매핑보다 우선
3. **TierMatchType 확장 불필요**: 기존 EXACT/WILDCARD/REGEX 그대로 사용

---

## 3. 구현 상세

### 3.1 TierMapping 확장

**파일**: `api/django/tiering/models.py`

```python
@dataclass
class TierMapping:
    """
    API path to tier mapping.

    Attributes:
        pattern: Path pattern (exact, wildcard, or regex)
        tier_id: Target tier ID
        pattern_type: Type of pattern matching
        priority: Mapping priority (higher = matched first)
        description: Description of the mapping
        methods: HTTP methods this mapping applies to (None = all methods)
    """

    pattern: str
    tier_id: str
    pattern_type: TierMatchType = TierMatchType.EXACT
    priority: int = 0
    description: str = ""
    methods: frozenset[str] | None = None  # 신규 필드

    _compiled_pattern: re.Pattern | None = field(
        default=None, repr=False, compare=False
    )

    def matches(self, path: str, method: str | None = None) -> bool:
        """
        Check if the path (and optionally method) matches this mapping.

        Args:
            path: API path to check
            method: HTTP method (GET, POST, etc.) — None이면 method 무시

        Returns:
            True if path matches and method is compatible
        """
        # Method 필터: mapping에 methods가 지정되어 있고,
        # 요청 method가 해당 set에 없으면 불일치
        if self.methods is not None and method is not None:
            if method.upper() not in self.methods:
                return False

        # 기존 path 매칭 로직 (변경 없음)
        if self.pattern_type == TierMatchType.EXACT:
            return path == self.pattern
        elif self.pattern_type == TierMatchType.WILDCARD:
            return fnmatch.fnmatch(path, self.pattern)
        elif self.pattern_type == TierMatchType.REGEX:
            if self._compiled_pattern is None:
                self._compiled_pattern = re.compile(self.pattern)
            return bool(self._compiled_pattern.match(path))
        return False
```

**하위 호환성 보장**:
- `methods=None` (기본값): 모든 HTTP 메서드에 매칭 → 기존 매핑 동작 변경 없음
- `method=None` 인자: method 필터 스킵 → 기존 호출 코드 변경 없음

### 3.2 캐시 키 변경

**파일**: `api/django/tiering/registry.py`

`get_tier_for_path()`는 현재 `self._path_tier_cache[path]`로 path만 캐시합니다. method를 포함하도록 확장:

```python
def get_tier_for_request(
    self,
    path: str,
    method: str | None = None,
) -> TierDefinition | None:
    """
    Get the tier for an API request (path + method).

    Args:
        path: API path
        method: HTTP method (None이면 기존 path-only 동작)

    Returns:
        TierDefinition or None
    """
    cache_key = (path, method.upper() if method else None)

    with self._data_lock:
        if cache_key in self._path_tier_cache:
            return self._path_tier_cache[cache_key]

        result = None
        for mapping in self._mappings:
            if mapping.matches(path, method):
                result = self._tiers.get(mapping.tier_id)
                break

        if len(self._path_tier_cache) < self._PATH_CACHE_MAX_SIZE:
            self._path_tier_cache[cache_key] = result
        return result
```

**기존 `get_tier_for_path()` 유지** (하위 호환):
```python
def get_tier_for_path(self, path: str) -> TierDefinition | None:
    """Legacy: path-only tier resolution."""
    return self.get_tier_for_request(path, method=None)
```

### 3.3 resolve 체인 확장

`resolve_tier()`, `resolve_tier_with_fallback()` 모두 `method` 파라미터 추가:

```python
def resolve_tier(
    self,
    path: str,
    client_ip: str | None = None,
    user_id: str | None = None,
    api_key: str | None = None,
    method: str | None = None,      # 신규
) -> TierDefinition | None:
    override_tier = self.get_override_tier(
        client_ip=client_ip,
        user_id=user_id,
        api_key=api_key,
    )
    if override_tier:
        return override_tier

    return self.get_tier_for_request(path, method=method)  # 변경


def resolve_tier_with_fallback(
    self,
    path: str,
    client_ip: str | None = None,
    user_id: str | None = None,
    api_key: str | None = None,
    method: str | None = None,      # 신규
) -> TierResult:
    # ... 기존 로직에서 resolve_tier() 호출부에 method= 전달
    tier = self.resolve_tier(
        path=path,
        client_ip=client_ip,
        user_id=user_id,
        api_key=api_key,
        method=method,              # 신규
    )
```

### 3.4 매핑 정렬 우선순위

method-specific 매핑이 method-agnostic 매핑보다 우선되도록 정렬 규칙을 보강합니다:

```python
def set_mappings(self, mappings: list[TierMapping]) -> TierValidationResult:
    # ...
    with self._data_lock:
        self._mappings = sorted(
            mappings,
            key=lambda m: (
                m.priority,                           # 1차: 기존 priority
                1 if m.methods is not None else 0,    # 2차: method-specific 우선
            ),
            reverse=True,
        )
```

이렇게 하면 동일 priority에서 `methods=frozenset({"POST"})`인 매핑이 `methods=None`보다 먼저 평가됩니다.

### 3.5 DEFAULT_TIER_MAPPINGS 확장

**파일**: `api/django/tiering/defaults.py`

기존 매핑은 변경하지 않고(하위 호환), method-specific 매핑을 **앞에 추가**합니다:

```python
DEFAULT_TIER_MAPPINGS: list[TierMapping] = [
    # =========================================================================
    # Method-Specific Mappings (신규 — 기존 path-only보다 우선)
    # =========================================================================

    # POST/DELETE /config/* → critical (설정 변경은 치유 동작)
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=70,
        description="설정 변경 API (쓰기)",
        methods=frozenset({"POST", "PUT", "PATCH", "DELETE"}),
    ),
    # POST /dlq/* → critical (DLQ 재처리는 치유 동작)
    TierMapping(
        pattern="/api/self-healing/dlq/*",
        tier_id="critical",
        pattern_type=TierMatchType.WILDCARD,
        priority=70,
        description="DLQ 재처리 (쓰기)",
        methods=frozenset({"POST", "PUT", "DELETE"}),
    ),
    # GET /config/* → non_essential (설정 조회는 비필수)
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="non_essential",
        pattern_type=TierMatchType.WILDCARD,
        priority=55,
        description="설정 조회 API (읽기)",
        methods=frozenset({"GET", "HEAD"}),
    ),

    # =========================================================================
    # Path-Only Mappings (기존 — 변경 없음)
    # =========================================================================
    # ... (기존 DEFAULT_TIER_MAPPINGS 그대로 유지)
]
```

### 3.6 AdmissionControlMiddleware 수정

**파일**: `api/django/admission_control.py`

`_process_request()`에서 `request.method` 전달 (최소 변경):

```python
def _process_request(self, request):
    path = request.path
    client_ip = self._get_client_ip(request)
    user_id = self._get_user_id(request)
    method = request.method                               # 신규: 1줄

    tier_result = self._registry.resolve_tier_with_fallback(
        path=path,
        client_ip=client_ip,
        user_id=user_id,
        method=method,                                    # 신규: 1줄
    )
    # ... 이하 기존 로직 동일
```

---

## 4. 직렬화/역직렬화

### 4.1 TierMapping.to_dict() / from_dict()

```python
def to_dict(self) -> dict[str, Any]:
    result = {
        "pattern": self.pattern,
        "tier_id": self.tier_id,
        "pattern_type": self.pattern_type.value,
        "priority": self.priority,
        "description": self.description,
    }
    if self.methods is not None:
        result["methods"] = sorted(self.methods)  # JSON 호환 list
    return result

@classmethod
def from_dict(cls, data: dict[str, Any]) -> TierMapping:
    methods = None
    if "methods" in data and data["methods"] is not None:
        methods = frozenset(data["methods"])

    return cls(
        pattern=data["pattern"],
        tier_id=data["tier_id"],
        pattern_type=TierMatchType(data.get("pattern_type", "exact")),
        priority=data.get("priority", 0),
        description=data.get("description", ""),
        methods=methods,
    )
```

### 4.2 하위 호환 보장

- `"methods"` 키가 없는 기존 JSON → `methods=None` → 모든 메서드에 매칭
- `"methods": null` → 동일하게 `methods=None`
- 기존 rollback 스냅샷도 정상 복원 (`from_dict`에서 키 부재 허용)

---

## 5. 테스트 전략

### 5.1 단위 테스트

```
tests/unit/api/tiering/test_tier_mapping_method.py
├── TestTierMappingMethods
│   ├── test_matches_method_specific_hit         # POST + path 일치 → True
│   ├── test_matches_method_specific_miss        # GET + POST-only 매핑 → False
│   ├── test_matches_method_none_accepts_all     # methods=None → 모든 method 통과
│   ├── test_matches_method_arg_none_skips_filter # method 미전달 → method 필터 스킵
│   └── test_to_from_dict_with_methods           # 직렬화/역직렬화 왕복
├── TestTierRegistryMethodResolution
│   ├── test_method_specific_higher_priority     # POST /config → critical
│   ├── test_method_fallback_to_path_only        # DELETE /status (매핑 없음) → path-only 매핑
│   ├── test_cache_key_includes_method           # GET과 POST 다른 결과 캐시
│   └── test_legacy_get_tier_for_path            # 기존 API 호환성
├── TestAdmissionControlWithMethod
│   ├── test_post_config_critical_tier           # POST /config → critical tier
│   ├── test_get_config_non_essential_tier       # GET /config → non_essential tier
│   └── test_method_propagation                  # request.method가 resolve 체인에 전달
```

### 5.2 회귀 테스트

기존 tiering 테스트가 모두 통과하는지 검증:
- `resolve_tier_with_fallback()` 기존 테스트: `method=None`이 기본값이므로 변경 없이 통과
- `TierMapping.matches()` 기존 테스트: `method` 인자 없음 → 기존 동작

---

## 6. 마이그레이션 전략

### 6.1 Phase 1: 인프라 (이번 작업)
- `TierMapping.methods` 필드 추가
- `TierMapping.matches(path, method)` 확장
- `get_tier_for_request()` 신규 메서드
- `resolve_tier_with_fallback()` `method` 파라미터 추가

### 6.2 Phase 2: 기본 매핑 추가
- `DEFAULT_TIER_MAPPINGS`에 method-specific 매핑 추가
- 기존 path-only 매핑은 유지 (fallback 역할)

### 6.3 Phase 3: 외부 설정 지원
- Admin API를 통한 method-specific 매핑 CRUD
- DB 백엔드의 TierMapping 스키마에 `methods` 컬럼 추가

---

## 7. 성능 영향 분석

### 7.1 캐시 키 변경

- **기존**: `dict[str, TierDefinition | None]` — path 문자열 키
- **변경**: `dict[tuple[str, str | None], TierDefinition | None]` — (path, method) 튜플 키

캐시 엔트리 최대 증가: 기존 대비 최대 N×M (N=경로수, M=메서드수). 실제 self-healing API의 고유 path 수는 ~30개, method 수는 5개이므로 최대 150 엔트리. `_PATH_CACHE_MAX_SIZE`(1000) 이내입니다.

### 7.2 매핑 순회

method-specific 매핑 추가로 `self._mappings` 길이 증가. 현재 기본 매핑 11개 → 최대 ~25개로 증가 예상. `sorted + linear scan` O(n)이지만 n이 매우 작아 영향 무시.

### 7.3 정렬 비용

`set_mappings()` 호출 시 정렬 키에 method 존재 여부 추가: O(n log n) 동일, 비교 함수에 튜플 비교 1회 추가. 무시 수준.

---

## 8. Fail-Open 안전성

| 시나리오 | 동작 |
|---------|------|
| `method=None` 전달 | method 필터 스킵 → 기존 path-only 동작 |
| method-specific 매핑 없음 | path-only 매핑으로 fallback |
| 잘못된 method 문자열 | `upper()` 처리 후 매칭 실패 → path-only fallback |
| 기존 JSON에 `methods` 키 없음 | `from_dict()`에서 `None` → 모든 method 허용 |

모든 경로에서 기존 동작보다 **더 restrictive하게** 동작하는 것이 아니라, 기존 동작을 유지하면서 **추가 분류 정밀도**만 제공합니다.

---

## 9. OPTIONS (CORS Preflight) Bypass 전략

### 9.1 문제

1.3절 표에서 `HEAD/OPTIONS`를 "-1 (Load Shedding 1순위)"로 분류하였으나, 브라우저 기반 클라이언트는 `POST`, `PUT`, `DELETE` 요청 전에 반드시 **OPTIONS (Preflight)** 요청을 전송합니다.

OPTIONS가 낮은 Tier(`non_essential`)로 분류되어 부하 시 거부되면, 후속 **Critical 쓰기 요청**(POST/DELETE)도 CORS 에러로 전송 자체가 불가능합니다.

### 9.2 결정 — Bypass (Always Allow)

OPTIONS를 Critical tier로 승격하는 대신, **Tier 분류 자체를 Bypass**합니다.

근거:
- OPTIONS 요청은 body가 없고 부하가 거의 없음
- Critical tier로 할당하면 캐시·통계가 왜곡됨
- `AdmissionControlMiddleware`에 이미 동일 패턴의 early-return 선례가 존재 — Deadline Fast-Fail (`admission_control.py` L130-155)

### 9.3 적용 대상 — 2곳

`resolve_tier_with_fallback()`을 호출하는 미들웨어가 **2곳** 존재합니다:

| 미들웨어 | 파일 | 호출 위치 |
|---------|------|-----------|
| `AdmissionControlMiddleware._process_request()` | `admission_control.py` L160 | TrafficGate 파이프라인 |
| `TieringMiddleware.__call__()` | `tiering/middleware.py` L117 | Emergency Mode Load Shedding |

**양쪽 모두** OPTIONS bypass를 추가해야 합니다.

### 9.4 구현 코드

**`admission_control.py` — `_process_request()` 최상단:**
```python
def _process_request(self, request):
    # CORS Preflight는 Tier 분류 대상에서 제외 (Always Allow)
    # - OPTIONS는 body 없이 부하 무시 수준
    # - 거부 시 후속 POST/DELETE도 CORS 에러로 전송 불가
    if request.method == "OPTIONS":
        return self.get_response(request)

    # 0단계: Deadline Context 설정 및 Fast-Fail 체크
    # ... (기존 로직)
```

**`tiering/middleware.py` — `__call__()` early-return 직후:**
```python
def __call__(self, request):
    if not self._enabled:
        return self.get_response(request)

    # CORS Preflight Bypass — OPTIONS는 Load Shedding 대상 제외
    if request.method == "OPTIONS":
        return self.get_response(request)

    try:
        # ... (기존 Emergency Mode 로직)
```

### 9.5 1.3절 표 정정

| 메서드 | 의미 | 기본 Priority Boost | 비고 |
|--------|------|---------------------|------|
| `DELETE` | 리소스 제거 → 치유 동작 | +2 (tier 승격 가능) | |
| `POST` | 리소스 생성/실행 → 상태 변경 | +1 | |
| `PUT/PATCH` | 리소스 수정 → 설정 변경 | +1 | |
| `GET` | 조회 전용 → 캐시 가능 | 0 (기본값) | |
| `HEAD` | 메타데이터 | -1 | Tier 분류 대상 |
| `OPTIONS` | CORS Preflight | **Bypass** | Tier 분류 제외 (Always Allow) |

---

## 10. LRU 캐시 Eviction 전략

### 10.1 문제 진단

**현재 캐시 동작** (`registry.py` L255-256):
```python
if len(self._path_tier_cache) < self._PATH_CACHE_MAX_SIZE:
    self._path_tier_cache[path] = result
```

- `_PATH_CACHE_MAX_SIZE` = 1024 (계약값, `test_tier_registry_cache.py` L112에서 검증)
- 크기 초과 시 **신규 엔트리를 캐시하지 않음** (No Eviction)
- `request.path`는 Django의 **raw path** (`admission_control.py` L160: `path = request.path`)

**Raw path 문제** — `urls.py`에 path parameter 포함 URL이 다수:
```python
path("dlq/<int:pk>/", ...)                           # → /api/self-healing/dlq/123/
path("dlq/<int:pk>/retry/", ...)                     # → /api/self-healing/dlq/456/retry/
path("status/<str:service_name>/", ...)               # → /api/self-healing/status/payment-service/
path("config/<str:config_type>/history/<int:version>/", ...)
```

→ 동적 path가 다수이므로 캐시가 조기 포화, 이후 요청은 매번 linear scan 수행.

`(path, method)` 튜플로 키가 바뀌면 캐시 엔트리가 최대 5배(method 수) 증가하여 문제 심화.

### 10.2 결정 — `OrderedDict` 기반 LRU

`collections.OrderedDict` + `move_to_end()` + `popitem(last=False)` 패턴을 사용합니다.

**선택 이유**: 프로젝트 내 동일 패턴의 검증된 선례 존재.

`SafeGauge` (`metrics/safe_gauge/core.py` L318-370):
```python
self._children: OrderedDict[tuple, SafeGaugeChild] = OrderedDict()

# LRU Hit → move_to_end
self._children.move_to_end(key)

# Eviction → popitem(last=False)
oldest_key, oldest_child = self._children.popitem(last=False)
```

**`functools.lru_cache` 미채택 이유**: 메서드 데코레이터로 사용 시 `self._data_lock`(RLock)과의 경합 및 락 중복 문제 발생 가능. `TierRegistry`는 이미 `self._data_lock`으로 스레드 안전성을 보장하므로, 내부에 명시적 LRU를 구현하는 것이 일관적.

### 10.3 구현 코드

**`registry.py` — `_init()` 변경:**
```python
from collections import OrderedDict

def _init(self):
    # ...
    # Path → TierDefinition 조회 결과 캐시 (LRU Eviction)
    self._path_tier_cache: OrderedDict[
        tuple[str, str | None], TierDefinition | None
    ] = OrderedDict()
    self._PATH_CACHE_MAX_SIZE = 1024
```

**`registry.py` — `get_tier_for_request()` 캐시 로직:**
```python
def get_tier_for_request(
    self,
    path: str,
    method: str | None = None,
) -> TierDefinition | None:
    cache_key = (path, method.upper() if method else None)

    with self._data_lock:
        # 1. Cache Hit → LRU 갱신 (가장 최근 접근으로 이동)
        if cache_key in self._path_tier_cache:
            self._path_tier_cache.move_to_end(cache_key)
            return self._path_tier_cache[cache_key]

        # 2. Cache Miss → 매핑 순회
        result = None
        for mapping in self._mappings:
            if mapping.matches(path, method):
                result = self._tiers.get(mapping.tier_id)
                break

        # 3. Cache Update + LRU Eviction
        self._path_tier_cache[cache_key] = result
        if len(self._path_tier_cache) > self._PATH_CACHE_MAX_SIZE:
            self._path_tier_cache.popitem(last=False)  # 가장 오래전 접근된 항목 제거

        return result
```

### 10.4 기존 테스트 영향

`test_tier_registry_cache.py`에서 캐시를 **문자열 키로 직접 접근**하는 테스트가 존재:
```python
assert "/api/self-healing/control/" in registry._path_tier_cache      # L41
assert registry._path_tier_cache["/unknown/path/"] is None            # L56
```

캐시 키가 `(path, method)` 튜플로 바뀌면 **회귀 실패**합니다.

수정 방향:
```python
# 기존 (회귀 실패)
assert "/api/self-healing/control/" in registry._path_tier_cache

# 수정 (튜플 키 호환)
assert ("/api/self-healing/control/", None) in registry._path_tier_cache
```

### 10.5 네이밍 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| `_path_tier_cache` | **이름 유지** | 테스트 8곳 이상에서 직접 참조 (`test_tier_registry_cache.py`), type hint만 변경 |
| `_PATH_CACHE_MAX_SIZE` | **이름 유지** | `test_tier_registry_cache.py` L112에서 계약값 테스트 존재 |

---

## 11. __post_init__ Methods 정규화

### 11.1 문제

`TierMapping.matches()` 내부에서 `method.upper()`로 비교하지만, `TierMapping.methods` 필드 자체가 소문자로 생성될 수 있습니다:

```python
# 소문자로 생성된 경우
TierMapping(methods=frozenset({"post"}))

# matches() 내부
if method.upper() not in self.methods:  # "POST" not in {"post"} → True (불일치!)
    return False
```

`from_dict()`에서만 `frozenset()` 변환을 하면, 직접 생성(`TierMapping(methods=["post"])`)이나 테스트 코드에서 방어가 안 됩니다.

### 11.2 결정 — `__post_init__`에서 정규화

현재 `TierMapping`은 `frozen=False` (`models.py` L85의 `@dataclass`에 `frozen` 인자 없음). 기존 코드에서도 `self._compiled_pattern = re.compile(...)` 로 일반 대입을 사용 중(`models.py` L111). 따라서 `object.__setattr__`은 불필요하고 **일반 대입으로 충분**합니다.

```python
def __post_init__(self):
    """Compile regex pattern and normalize methods."""
    if self.pattern_type == TierMatchType.REGEX:
        try:
            self._compiled_pattern = re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{self.pattern}': {e}")

    # methods 정규화: list/tuple/set → frozenset, 대소문자 → UPPER
    if self.methods is not None:
        if not isinstance(self.methods, frozenset):
            self.methods = frozenset(m.upper() for m in self.methods)
        else:
            normalized = frozenset(m.upper() for m in self.methods)
            if normalized != self.methods:
                self.methods = normalized
```

### 11.3 방어 범위

| 입력 | 정규화 결과 |
|------|------------|
| `methods=["post", "get"]` | `frozenset({"POST", "GET"})` |
| `methods=frozenset({"delete"})` | `frozenset({"DELETE"})` |
| `methods=("Put",)` | `frozenset({"PUT"})` |
| `methods=None` | `None` (변경 없음) |
| `TierMapping.from_dict({"methods": ["post"]})` | `__post_init__` 거쳐 자동 정규화 |

---

## 12. TieringMiddleware method 전파

### 12.1 변경 필요성

9절에서 OPTIONS bypass를 추가하면 `TieringMiddleware`도 수정 대상이 됩니다. 이왕 수정하는 김에 `request.method`를 `resolve_tier_with_fallback()`에 전달하여 Emergency Mode Load Shedding에서도 method 기반 분류를 활용합니다.

### 12.2 현재 코드 (`tiering/middleware.py` L117-126)

```python
path = request.path
client_ip = self._get_client_ip(request)
user_id = self._get_user_id(request)

tier_result = self._registry.resolve_tier_with_fallback(
    path=path,
    client_ip=client_ip,
    user_id=str(user_id) if user_id else None,
)
```

### 12.3 수정 코드

```python
path = request.path
client_ip = self._get_client_ip(request)
user_id = self._get_user_id(request)
method = request.method                               # 신규

tier_result = self._registry.resolve_tier_with_fallback(
    path=path,
    client_ip=client_ip,
    user_id=str(user_id) if user_id else None,
    method=method,                                    # 신규
)
```

### 12.4 변경 범위 테이블 업데이트

2.1절의 변경 범위에 추가:

| 파일 | 변경 유형 | 규모 |
|------|-----------|------|
| `tiering/models.py` | `TierMapping` 필드 추가 + `__post_init__` + `matches()` 수정 | ~25줄 |
| `tiering/defaults.py` | 메서드 인식 매핑 추가 | ~30줄 |
| `tiering/registry.py` | LRU `OrderedDict` + `get_tier_for_request()` 확장 | ~30줄 |
| `api/django/admission_control.py` | OPTIONS bypass + `request.method` 전달 | ~6줄 |
| `tiering/middleware.py` | OPTIONS bypass + `request.method` 전달 | ~6줄 |

---

## 13. 매핑 충돌 및 우선순위 정책 명확화

### 13.1 정책 — Priority 숫자 > Method 구체성

`set_mappings()` (`registry.py` L210-213)의 현재 정렬:
```python
self._mappings = sorted(mappings, key=lambda m: m.priority, reverse=True)
```

3.4절에서 제안한 확장 정렬:
```python
key=lambda m: (m.priority, 1 if m.methods is not None else 0)
```

→ **1차: priority 숫자**, **2차: method 구체성** (동일 priority 내 tiebreaker 역할).

### 13.2 시나리오 검증

| Rule | pattern | methods | priority | 결과 |
|------|---------|---------|----------|------|
| A | `/api/*` | None (All) | 100 | 1순위 |
| B | `/api/specific` | `["GET"]` | 10 | 2순위 |

요청 `GET /api/specific` → **Rule A 적용** (priority 100 > 10).

이것이 올바른 이유: `priority=100`을 명시적으로 부여한 것은 관리자의 의도적 결정. Method 구체성을 priority보다 우위에 두면 낮은 priority의 method-specific 매핑이 높은 priority의 전역 매핑을 override하는 **예측 불가능한 동작** 발생.

### 13.3 기본 매핑의 실질적 충돌 부재

`defaults.py` 현재 매핑의 priority 체계:
- critical: `priority=100, 95`
- standard: `priority=50`
- non_essential: `priority=10`

3.5절의 신규 method-specific 매핑: `priority=70, 55`

→ critical(100/95) 매핑과 우선순위가 **겹치지 않으므로** 실질적 충돌이 발생하지 않습니다.

### 13.4 OPTIONS fallback 경로와의 상호작용

9절의 bypass가 없다고 가정한 경우 `OPTIONS /api/self-healing/config/test` 요청의 매핑 경로:

1. `priority=70` (쓰기): `methods={"POST", "PUT", "PATCH", "DELETE"}` → OPTIONS 불일치
2. `priority=55` (읽기): `methods={"GET", "HEAD"}` → OPTIONS 불일치
3. `priority=50` (기존 path-only): `methods=None` → **일치** → `standard`

→ 9절의 OPTIONS bypass가 없으면 OPTIONS는 `standard` tier로 fallback됩니다. 이는 리뷰 1(OPTIONS 처리)과 리뷰 5(우선순위 정책)가 **상호 의존적**임을 의미하며, 9절의 bypass 구현이 **필수적**인 추가 근거입니다.

---

## 14. 데이터 영속성 전략 (Phase 3 가이드)

### 14.1 현재 상태

`TierMapping`은 순수 `@dataclass` (`tiering/models.py` L85). `class TierMapping(models.Model)` 형태의 Django ORM Model은 **프로젝트에 존재하지 않습니다**.

영속성은 `export_config()`/`import_config()` (`registry.py` L637-672)를 통한 dict 직렬화 방식입니다.

### 14.2 Phase 3 DB 저장 전략 — JSONField 권장

| 선택지 | 장점 | 단점 | 채택 |
|--------|------|------|------|
| **(A) JSONField** | `from_dict()` 파싱 로직과 정확히 호환, PostgreSQL/SQLite 모두 지원 | 쿼리 성능 (인덱싱 제한) | **채택** |
| (B) Comma Separated String | 단순 | 추가 파싱 로직 (split/join) 필요, 검증 복잡 | 미채택 |
| (C) M2M 테이블 | 정규화 | 과도 (HTTP method 종류 ≤ 7개), JOIN 비용 | 미채택 |

**JSONField 채택 이유:**
- `methods` 필드가 `frozenset[str]` → JSONField에 `["GET", "POST"]` 형태로 저장하면 `from_dict()`의 기존 로직(`frozenset(data["methods"])`)과 **변경 없이** 호환
- 기존 `export_config()`의 `to_dict()` → `{"methods": ["GET", "POST"]}` 출력이 JSONField와 동일 포맷

### 14.3 Method Override 정책

`X-HTTP-Method-Override` 헤더를 처리하는 코드는 **프로젝트 전체에 존재하지 않습니다**. Django 자체도 기본 미지원.

**정책**: Tier 분류에서는 `request.method`(Django가 파싱한 실제 HTTP method)를 **그대로 신뢰**합니다.

레거시 호환이 향후 필요한 경우, `AdmissionControlMiddleware` **이전** 위치에 Method Override 미들웨어를 배치하여 `request.method` 자체를 변환하면, Tier 분류 코드는 수정 없이 자동 적용됩니다. (계층 분리 원칙)

---

## 15. 테스트 전략 (보완)

### 15.1 추가 테스트 케이스

5.1절의 테스트에 다음 케이스를 추가합니다:

```
tests/unit/api/tiering/test_tier_mapping_method.py
├── TestTierMappingMethods
│   ├── ... (기존 5개)
│   └── test_post_init_normalizes_methods_case    # methods=["post"] → frozenset({"POST"})
│   └── test_post_init_normalizes_list_to_frozenset # methods=["GET"] → frozenset({"GET"})
├── TestTierRegistryMethodResolution
│   ├── ... (기존 4개)
│   └── test_lru_eviction_on_cache_full           # 캐시 초과 시 LRU eviction 동작
│   └── test_lru_move_to_end_on_hit               # 캐시 히트 시 LRU 갱신
├── TestOptionsPreflightBypass
│   ├── test_admission_control_options_bypass      # OPTIONS → bypass (tier 분류 스킵)
│   ├── test_tiering_middleware_options_bypass      # OPTIONS → bypass (load shedding 스킵)
│   └── test_options_not_cached                    # OPTIONS bypass 시 캐시 미사용
```

### 15.2 기존 테스트 회귀 수정

캐시 키 변경으로 인한 `test_tier_registry_cache.py` 회귀 수정:

```python
# 수정 전 (회귀 실패)
assert "/api/self-healing/control/" in registry._path_tier_cache
assert registry._path_tier_cache["/unknown/path/"] is None

# 수정 후 (튜플 키 호환)
assert ("/api/self-healing/control/", None) in registry._path_tier_cache
assert registry._path_tier_cache[("/unknown/path/", None)] is None
```

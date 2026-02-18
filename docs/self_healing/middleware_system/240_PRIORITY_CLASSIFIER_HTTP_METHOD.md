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

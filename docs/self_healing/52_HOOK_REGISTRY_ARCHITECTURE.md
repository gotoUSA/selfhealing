# 52. Hook Registry Architecture

> **Enterprise-Grade Bypass Hook System with Audit Integration**

## 개요

Hook Registry는 런타임에 동적으로 동작을 수정할 수 있는 **확장 가능한 훅 시스템**입니다.  
특히 Rate Limiting 바이패스와 같은 민감한 결정을 **감사 추적 가능**하게 만듭니다.

### 핵심 가치

| 가치 | 설명 |
|------|------|
| **Domain-Free** | 프로덕션 코드에서 테스트 키워드(PLATINUM, chaos-monkey 등) 완전 제거 |
| **Audit Trail** | 모든 바이패스 결정이 해시 체인 감사 로그에 기록됨 |
| **Environment-Safe** | Production 환경에서는 자동 차단 |
| **Priority-Based** | 훅 우선순위로 실행 순서 제어 |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Production Code                               │
│  ┌─────────────────┐                                                │
│  │  rate_limit.py  │──────┐                                         │
│  └─────────────────┘      │                                         │
│                           ▼                                         │
│  ┌─────────────────────────────────────────┐                        │
│  │         BypassRegistry (core/hooks.py)   │                        │
│  │  ┌─────────────────────────────────────┐│                        │
│  │  │  should_bypass(request)             ││                        │
│  │  │  ├─ Execute hooks by priority       ││                        │
│  │  │  ├─ Return BypassResult             ││                        │
│  │  │  └─ Log to Audit System             ││                        │
│  │  └─────────────────────────────────────┘│                        │
│  └─────────────────────────────────────────┘                        │
│                           ▲                                         │
│                           │ register()                              │
└───────────────────────────┼─────────────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────────────┐
│                     Resilience Module                                │
│  ┌─────────────────────────────────────────┐                        │
│  │   resilience/bypass_hooks.py            │                        │
│  │  ┌─────────────────────────────────────┐│                        │
│  │  │  platinum_bypass_hook (priority=1000)│                        │
│  │  │  chaos_monkey_hook (priority=500)    │                        │
│  │  │  stress_test_hook (priority=300)     │                        │
│  │  │  integration_hook (priority=100)     │                        │
│  │  └─────────────────────────────────────┘│                        │
│  │                                         │                        │
│  │  if ENABLE_RESILIENCE_TESTING:          │                        │
│  │      BypassRegistry.register(...)       │                        │
│  └─────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 핵심 컴포넌트

### 1. BypassRegistry (core/hooks.py)

```python
from selfhealing.core.hooks import BypassRegistry, BypassResult

# 바이패스 체크 (프로덕션 코드에서 호출)
result: BypassResult = BypassRegistry.should_bypass(request)

if result.bypassed:
    # Audit 로그는 자동으로 기록됨
    print(f"Bypassed by: {result.hook_name}")
    print(f"Reason: {result.reason}")
```

### 2. Hook Registration (resilience/bypass_hooks.py)

```python
from selfhealing.core.hooks import BypassRegistry

def platinum_bypass_hook(request) -> bool:
    """PLATINUM 모드: 완전 바이패스"""
    return request.headers.get("X-Test-Mode") == "platinum"

# 환경 조건부 등록
if settings.ENABLE_RESILIENCE_TESTING:
    BypassRegistry.register(
        platinum_bypass_hook,
        priority=1000,
        name="platinum_mode",
        description="PLATINUM extreme stress testing"
    )
```

### 3. Audit Integration

모든 바이패스 결정은 자동으로 감사 시스템에 기록됩니다:

```json
{
    "event_type": "bypass_decision",
    "bypassed": true,
    "reason": "PLATINUM extreme stress testing - complete rate limit bypass",
    "hook_name": "platinum_mode",
    "priority": 1000,
    "timestamp": "2025-12-30T12:00:00.000Z",
    "request_path": "/api/self-healing/circuit-breaker/status/",
    "request_method": "GET"
}
```

---

## 우선순위 가이드

| Priority | 용도 | 예시 |
|----------|------|------|
| **1000+** | Emergency/Admin 오버라이드 | PLATINUM mode |
| **500-999** | 극한 테스트 모드 | chaos-monkey, HELLMODE |
| **300-499** | 일반 부하 테스트 | stress, load-test |
| **100-299** | 통합 테스트 | integration, CI/CD |
| **1-99** | 저우선순위/폴백 | 기본 테스트 모드 |

---

## 환경 설정

### Django Settings

```python
# settings/base.py (프로덕션)
ENABLE_RESILIENCE_TESTING = False  # 프로덕션에서는 비활성화

# settings/test.py (테스트)
ENABLE_RESILIENCE_TESTING = True
```

### 환경 변수

| 변수 | 값 | 설명 |
|------|-----|------|
| `ENVIRONMENT` | `production` | 프로덕션 환경 (모든 훅 차단) |
| `ENABLE_RESILIENCE_TESTING` | `true` | 회복력 테스트 훅 활성화 |
| `CHAOS_ENABLED` | `true` | Chaos Engineering 활성화 (하위 호환) |

---

## 사용 예시

### Rate Limit Middleware

```python
# rate_limit.py
class ControlAPIRateLimitMiddleware:
    def __call__(self, request):
        # Hook Registry로 바이패스 체크
        bypass_result = self._check_bypass_registry(request)
        
        if bypass_result.bypassed:
            response = self.get_response(request)
            response["X-RateLimit-Mode"] = "bypass"
            response["X-RateLimit-Bypass-Reason"] = bypass_result.hook_name
            return response
        
        # 일반 rate limit 로직...
```

### 커스텀 훅 추가

```python
# my_app/hooks.py
from selfhealing.core.hooks import register_bypass_hook

@register_bypass_hook(priority=800, name="admin_override")
def admin_bypass_hook(request):
    """관리자 요청 바이패스"""
    return request.user.is_staff and request.headers.get("X-Admin-Override")
```

---

## 모니터링

### 훅 상태 확인

```python
from selfhealing.core.hooks import BypassRegistry

# 등록된 훅 목록
hooks = BypassRegistry.get_registered_hooks()

# 통계
stats = BypassRegistry.get_statistics()
print(f"Total hooks: {stats['total_hooks']}")
print(f"Total bypasses: {stats['total_bypasses']}")
print(f"Bypass rate: {stats['bypass_rate']:.2%}")
```

### API 엔드포인트 (선택)

```
GET /api/self-healing/hooks/status/
```

---

## Big 4 감사 대응

이 시스템은 다음 컴플라이언스 요구사항을 충족합니다:

| 요구사항 | 구현 |
|----------|------|
| **감사 추적** | 모든 바이패스 결정이 해시 체인 로그에 기록 |
| **변경 불가능성** | Audit 로그는 서명되어 위변조 불가 |
| **환경 분리** | Production에서는 자동 차단 |
| **역할 기반 접근** | 훅 등록은 코드 배포로만 가능 |

---

## 마이그레이션 가이드

### Before (하드코딩)

```python
# rate_limit.py
def _should_bypass_for_xtest(self, request):
    xtest_header = request.META.get("HTTP_X_TEST_MODE", "")
    if xtest_header == "platinum":  # ❌ 하드코딩
        return True
    if xtest_header == "chaos-monkey":  # ❌ 하드코딩
        return True
    ...
```

### After (Hook Registry)

```python
# rate_limit.py
def _check_bypass_registry(self, request):
    from selfhealing.core.hooks import BypassRegistry
    return BypassRegistry.should_bypass(request)  # ✅ 위임
```

---

## 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── core/
│   └── hooks.py              # BypassRegistry (핵심)
│
├── resilience/               # 회복력 테스트 모듈
│   ├── __init__.py           # 자동 훅 등록
│   └── bypass_hooks.py       # 바이패스 훅 정의
│
├── api/django/
│   └── rate_limit.py         # BypassRegistry 사용

packages/selfhealing-python/tests/core/
├── test_hooks.py             # BypassRegistry 단위 테스트 (22개)
└── test_bypass_hooks.py      # Resilience 훅 테스트 (37개)
```

---

## 구현 상태

### ✅ 완료된 기능

| 기능 | 상태 | 설명 |
|------|------|------|
| **BypassRegistry** | ✅ 완료 | Thread-safe 싱글톤, 우선순위 기반 실행 |
| **BypassResult** | ✅ 완료 | 감사 정보 포함 결과 데이터클래스 |
| **HookInfo** | ✅ 완료 | 훅 메타데이터 및 통계 추적 |
| **Resilience Hooks** | ✅ 완료 | platinum, chaos-monkey, stress, integration |
| **환경 분리** | ✅ 완료 | Production 자동 차단 |
| **테스트** | ✅ 완료 | 59개 테스트 전체 통과 |

### 테스트 커버리지

```
tests/core/test_hooks.py           - 22 tests (BypassRegistry 핵심 기능)
tests/core/test_bypass_hooks.py    - 37 tests (Resilience 훅 & 통합)
Total: 59 tests PASSED
```

### 데드락 수정 내역 (2025-12-30)

`get_statistics()` 메서드에서 lock 내부에서 `get_registered_hooks()` 호출 시 
재진입 데드락(Reentrant Deadlock) 발생 → 인라인 리스트 컴프리헨션으로 수정

---

## 참고 문서

- [07_CONTROL_API.md](07_CONTROL_API.md) - Rate Limiting 설계
- [19_CHAOS_PROOF_ROADMAP.md](19_CHAOS_PROOF_ROADMAP.md) - Chaos Engineering
- [37_CONTINUOUS_AUDIT_IMPLEMENTATION.md](37_CONTINUOUS_AUDIT_IMPLEMENTATION.md) - 감사 시스템

---

**Version**: 1.1.0  
**Date**: 2025-12-30  
**Author**: Self-Healing Infrastructure Team  
**Last Updated**: 2025-12-30 (데드락 수정 및 테스트 완료)

# Phase 1: Auto-Instrument Middleware

## 목적

고객 코드 수정 없이 **자동으로** 프레임워크를 감지하고 계측하여 에러/성능 이벤트를 수집.

**핵심 원칙:**
- 프레임워크 자동 감지 (Django, FastAPI, Flask)
- 미들웨어 자동 등록
- 고객 코드 수정 불필요
- 성능 영향 최소화

---

## 지원 프레임워크

| 프레임워크 | 감지 방법 | 계측 방식 |
|-----------|----------|----------|
| Django | `django` 모듈 존재 | Middleware |
| FastAPI | `fastapi` 모듈 존재 | Middleware |
| Flask | `flask` 모듈 존재 | before_request / after_request |
| ASGI Generic | `asgiref` 존재 | ASGI Middleware |
| WSGI Generic | fallback | WSGI Middleware |

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    selfhealing.init()                            │
│                           │                                      │
│                           ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                 Framework Detector                       │    │
│  │  - Django 감지: django.conf.settings 확인               │    │
│  │  - FastAPI 감지: fastapi.FastAPI 확인                   │    │
│  │  - Flask 감지: flask.Flask 확인                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           │                                      │
│           ┌───────────────┼───────────────┐                     │
│           ▼               ▼               ▼                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ Django      │  │ FastAPI     │  │ Flask       │             │
│  │ Instrumentor│  │ Instrumentor│  │ Instrumentor│             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│           │               │               │                     │
│           ▼               ▼               ▼                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Unified Event Collector                     │    │
│  │  - HTTP 에러 캡처                                        │    │
│  │  - 응답 시간 측정                                        │    │
│  │  - 예외 캡처                                             │    │
│  └─────────────────────────────────────────────────────────┘    │
│                           │                                      │
│                           ▼                                      │
│                   HTTP Exporter                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 구현 상세

### 1. Framework Detector

```python
"""
selfhealing/instrument/detector.py
"""

import importlib
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Framework(Enum):
    DJANGO = "django"
    FASTAPI = "fastapi"
    FLASK = "flask"
    ASGI = "asgi"
    WSGI = "wsgi"
    UNKNOWN = "unknown"


def detect_framework() -> Framework:
    """
    현재 환경에서 사용 중인 웹 프레임워크 감지.
    """
    # Django 감지
    try:
        import django
        from django.conf import settings
        if settings.configured:
            logger.info("[Instrument] Detected Django framework")
            return Framework.DJANGO
    except (ImportError, Exception):
        pass
    
    # FastAPI 감지
    try:
        import fastapi
        logger.info("[Instrument] Detected FastAPI framework")
        return Framework.FASTAPI
    except ImportError:
        pass
    
    # Flask 감지
    try:
        import flask
        logger.info("[Instrument] Detected Flask framework")
        return Framework.FLASK
    except ImportError:
        pass
    
    # ASGI 감지
    try:
        import asgiref
        logger.info("[Instrument] Detected ASGI application")
        return Framework.ASGI
    except ImportError:
        pass
    
    logger.warning("[Instrument] No supported framework detected")
    return Framework.UNKNOWN


def get_instrumentor(framework: Framework):
    """프레임워크에 맞는 Instrumentor 반환"""
    if framework == Framework.DJANGO:
        from selfhealing.instrument.django import DjangoInstrumentor
        return DjangoInstrumentor()
    
    elif framework == Framework.FASTAPI:
        from selfhealing.instrument.fastapi import FastAPIInstrumentor
        return FastAPIInstrumentor()
    
    elif framework == Framework.FLASK:
        from selfhealing.instrument.flask import FlaskInstrumentor
        return FlaskInstrumentor()
    
    else:
        return None
```

### 2. Django Instrumentor

```python
"""
selfhealing/instrument/django.py
"""

import logging
import time
import traceback
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


class SelfHealingMiddleware:
    """
    Django 미들웨어: HTTP 요청/응답 계측.
    """
    
    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._exclude_paths: List[str] = []
        self._slow_threshold_ms: int = 3000
        
        # 설정 로드
        self._load_config()
    
    def _load_config(self) -> None:
        """selfhealing 설정 로드"""
        try:
            import selfhealing
            config = selfhealing.get_config()
            self._exclude_paths = config.get("exclude_paths", [])
            self._slow_threshold_ms = config.get("slow_threshold_ms", 3000)
        except Exception:
            pass
    
    def __call__(self, request):
        # 제외 경로 체크
        if self._should_exclude(request.path):
            return self.get_response(request)
        
        # 시작 시간 기록
        start_time = time.perf_counter()
        
        # 요청 처리
        response = None
        exception = None
        
        try:
            response = self.get_response(request)
        except Exception as e:
            exception = e
            raise
        finally:
            # 응답 시간 계산
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # 이벤트 기록
            self._record_request(
                request=request,
                response=response,
                exception=exception,
                duration_ms=duration_ms,
            )
        
        return response
    
    def _should_exclude(self, path: str) -> bool:
        """경로 제외 여부 확인"""
        for exclude in self._exclude_paths:
            if path.startswith(exclude):
                return True
        return False
    
    def _record_request(
        self,
        request,
        response,
        exception: Optional[Exception],
        duration_ms: float,
    ) -> None:
        """요청 정보를 이벤트로 기록"""
        from selfhealing.exporter.events import emit_event, EventType
        
        # 예외 발생
        if exception is not None:
            emit_event(
                EventType.EXCEPTION_CAUGHT,
                endpoint=request.path,
                method=request.method,
                error_type=type(exception).__name__,
                error_message=str(exception),
                error_traceback=traceback.format_exc(),
                details={
                    "duration_ms": duration_ms,
                }
            )
            return
        
        # HTTP 에러 (4xx, 5xx)
        if response is not None and response.status_code >= 400:
            emit_event(
                EventType.HTTP_ERROR,
                endpoint=request.path,
                method=request.method,
                details={
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
        
        # 느린 요청
        elif duration_ms > self._slow_threshold_ms:
            emit_event(
                EventType.HTTP_SLOW,
                endpoint=request.path,
                method=request.method,
                details={
                    "status_code": response.status_code if response else None,
                    "duration_ms": duration_ms,
                    "threshold_ms": self._slow_threshold_ms,
                }
            )


class DjangoInstrumentor:
    """Django 프레임워크 Instrumentor"""
    
    def __init__(self):
        self._installed = False
    
    def instrument(self) -> bool:
        """Django 미들웨어 자동 등록"""
        if self._installed:
            return True
        
        try:
            from django.conf import settings
            
            middleware_path = "selfhealing.instrument.django.SelfHealingMiddleware"
            
            # 미들웨어 리스트에 추가
            if hasattr(settings, "MIDDLEWARE"):
                if middleware_path not in settings.MIDDLEWARE:
                    # 가장 앞에 추가 (모든 요청 캡처)
                    settings.MIDDLEWARE = [middleware_path] + list(settings.MIDDLEWARE)
                    logger.info("[Instrument] Django middleware installed")
            
            self._installed = True
            return True
        
        except Exception as e:
            logger.error(f"[Instrument] Failed to install Django middleware: {e}")
            return False
    
    def uninstrument(self) -> None:
        """미들웨어 제거"""
        try:
            from django.conf import settings
            
            middleware_path = "selfhealing.instrument.django.SelfHealingMiddleware"
            
            if hasattr(settings, "MIDDLEWARE"):
                settings.MIDDLEWARE = [
                    m for m in settings.MIDDLEWARE
                    if m != middleware_path
                ]
            
            self._installed = False
            logger.info("[Instrument] Django middleware removed")
        
        except Exception as e:
            logger.error(f"[Instrument] Failed to remove Django middleware: {e}")
```

### 3. FastAPI Instrumentor

```python
"""
selfhealing/instrument/fastapi_inst.py
"""

import logging
import time
import traceback
from typing import Callable, List

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class SelfHealingASGIMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette ASGI 미들웨어.
    """
    
    def __init__(self, app, exclude_paths: List[str] = None):
        super().__init__(app)
        self._exclude_paths = exclude_paths or []
        self._slow_threshold_ms = 3000
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 제외 경로 체크
        if self._should_exclude(request.url.path):
            return await call_next(request)
        
        start_time = time.perf_counter()
        exception = None
        response = None
        
        try:
            response = await call_next(request)
        except Exception as e:
            exception = e
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self._record_request(request, response, exception, duration_ms)
        
        return response
    
    def _should_exclude(self, path: str) -> bool:
        for exclude in self._exclude_paths:
            if path.startswith(exclude):
                return True
        return False
    
    def _record_request(self, request, response, exception, duration_ms):
        from selfhealing.exporter.events import emit_event, EventType
        
        if exception:
            emit_event(
                EventType.EXCEPTION_CAUGHT,
                endpoint=str(request.url.path),
                method=request.method,
                error_type=type(exception).__name__,
                error_message=str(exception),
                error_traceback=traceback.format_exc(),
            )
        elif response and response.status_code >= 400:
            emit_event(
                EventType.HTTP_ERROR,
                endpoint=str(request.url.path),
                method=request.method,
                details={"status_code": response.status_code, "duration_ms": duration_ms},
            )
        elif duration_ms > self._slow_threshold_ms:
            emit_event(
                EventType.HTTP_SLOW,
                endpoint=str(request.url.path),
                method=request.method,
                details={"duration_ms": duration_ms},
            )


class FastAPIInstrumentor:
    """FastAPI 프레임워크 Instrumentor"""
    
    def __init__(self):
        self._app = None
        self._installed = False
    
    def instrument(self, app=None) -> bool:
        """FastAPI 앱에 미들웨어 추가"""
        if self._installed:
            return True
        
        try:
            # 앱이 주어지지 않으면 자동 감지 시도
            if app is None:
                app = self._find_app()
            
            if app is None:
                logger.warning("[Instrument] FastAPI app not found")
                return False
            
            app.add_middleware(SelfHealingASGIMiddleware)
            self._app = app
            self._installed = True
            logger.info("[Instrument] FastAPI middleware installed")
            return True
        
        except Exception as e:
            logger.error(f"[Instrument] Failed to install FastAPI middleware: {e}")
            return False
    
    def _find_app(self):
        """FastAPI 앱 자동 감지"""
        import sys
        import gc
        
        # gc를 통해 FastAPI 인스턴스 찾기
        try:
            from fastapi import FastAPI
            for obj in gc.get_objects():
                if isinstance(obj, FastAPI):
                    return obj
        except Exception:
            pass
        
        return None
```

### 4. 통합 초기화

```python
"""
selfhealing/__init__.py (확장)
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_config: Dict[str, Any] = {}
_initialized = False


def init(
    api_key: str,
    endpoint: str = "https://api.selfhealing.io",
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    auto_instrument: bool = True,
    exclude_paths: Optional[list] = None,
    sample_rate: float = 1.0,
    **kwargs
) -> None:
    """
    Self-Healing SDK 초기화.
    
    이 한 줄로 모든 자동 계측이 활성화됩니다.
    
    Example:
        import selfhealing
        selfhealing.init(api_key="sk_live_xxx")
    
    Args:
        api_key: API 인증 키
        endpoint: Cloud API 엔드포인트
        service_name: 서비스 이름 (자동 감지)
        environment: 환경 이름 (자동 감지)
        auto_instrument: 자동 계측 활성화 (기본: True)
        exclude_paths: 계측 제외 경로
        sample_rate: 샘플링 비율 (1.0 = 100%)
    """
    global _config, _initialized
    
    if _initialized:
        logger.warning("[SelfHealing] Already initialized")
        return
    
    # 설정 저장
    _config = {
        "api_key": api_key,
        "endpoint": endpoint,
        "service_name": service_name or _detect_service_name(),
        "environment": environment or _detect_environment(),
        "exclude_paths": exclude_paths or ["/health", "/metrics", "/favicon.ico"],
        "sample_rate": sample_rate,
        **kwargs,
    }
    
    # HTTP Exporter 초기화
    from selfhealing.exporter import HTTPExporter, ExporterConfig
    
    exporter_config = ExporterConfig(
        api_key=api_key,
        endpoint=endpoint,
        service_name=_config["service_name"],
        environment=_config["environment"],
    )
    HTTPExporter.initialize(exporter_config)
    
    # Exception Hook 설치
    from selfhealing.instrument.exception_hook import install_exception_hook
    install_exception_hook()
    
    # 자동 계측
    if auto_instrument:
        from selfhealing.instrument.detector import detect_framework, get_instrumentor
        
        framework = detect_framework()
        instrumentor = get_instrumentor(framework)
        
        if instrumentor:
            instrumentor.instrument()
    
    _initialized = True
    logger.info(f"[SelfHealing] Initialized - service={_config['service_name']}, env={_config['environment']}")


def get_config() -> Dict[str, Any]:
    """현재 설정 반환"""
    return _config.copy()


def _detect_service_name() -> str:
    """서비스 이름 자동 감지"""
    import os
    import sys
    
    # 환경 변수
    for var in ["SERVICE_NAME", "APP_NAME", "HOSTNAME"]:
        if os.getenv(var):
            return os.getenv(var)
    
    # Django 설정
    try:
        from django.conf import settings
        if hasattr(settings, "ROOT_URLCONF"):
            return settings.ROOT_URLCONF.split(".")[0]
    except Exception:
        pass
    
    # 현재 디렉토리
    return os.path.basename(os.getcwd())


def _detect_environment() -> str:
    """환경 자동 감지"""
    import os
    
    for var in ["ENVIRONMENT", "ENV", "DJANGO_ENV", "FLASK_ENV", "APP_ENV"]:
        if os.getenv(var):
            return os.getenv(var)
    
    # Django DEBUG 설정
    try:
        from django.conf import settings
        if hasattr(settings, "DEBUG"):
            return "development" if settings.DEBUG else "production"
    except Exception:
        pass
    
    return "development"
```

---

## 수집되는 이벤트

| 이벤트 | 조건 | 데이터 |
|--------|------|--------|
| `http.error` | 4xx/5xx 응답 | endpoint, method, status_code, duration_ms |
| `http.slow` | 응답 > 3초 | endpoint, method, duration_ms, threshold_ms |
| `exception.caught` | 예외 발생 | endpoint, error_type, error_message, traceback |

---

## 제외 경로 기본값

```python
DEFAULT_EXCLUDE_PATHS = [
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/live",
    "/livez",
    "/metrics",
    "/favicon.ico",
    "/static/",
    "/media/",
    "/__debug__/",
]
```

---

## 성능 영향

| 항목 | 영향 |
|------|------|
| 요청당 오버헤드 | < 0.1ms |
| 메모리 | 미들웨어 인스턴스 ~1KB |
| 비동기 전송 | 메인 스레드 블로킹 없음 |

---

## 다음 단계

→ [03-PHASE1-EXCEPTION-HOOK.md](03-PHASE1-EXCEPTION-HOOK.md)

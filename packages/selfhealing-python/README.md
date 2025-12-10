# SelfHealing

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Self-Healing Reliability Layer for Python Applications - 분산 시스템을 위한 자동 복구 라이브러리

## 🎯 특징

- **Circuit Breaker**: 연쇄 장애 방지를 위한 서킷 브레이커 패턴
- **Dead Letter Queue (DLQ)**: 실패한 작업의 저장 및 재시도 관리
- **Automatic Retry**: 지수/선형/상수 백오프 전략 지원
- **Replay System**: 실패한 작업의 자동/수동 재시도
- **Prometheus Metrics**: 완전한 관찰성을 위한 메트릭 수집
- **Framework Agnostic**: Django, FastAPI, Flask 등 다양한 프레임워크 지원

## 📦 설치

### 기본 설치 (프레임워크 독립적)

```bash
pip install selfhealing
```

### Django와 함께 사용

```bash
pip install selfhealing[django]
```

### Celery와 함께 사용

```bash
pip install selfhealing[celery]
```

### 모든 기능 설치

```bash
pip install selfhealing[all]
```

## 🚀 빠른 시작

### 기본 설정

```python
from selfhealing import configure
from selfhealing.core import SelfHealingConfig

# 간단한 설정
configure(
    debug_mode=False,
    auto_replay_enabled=True,
)

# 또는 상세 설정
config = SelfHealingConfig.from_dict({
    "circuit_breaker": {
        "failure_threshold": 5,
        "recovery_timeout": 60,
    },
    "dlq": {
        "max_retries": 3,
        "expiry_hours": 72,
    },
    "retry": {
        "backoff_strategy": "exponential",
        "base_delay": 1.0,
        "max_delay": 300.0,
    },
})
```

### Circuit Breaker 사용

```python
from selfhealing.core import CircuitState

# Circuit Breaker는 서비스 호출을 보호합니다
# 연속 실패 시 회로를 열어 빠른 실패를 반환합니다
```

### Backoff Calculator 사용

```python
from selfhealing.core.backoff import get_backoff_calculator

# 지수 백오프
backoff = get_backoff_calculator("exponential", base_delay=1.0, max_delay=300.0)

# 재시도 딜레이 계산
delay = backoff.calculate(attempt=3)  # 4.0초 (jitter 적용 시 변동)
```

## 🏗️ 아키텍처

```
selfhealing/
├── core/           # 프레임워크 독립적 핵심 로직
│   ├── types.py    # 타입 정의 (Enum, Dataclass)
│   ├── config.py   # 설정 관리
│   └── backoff.py  # 백오프 계산 전략
├── interfaces/     # 추상 인터페이스 (Repository 패턴)
├── adapters/       # 프레임워크별 어댑터
│   ├── django/     # Django ORM 구현
│   └── celery/     # Celery 태스크 구현
├── api/            # REST API
│   └── django/     # DRF 뷰/시리얼라이저
└── metrics/        # Prometheus 메트릭
```

## 🔧 Django 통합

### settings.py

```python
INSTALLED_APPS = [
    # ...
    'selfhealing.adapters.django',
]

# Self-Healing 설정
SELFHEALING = {
    "circuit_breaker": {
        "failure_threshold": 5,
        "recovery_timeout": 60,
    },
    "dlq": {
        "max_retries": 3,
    },
}
```

### urls.py

```python
from django.urls import path, include

urlpatterns = [
    # ...
    path('api/self-healing/', include('selfhealing.api.django.urls')),
]
```

## 📊 Prometheus 메트릭

```python
from selfhealing.metrics import get_metrics

metrics = get_metrics()

# Circuit Breaker 상태 기록
metrics.set_circuit_state("payment-gateway", "open")

# DLQ 카운트 기록
metrics.set_dlq_count("pending", "payment", 5)

# 재시도 기록
metrics.record_retry("payment", success=True, delay=2.5)
```

### 사용 가능한 메트릭

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `selfhealing_circuit_breaker_state` | Gauge | 서킷 브레이커 상태 |
| `selfhealing_circuit_breaker_failures_total` | Counter | 총 실패 횟수 |
| `selfhealing_dlq_operations` | Gauge | DLQ 작업 수 |
| `selfhealing_retry_attempts_total` | Counter | 총 재시도 횟수 |
| `selfhealing_replay_operations_total` | Counter | 총 재생 작업 수 |

## 🧪 테스트

```bash
# 개발 의존성 설치
pip install -e ".[dev]"

# 테스트 실행
pytest tests/ -v

# 커버리지 포함
pytest tests/ --cov=src/selfhealing --cov-report=html
```

## 📝 개발 로드맵

- [x] Core 모듈 (types, config, backoff)
- [x] Repository 인터페이스
- [x] Prometheus 메트릭
- [x] Django 어댑터
- [x] Celery 어댑터
- [x] REST API
- [x] 문서화 (Sprint 12 완료)

### 향후 계획
- [ ] FastAPI 어댑터
- [ ] Flask 어댑터
- [ ] Redis 기반 Repository
- [ ] AsyncIO 지원
- [ ] OpenTelemetry 통합

## 📄 라이선스

MIT License - 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.

## 🤝 기여

기여를 환영합니다! Issue나 Pull Request를 통해 참여해 주세요.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

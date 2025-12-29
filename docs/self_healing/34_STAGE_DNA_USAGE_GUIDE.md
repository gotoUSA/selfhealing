# Stage DNA 수동 선언 가이드 (레퍼런스)

📅 **작성일**: 2025-12-29  
🎯 **목적**: Stage DNA 수동 선언 방법 및 레퍼런스  
📋 **버전**: v1.1.0

---

> ⚠️ **참고**: 자동 분석 도구 `DNAAnalyzer`가 도입되었습니다.
> 자동 분석을 권장하며, 이 문서는 **수동 선언이 필요한 경우의 레퍼런스**입니다.
> 
> 👉 자동 분석 가이드: [35_DNA_ANALYZER_GUIDE.md](./35_DNA_ANALYZER_GUIDE.md)

---

## 📌 Stage DNA란?

**Stage DNA는 테스트 시나리오의 "유전자 정보"입니다.**

각 테스트 Stage 파일에 DNA를 선언하면, Self-Healing 시스템이 해당 Stage에 필요한 모듈들을 **자동으로 검증**하고, 테스트 실행에 필요한 **설정을 자동 튜닝**합니다.

### 핵심 기능

1. **모듈 검증**: Stage에서 사용하는 Self-Healing 모듈이 올바르게 구성되었는지 확인
2. **자동 튜닝**: Stage 유형에 따라 Rate Limit, Timeout, 우선순위 등 자동 설정
3. **HTTP 헤더 생성**: X-Test-Mode, X-Recovery-Priority 등 헤더 자동 주입
4. **비용 추적**: 복구 작업의 비용을 추적하고 예산 초과 방지
5. **규정 준수**: DORA, PCI-DSS 등 규정 준수 상태 자동 검증

---

## 🚀 빠른 시작

### 1단계: Stage 파일에 DNA 선언

```python
# load_tests/scenarios/integration/stage_example.py

# =============================================================================
# Stage DNA - Self-Healing 모듈 의존성 선언
# =============================================================================
STAGE_DNA = {
    "name": "Stage XX - My Test",
    "type": "integration",  # smoke, load, chaos, integration, platinum
    "required_modules": ["circuit_breaker", "dlq", "health", "observability"],
    "optional_modules": ["governance", "reconciliation"],
}

# DNA 검증 실행
from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
_dna_result = validate_stage_dna(STAGE_DNA)
if not _dna_result.is_valid:
    import warnings
    warnings.warn(str(_dna_result))
```

### 2단계: 테스트 실행

```bash
PYTHONPATH=. locust -f load_tests/scenarios/integration/stage_example.py \
    --host=http://localhost:8000 --users=10 --spawn-rate=5 --run-time=60s --headless
```

DNA 검증에 실패하면 경고가 표시되고, 어떤 모듈이 누락되었는지 알려줍니다.

---

## 📊 Stage 유형별 필수 모듈

| Stage 유형 | 필수 모듈 | 권장 모듈 |
|-----------|----------|----------|
| **smoke** | `health` | - |
| **load** | `circuit_breaker`, `error_budget`, `health` | `state_cache`, `adaptive_jitter`, `throttle`, `rate_limiter` |
| **chaos** | `circuit_breaker`, `chaos`, `xtest`, `emergency`, `dlq`, `health` | `observability`, `corruption_shield` |
| **integration** | `circuit_breaker`, `dlq`, `observability`, `health` | `reconciliation`, `governance`, `l2_storage` |
| **platinum** | 전체 Core + Advanced 모듈 | 모든 모듈 |

---

## 🧬 DNA 확장 기능

### FinOps DNA - 비용 관리

```python
STAGE_DNA = {
    "name": "Stage XX - Cost Controlled Test",
    "type": "integration",
    "required_modules": ["circuit_breaker", "dlq", "health"],
    
    # FinOps DNA 설정
    "max_recovery_budget": "$10.00",  # 최대 복구 예산
    "cost_per_retry": "$0.001",       # 재시도당 비용
}
```

### Rollback DNA - 자동 롤백

```python
STAGE_DNA = {
    "name": "Stage XX - Safe Rollback Test",
    "type": "chaos",
    "required_modules": ["circuit_breaker", "chaos", "emergency"],
    
    # Rollback DNA 설정
    "rollback_strategy": "automatic",  # automatic, manual, canary
    "rollback_timeout_seconds": 120,
}
```

### Blast Radius DNA - 장애 격리

```python
STAGE_DNA = {
    "name": "Stage XX - Isolated Chaos Test",
    "type": "chaos",
    "required_modules": ["circuit_breaker", "chaos", "xtest"],
    
    # Blast Radius DNA 설정
    "blast_radius": "isolated",       # isolated, limited, moderate, extensive
    "affected_services": ["payment", "inventory"],
}
```

### Compliance DNA - 규정 준수

```python
STAGE_DNA = {
    "name": "Stage XX - Compliant Test",
    "type": "platinum",
    "required_modules": ["circuit_breaker", "dlq", "observability"],
    
    # Compliance DNA 설정
    "regulatory_standards": ["DORA_2025", "PCI-DSS"],
}
```

### Self-Learning DNA - 자가 학습

```python
STAGE_DNA = {
    "name": "Stage XX - Learning Test",
    "type": "platinum",
    "required_modules": ["circuit_breaker", "dlq", "observability"],
    
    # Self-Learning DNA 설정
    "enable_self_learning": True,
    "suggestion_threshold": 0.8,  # 80% 신뢰도 이상일 때 제안 생성
}
```

---

## ⚙️ 동작 원리

### 1. DNA 검증 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│  Stage 파일 로드                                                 │
│  ↓                                                               │
│  STAGE_DNA 변수 파싱                                             │
│  ↓                                                               │
│  validate_stage_dna() 호출                                       │
│  ↓                                                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 1. Stage 유형 확인 (smoke/load/chaos/integration/platinum)  │ │
│  │ 2. 필수 모듈 체크 (REQUIRED_BY_TYPE)                        │ │
│  │ 3. 권장 모듈 체크 (RECOMMENDED_BY_TYPE)                      │ │
│  │ 4. Unknown 모듈 감지 (Strict Mode)                          │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  ↓                                                               │
│  ValidationResult 반환                                           │
│  ↓                                                               │
│  ✅ PASS 또는 ❌ FAIL + 경고 메시지                              │
└─────────────────────────────────────────────────────────────────┘
```

### 2. 자동 튜닝 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│  apply_stage_tuning(STAGE_DNA) 호출                             │
│  ↓                                                               │
│  Stage 유형별 튜닝 설정 로드                                     │
│  ↓                                                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ chaos/platinum 유형:                                         │ │
│  │   - auto_whitelist: True (Rate Limit 바이패스)               │ │
│  │   - xtest_mode: True (X-Test-Mode 헤더)                     │ │
│  │   - rate_limit_multiplier: 5~10x                            │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  ↓                                                               │
│  HTTP 헤더 자동 생성                                             │
│  ↓                                                               │
│  enhanced_dna 반환                                               │
└─────────────────────────────────────────────────────────────────┘
```

### 3. HTTP 헤더 자동 생성

```python
from load_tests.utils.selfhealing.stage_dna import get_http_headers_for_stage

headers = get_http_headers_for_stage(STAGE_DNA)
# 결과:
# {
#     "X-Test-Mode": "true",
#     "X-Test-Bypass-RateLimit": "true",
#     "X-Recovery-Priority": "high",
#     "X-Stage-Name": "Stage XX - My Test",
#     "X-Stage-Type": "chaos",
#     "X-RateLimit-Multiplier": "5.0"
# }
```

---

## 🔧 고급 사용법

### Strict Mode 설정

Unknown 모듈을 사용하려고 하면 다양한 레벨로 처리할 수 있습니다:

```python
from load_tests.utils.selfhealing.stage_dna import (
    validate_stage_dna, 
    StrictModeLevel,
    DNAValidationError
)

# OFF: 경고만 (기본)
result = validate_stage_dna(STAGE_DNA, strict_mode=StrictModeLevel.OFF)

# WARN: 경고 + 로그 기록
result = validate_stage_dna(STAGE_DNA, strict_mode=StrictModeLevel.WARN)

# BLOCK: 경고 + Exception 발생
try:
    result = validate_stage_dna(STAGE_DNA, strict_mode=StrictModeLevel.BLOCK)
except DNAValidationError as e:
    print(f"DNA 검증 실패: {e}")

# FATAL: 경고 + 프로세스 종료
result = validate_stage_dna(STAGE_DNA, strict_mode=StrictModeLevel.FATAL)
```

### 모든 Stage 파일 일괄 검증

```bash
cd myproject
python -m load_tests.utils.selfhealing.stage_dna --dir load_tests/scenarios
```

또는 코드에서:

```python
from load_tests.utils.selfhealing.stage_dna import validate_all_stages

results = validate_all_stages(
    stages_dir="load_tests/scenarios",
    strict=False,
    verbose=True
)
```

### DNA 템플릿 생성

```bash
python -m load_tests.utils.selfhealing.stage_dna \
    --generate chaos \
    --name "Stage 99 - My Chaos Test"
```

---

## 📡 REST API 사용

DNA 서비스들은 REST API를 통해서도 사용할 수 있습니다.

### FinOps API

```bash
# 예산 설정
curl -X POST http://localhost:8000/api/self-healing/dna/finops/budget/stage14/ \
    -H "Content-Type: application/json" \
    -d '{"max_budget": "10.00", "hard_limit": true}'

# 비용 리포트 조회
curl http://localhost:8000/api/self-healing/dna/finops/report/?period=daily
```

### Compliance API

```bash
# 규정 준수 검사 실행
curl -X POST http://localhost:8000/api/self-healing/dna/compliance/check/stage14/ \
    -H "Content-Type: application/json" \
    -d '{"standards": ["DORA_2025"]}'

# 위반 목록 조회
curl http://localhost:8000/api/self-healing/dna/compliance/violations/?unresolved=true
```

### Rollback API

```bash
# 롤백 정책 설정
curl -X POST http://localhost:8000/api/self-healing/dna/rollback/policy/stage14/ \
    -H "Content-Type: application/json" \
    -d '{"strategy": "automatic", "timeout_seconds": 120}'

# 롤백 요청
curl -X POST http://localhost:8000/api/self-healing/dna/rollback/request/ \
    -H "Content-Type: application/json" \
    -d '{"stage_name": "stage14", "reason": "Circuit breaker open"}'
```

### Blast Radius API

```bash
# 영향 평가 수행
curl -X POST http://localhost:8000/api/self-healing/dna/blast-radius/assessment/ \
    -H "Content-Type: application/json" \
    -d '{"stage_name": "stage14", "trigger_event": "payment_failure", "failing_services": ["payment"]}'

# 의존성 그래프 조회
curl http://localhost:8000/api/self-healing/dna/blast-radius/graph/
```

### Learning API

```bash
# 학습 세션 시작
curl -X POST http://localhost:8000/api/self-healing/dna/learning/session/start/ \
    -H "Content-Type: application/json" \
    -d '{"stage_name": "stage14"}'

# 제안 조회
curl http://localhost:8000/api/self-healing/dna/learning/suggestions/?unapplied=true

# Cross-Stage 인사이트 조회
curl http://localhost:8000/api/self-healing/dna/learning/insights/
```

---

## 🧪 테스트 클라이언트 모듈

`load_tests/utils/selfhealing/` 디렉토리에 있는 DNA 테스트 클라이언트들:

| 모듈 | 용도 |
|-----|------|
| `dna_safety.py` | Rollback + Blast Radius 테스트 |
| `dna_finops.py` | FinOps 비용 추적 테스트 |
| `dna_compliance.py` | Compliance 규정 검사 테스트 |
| `dna_learning.py` | Self-Learning 패턴 학습 테스트 |
| `dna_drift.py` | DNA Drift 감지 테스트 |
| `dna_discovery.py` | 미사용 모듈 발견 테스트 |
| `dna_mutation.py` | Mutation 테스트 |
| `dna_graph.py` | 의존성 그래프 테스트 |
| `dna_metrics.py` | Metrics vs DNA 충돌 감지 테스트 |
| `dna_zerobase.py` | Zero-Base 시나리오 테스트 |
| `dna_innovation.py` | Auto-Suggestion + Cross-Stage Learning 테스트 |

---

## 📋 체크리스트

Stage에 DNA를 적용할 때 확인할 사항:

- [ ] `STAGE_DNA` 딕셔너리 선언
- [ ] `name` 필드에 Stage 이름 설정
- [ ] `type` 필드에 올바른 유형 설정 (smoke/load/chaos/integration/platinum)
- [ ] `required_modules` 필드에 사용하는 모듈 나열
- [ ] `validate_stage_dna()` 호출하여 검증 실행
- [ ] 검증 결과 경고 처리

---

## ❓ FAQ

### Q: Stage DNA를 선언하지 않으면 어떻게 되나요?

A: 테스트는 정상 실행되지만, Self-Healing 시스템의 자동 튜닝과 검증 혜택을 받지 못합니다. 모듈 누락이나 설정 오류를 사전에 발견하기 어렵습니다.

### Q: 새로운 모듈을 추가하려면?

A: `stage_dna.py`의 `ALL_AVAILABLE_MODULES` 집합에 모듈 이름을 추가하고, 필요한 경우 `REQUIRED_BY_TYPE` 또는 `RECOMMENDED_BY_TYPE`에도 추가하세요.

### Q: Platinum 유형은 언제 사용하나요?

A: 모든 Self-Healing 기능을 최대로 활용해야 하는 고급 테스트에 사용합니다. Rate Limit 10배 증가, 최고 우선순위, 모든 Advanced 모듈 필요 등 가장 높은 수준의 설정이 적용됩니다.

### Q: DNA 검증이 실패해도 테스트를 실행하고 싶으면?

A: `strict_mode=StrictModeLevel.OFF`로 설정하면 경고만 표시하고 테스트는 계속 실행됩니다.

---

## 📚 관련 문서

- [29_STAGE_DNA_EVOLUTION_MASTER.md](29_STAGE_DNA_EVOLUTION_MASTER.md) - DNA 마스터 가이드
- [30_DNA_DRIFT_DISCOVERY.md](30_DNA_DRIFT_DISCOVERY.md) - Drift 감지 상세
- [31_DNA_ADVANCED_FEATURES.md](31_DNA_ADVANCED_FEATURES.md) - 고급 기능
- [32_DNA_ENTERPRISE_FEATURES.md](32_DNA_ENTERPRISE_FEATURES.md) - Enterprise 기능
- [33_DNA_SAFETY_FEATURES.md](33_DNA_SAFETY_FEATURES.md) - Safety 기능
- [27_SELFHEALING_SCENARIO_MAPPING.md](27_SELFHEALING_SCENARIO_MAPPING.md) - 모듈 매핑

---

**작성자**: GitHub Copilot (Claude Opus 4.5)  
**검토자**: System Architect  
**작성일**: 2025-12-29

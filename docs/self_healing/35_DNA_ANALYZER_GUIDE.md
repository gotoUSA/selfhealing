# DNA Analyzer 사용 가이드

📅 **작성일**: 2025-12-29  
🎯 **목적**: DNAAnalyzer를 통한 자동 Stage DNA 분석 및 추천  
📋 **버전**: v1.0.0

---

## 📌 개요

**DNAAnalyzer**는 Stage DNA의 한계를 극복하기 위해 만들어진 **지능형 분석 도구**입니다.

### 기존 수동 DNA의 문제점

| 문제 | 설명 |
|------|------|
| 🤷 누가 선언하나? | 개발자가 "감"으로 필요한 모듈을 선언 |
| ❓ 뭐가 필요한지 어떻게 아나? | 경험에 의존, 누락/오버엔지니어링 발생 |
| 📄 검증이 안됨 | 선언만 하고 실제 사용 여부 확인 불가 |

### DNAAnalyzer의 해결책

| 기능 | 설명 |
|------|------|
| 🔍 **자동 감지** | 코드 분석으로 실제 사용하는 모듈 탐지 |
| 📊 **테스트 결과 분석** | 에러 패턴 기반 모듈 추천 |
| ⚠️ **오버엔지니어링 감지** | 선언했지만 안 쓰는 모듈 발견 |
| 🆕 **Gap 분석** | 기존 모듈로 해결 안 되는 영역 제안 |
| 📝 **최적 DNA 생성** | 분석 결과 기반 정확한 STAGE_DNA 자동 생성 |

---

## 🚀 빠른 시작

### 1. 단일 Stage 분석

```python
from load_tests.utils.selfhealing.dna_analyzer import DNAAnalyzer

analyzer = DNAAnalyzer()

# Stage 파일 분석
result = analyzer.analyze_stage_file('load_tests/scenarios/integration/stage14_dlq_api_test.py')

# 결과 출력
print(result.get_summary())
```

**출력 예시:**
```
📊 Stage DNA 분석 결과: stage14_dlq_api_test.py
============================================================

📝 선언된 모듈: ['circuit_breaker', 'dlq', 'governance', 'health', 'observability']
🔍 감지된 모듈: ['dlq', 'observability']

⚠️ 오버엔지니어링 (선언했지만 미사용): ['circuit_breaker', 'governance', 'health']

💡 추천사항:
  ➖ [LOW] circuit_breaker: 선언되었지만 코드에서 사용되지 않음 - 제거 권장
  ➖ [LOW] governance: 선언되었지만 코드에서 사용되지 않음 - 제거 권장
  ➖ [LOW] health: 선언되었지만 코드에서 사용되지 않음 - 제거 권장
```

### 2. 테스트 결과 기반 추천

테스트 실패 로그를 분석하여 필요한 모듈을 추천합니다:

```python
# 테스트 결과 분석
test_output = """
FAILED test_payment - TimeoutError: Connection timed out after 30s
ERROR test_order - 429 Too Many Requests
FAILED test_webhook - ConnectionError: Max retries exceeded
"""

# 기존 분석 결과에 테스트 결과 추가
result = analyzer.analyze_test_results(result, test_output)

print(result.get_summary())
```

**추천 예시:**
```
💡 추천사항:
  ➕ [HIGH] rate_limiter: 타임아웃 발생 시 Circuit Breaker로 빠른 실패 처리
  ➕ [HIGH] adaptive_jitter: Rate Limit 발생 시 부하 분산
  ➕ [HIGH] error_budget: 재시도 소진 시 DLQ로 나중에 재처리
```

### 3. Gap 분석

기존 Self-Healing 모듈로 해결할 수 없는 영역을 식별합니다:

```python
gap_output = """
ERROR: race condition detected in concurrent order processing
FAILED: saga orchestration failure - compensation stuck
WARNING: queue full - producer faster than consumer
"""

result = analyzer.detect_gaps(result, gap_output)

print(result.gaps)
```

**출력:**
```
[distributed_lock] 분산 락 메커니즘이 필요할 수 있음 (Redis/Zookeeper 기반)
[saga_pattern] Saga 패턴을 통한 분산 트랜잭션 관리가 필요할 수 있음
[backpressure] 백프레셔 메커니즘이 필요할 수 있음
```

### 4. 최적 DNA 자동 생성

분석 결과를 바탕으로 최적의 STAGE_DNA를 생성합니다:

```python
optimal_dna = analyzer.generate_optimal_dna(result)

print(optimal_dna)
```

**출력:**
```python
STAGE_DNA = {
    "name": "stage14_dlq_api_test.py",
    "type": "integration",
    "required_modules": ["dlq", "observability"],
    "optional_modules": [],
    "_generated_by": "DNAAnalyzer",
    "_analysis_confidence": 0.85
}
```

---

## 🔧 CLI 사용법

### 전체 Stage 분석

```bash
cd load_tests/utils/selfhealing
python dna_analyzer.py
```

### 특정 디렉토리 분석

```python
import os
from load_tests.utils.selfhealing.dna_analyzer import DNAAnalyzer

analyzer = DNAAnalyzer()

for f in os.listdir('load_tests/scenarios/chaos'):
    if f.startswith('stage') and f.endswith('.py'):
        result = analyzer.analyze_stage_file(f'load_tests/scenarios/chaos/{f}')
        print(result.get_summary())
        print()
```

---

## 📊 분석 결과 해석

### 추천 타입

| 아이콘 | 타입 | 의미 |
|--------|------|------|
| ➕ | ADD | 새로운 모듈 추가 권장 |
| ➖ | REMOVE | 불필요한 모듈 제거 권장 |
| 🆕 | NEW_FEATURE | 새 기능 개발 필요 |
| ⬆️ | UPGRADE | 기존 모듈 업그레이드 필요 |

### 우선순위

| 우선순위 | 의미 |
|----------|------|
| 🔴 HIGH | 즉시 조치 필요 |
| 🟡 MEDIUM | 권장 조치 |
| 🟢 LOW | 선택적 개선 |

---

## 🛡️ Fallback 및 에러 처리

DNAAnalyzer는 다음 상황에서 **Graceful Degradation**을 제공합니다:

### 1. 파일을 찾을 수 없는 경우

```python
result = analyzer.analyze_stage_file('nonexistent.py')
# → 빈 결과 반환, 에러 로깅
```

### 2. 구문 분석 실패

```python
# 파이썬 문법 오류가 있는 파일
result = analyzer.analyze_stage_file('broken_syntax.py')
# → 부분 분석 결과 반환 (정규식 기반 분석 fallback)
```

### 3. 분석기 완전 실패 시

수동 DNA 선언으로 fallback:

```python
# 자동 분석 실패 시 수동 선언 사용
try:
    result = analyzer.analyze_stage_file(file_path)
    if not result.detected_modules:
        # 수동 선언된 DNA 사용
        from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
        validate_stage_dna(STAGE_DNA)
except Exception:
    # 완전 실패 시 수동 DNA 사용
    pass
```

---

## 📈 통합 워크플로우

### CI/CD 파이프라인 통합

```yaml
# .github/workflows/dna-analysis.yml
name: Stage DNA Analysis

on:
  pull_request:
    paths:
      - 'load_tests/scenarios/**/*.py'

jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Analyze Stage DNA
        run: |
          python -c "
          from load_tests.utils.selfhealing.dna_analyzer import DNAAnalyzer
          import os
          
          analyzer = DNAAnalyzer()
          issues = []
          
          for root, dirs, files in os.walk('load_tests/scenarios'):
              for f in files:
                  if f.startswith('stage') and f.endswith('.py'):
                      result = analyzer.analyze_stage_file(os.path.join(root, f))
                      if result.over_engineered or result.under_declared:
                          issues.append(result.get_summary())
          
          if issues:
              print('⚠️ DNA 이슈 발견:')
              for issue in issues:
                  print(issue)
              exit(1)
          else:
              print('✅ 모든 Stage DNA가 정상입니다.')
          "
```

### Pre-commit Hook

```bash
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: dna-check
        name: Stage DNA Analysis
        entry: python -c "from load_tests.utils.selfhealing.dna_analyzer import DNAAnalyzer; ..."
        language: python
        files: ^load_tests/scenarios/.*\.py$
```

---

## 🔄 수동 선언과의 관계

| 상황 | 권장 접근 |
|------|----------|
| 새 Stage 작성 시 | DNAAnalyzer로 분석 후 추천 DNA 사용 |
| 기존 Stage 검증 | DNAAnalyzer로 오버엔지니어링 확인 |
| 자동 분석 실패 시 | 수동 DNA 선언 (34번 문서 참조) |
| 특수한 요구사항 | 수동 선언 + 자동 분석 병행 |

---

## 📚 관련 문서

- [29_STAGE_DNA_EVOLUTION_MASTER.md](./29_STAGE_DNA_EVOLUTION_MASTER.md) - Stage DNA 진화 마스터 계획
- [34_STAGE_DNA_USAGE_GUIDE.md](./34_STAGE_DNA_USAGE_GUIDE.md) - 수동 DNA 선언 레퍼런스
- [SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md) - 시스템 아키텍처

---

## 🛠️ 개발자 정보

**파일 위치**: `load_tests/utils/selfhealing/dna_analyzer.py`

**주요 클래스**:
- `DNAAnalyzer` - 메인 분석기
- `AnalysisResult` - 분석 결과 데이터 클래스
- `Recommendation` - 추천사항 데이터 클래스
- `ModuleUsage` - 모듈 사용 증거 데이터 클래스

**확장 방법**:
- 새 모듈 시그니처: `MODULE_SIGNATURES`에 추가
- 새 에러 패턴: `RESULT_TO_MODULE_RECOMMENDATIONS`에 추가
- 새 Gap 패턴: `detect_gaps()` 메서드의 `gap_patterns`에 추가

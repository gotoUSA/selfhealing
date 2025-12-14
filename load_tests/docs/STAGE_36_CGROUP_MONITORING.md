# Stage 36: cgroup 기반 메모리 모니터링

## 📋 개요

Stage 36에서 발견한 핵심 문제와 해결책을 문서화합니다.

## 🔍 문제 발견

### 기존 접근법의 한계

```python
# 기존 코드 (문제)
def get_memory_percent():
    current_mb = psutil.Process().memory_info().rss  # 단일 워커 RSS
    container_limit_mb = 512  # 컨테이너 제한
    return current_mb / container_limit_mb  # → 22% (실제로는 100%!)
```

| 관측 주체 | 관측 값 | 실제 값 |
|-----------|---------|---------|
| psutil (단일 워커) | 112MB (22%) | - |
| cgroup (컨테이너) | - | 512MB (100%) |

**문제**: 4개 워커 환경에서 psutil은 자신의 RSS만 봄
- 워커 1: 112MB (22%)
- 워커 2: 112MB (22%)
- 워커 3: 112MB (22%)
- 워커 4: 112MB (22%)
- **실제 컨테이너**: 512MB (100%) → OOM Kill!

### 결과

```
App says: "Memory is 22%, all good!"
Kernel: *SIGKILL* (OOM)
User: "Error 0 - Connection reset"
```

## ✅ 해결책: 업계 표준 4종 세트

### 1순위: cgroup 메모리 (SSoT)

```python
class CgroupMemoryMonitor:
    CGROUP_V2_CURRENT = "/sys/fs/cgroup/memory.current"
    CGROUP_V2_MAX = "/sys/fs/cgroup/memory.max"
    
    @classmethod
    def get_memory_percent(cls) -> float:
        current = cls.get_current_bytes()  # 컨테이너 전체
        max_bytes = cls.get_max_bytes()
        return current / max_bytes  # → 100% (정확!)
```

### 2순위: per-process RSS (원인 분해용)

```python
# cgroup이 95%인데...
# - 워커 RSS 합이 50%면 → 페이지 캐시/공유 메모리 의심
# - 워커 RSS 합이 95%면 → 앱 경로 문제
```

### 3순위: OOM/Pressure 조기 경보

```python
# memory.events
oom: 0       # OOM 발생 횟수
oom_kill: 0  # 실제 kill 횟수

# PSI (Pressure Stall Information)
some.avg10: 0.0  # 경미한 압박
full.avg10: 0.0  # 심각한 압박
```

### 4순위: 런타임 지표

- gunicorn worker exits / timeouts
- open files / sockets
- 429/503 비율 변화

## 📊 테스트 결과

### 시나리오별 결과

| 시나리오 | 429 | 503 | 설명 |
|----------|-----|-----|------|
| **공격적 부하** (Locust 동시 요청) | 0 | 817 | 61% → 100% 한 번에 점프, throttle 구간 스킵 |
| **점진적 부하** (2MB씩 순차 할당) | **18** | 0 | 83% → 90% 구간에서 429 정상 발생 |

### 점진적 부하 테스트 상세

```
[31] HTTP 200 - 86.8% (throttled)
[32] HTTP 200 - 87.2% (throttled)
[33] HTTP 429 - 87.2% (throttled)  ← 첫 429 발생
[34] HTTP 200 - 87.6% (throttled)
[35] HTTP 200 - 88.0% (throttled)
[36] HTTP 429 - 88.1% (throttled)
...
[48-50] HTTP 429 - 89.2% (throttled)

=== GRADUAL LOAD TEST RESULTS ===
429 (throttle): 18         ← 정상 발생!
503 (critical): 0
Throttle state seen: 26    ← throttle 상태 26회 감지
Probability reject: 18     ← 30% 확률 기반 거부 작동
```

### 왜 공격적 부하에서 429가 0인가?

```
Throttle 구간: 83% ~ 90% = 36MB (512MB의 7%)
Locust 동시 요청: 10개 × 각 워커 메모리 사용
→ 한 사이클에 61% → 100% 점프
→ 36MB 구간을 "스킵"
```

**이것은 버그가 아님**: 
- 공격적 부하에서 throttle 스킵은 **예상된 동작**
- 503(critical)이 **최후의 방어선**으로 정상 작동

### 기존 vs 개선 비교

| 지표 | 값 |
|------|-----|
| 관측 메모리 | 22% |
| OOM Kill | 다수 발생 |
| 429/503 반환 | 0회 |
| Error 0 | 발생 |

### 개선 (cgroup SSoT)

| 지표 | 값 |
|------|-----|
| 관측 메모리 | 96% |
| OOM Kill | **0회** |
| 429/503 반환 | **817회** |
| Error 0 | 없음 |

## 🔧 구현 세부사항

### API 응답 예시

```json
{
  "current_mb": 492.7,
  "percent": 96.2,
  "state": "critical",
  
  "cgroup": {
    "current_mb": 492.7,
    "limit_mb": 512.0,
    "percent": 96.2,
    "version": 2
  },
  
  "process": {
    "rss_mb": 112.9,
    "rss_percent_of_limit": 22.1,
    "note": "Single worker RSS. Use to analyze 'who is eating memory'"
  },
  
  "pressure": {
    "oom_events": {"oom": 0, "oom_kill": 0},
    "psi": {"some": {"avg10": 0.0}, "full": {"avg10": 0.0}}
  }
}
```

### 백프레셔 동작

```
Memory < 60%  → normal (요청 처리)
Memory 60-83% → warning (경고 로그)
Memory 83-90% → throttled (429 반환)
Memory > 90%  → critical (503 반환)
```

## 📚 업계 사례

| 회사 | 접근법 |
|------|--------|
| Netflix | cgroup 기반 메모리 제한 + 외부 rate limiting |
| Google | Borg의 cgroup 모니터링 |
| Uber | PSI 기반 조기 경보 |
| AWS | ECS Task 레벨 메모리 모니터링 |

## 🎯 핵심 교훈

> **"모니터링의 기준 값은 무조건 cgroup으로 잡아야 함.
> psutil은 보조 지표로만 써."**

### 잘못된 접근

```
앱 레벨 메모리 모니터링 → OOM 막을 수 없음
```

### 올바른 접근

```
cgroup 레벨 메모리 모니터링 → OOM 전에 백프레셔 가능
```

## 🔗 관련 파일

- [memory_test_views.py](../shopping/views/memory_test_views.py) - CgroupMemoryMonitor 클래스
- [docker-compose.stage36-real.yml](../docker-compose.stage36-real.yml) - 테스트 환경

## 📅 변경 이력

| 날짜 | 변경 |
|------|------|
| 2025-12-13 | psutil → cgroup SSoT 전환 |
| 2025-12-13 | 4종 세트 모니터링 구현 |
| 2025-12-13 | OOM Kill 0회 달성 |

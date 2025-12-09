# Self-Healing 부하 테스트 계획

> Self-Healing 시스템은 "에러 없이 정상 동작하는지" 테스트하는 게 아니라
> "에러가 날 때는 항상 올바르게 종결되는지" 테스트하는 시스템이다.

---

## 📋 목차

1. [현재 상태 분석](#현재-상태-분석)
2. [부족한 시나리오](#부족한-시나리오)
3. [신규 테스트 계획](#신규-테스트-계획)
4. [구현 우선순위](#구현-우선순위)
5. [검증 항목 체크리스트](#검증-항목-체크리스트)

---

## 현재 상태 분석

### ✅ 갖춘 테스트

| Stage | 테스트 | Self-Healing 관점 |
|-------|--------|------------------|
| 2 | Idempotency | idempotent key 검증 ✓ |
| 3 | Latency | 지연 시 retry 발동 ✓ |
| 5 | Rollback | 실패 시 재고 복구 ✓ |
| 6 | Chaos | 랜덤 장애 복원력 ✓ |
| 9 | Soak | 메모리/리소스 누수 ✓ |
| 10 | Self-Healing | Control API 기능 ✓ |

### ❌ 부족한 시나리오

| 패턴 | 목적 | 현재 상태 |
|------|------|----------|
| Ramp-up | 임계점 탐색 | ❌ 없음 |
| Spike → Recovery | 회복 안정성 | ❌ 없음 |
| Spike 반복 | backoff 튜닝 | ❌ 없음 |
| DLQ 검증 | 재처리 정합성 | ❌ 없음 |
| Circuit Breaker 상태 전이 | 자동 open/close | ❌ 없음 |

---

## 부족한 시나리오

### 1. Ramp-up (점진적 증가)

```
목적: 임계점 탐색 - 언제부터 retry가 발동되는가?

부하 패턴:
  0분: 10 users
  5분: 50 users
  10분: 100 users
  15분: 200 users
  20분: 300 users

관찰 항목:
  - retry 발생 시점
  - 응답 시간 증가 추이
  - 에러율 변화
  - Circuit Breaker 상태 변화
```

### 2. Spike (급격한 폭발)

```
목적: Circuit Breaker가 제때 열리는가?

부하 패턴:
  0초: 0 users
  5초: 500 users (즉시 투입)
  유지: 2분
  
관찰 항목:
  - Circuit Breaker 열림 타이밍
  - 열린 후 요청 차단 비율
  - DLQ 적재 건수
```

### 3. Spike → Recovery (회복 테스트)

```
목적: 폭발 후 정상화 - reset이 올바른가?

부하 패턴:
  Phase 1 (Spike): 0→500 users, 2분
  Phase 2 (Cooldown): 500→50 users, 3분
  Phase 3 (Normal): 50 users 유지, 5분

관찰 항목:
  - Circuit Breaker 닫힘 타이밍
  - DLQ 재처리 시작 시점
  - 재처리 후 데이터 정합성
  - 응답 시간 정상화 시간
```

### 4. Spike 반복 (Backoff 튜닝)

```
목적: 반복 장애 시 backoff가 과하지 않은가?

부하 패턴:
  Cycle 1: Spike(500) → Recovery(50) → 2분 대기
  Cycle 2: Spike(500) → Recovery(50) → 2분 대기
  Cycle 3: Spike(500) → Recovery(50) → 2분 대기

관찰 항목:
  - 각 사이클별 회복 시간 비교
  - exponential backoff 누적 여부
  - 3회차에서 backoff 과다 여부
```

### 5. DLQ 검증 (재처리 정합성)

```
목적: DLQ 없이 복구 가능한가? 재처리가 정확한가?

시나리오:
  1. 의도적으로 결제 실패 유발 (PG timeout 주입)
  2. DLQ 적재 확인
  3. PG 정상화
  4. DLQ replay 트리거
  5. 재처리 결과 검증

검증 항목:
  - 재처리 후 중복 결제 없음
  - idempotent key 정상 작동
  - 최종 데이터 정합성
```

### 6. Circuit Breaker 상태 전이 (자동 동작)

```
목적: 수동 Control이 아닌 자동 전이 검증

시나리오:
  Phase 1: 정상 트래픽 (closed 상태)
  Phase 2: 연속 실패 주입 → open 전이 확인
  Phase 3: recovery_timeout 대기 → half_open 전이
  Phase 4: 성공 요청 → closed 복귀

검증 항목:
  - 각 전이 타이밍
  - half_open에서 제한된 요청 허용
  - 자동 closed 복귀 조건
```

---

## 신규 테스트 계획

### Stage 11: Ramp-up 임계점 탐색

```python
# load_tests/scenarios/stage11_ramp_threshold.py

목적: 시스템 임계점과 Self-Healing 발동 시점 탐색

LoadShape:
  - LinearRamp: 10 → 300 users over 20분
  
수집 지표:
  - retry_count (분당)
  - circuit_breaker_state
  - dlq_count
  - avg_response_time (분당)
  - error_rate (분당)
  
출력:
  - 임계점 그래프 (users vs metrics)
  - retry 발동 시작 user 수
  - CB 전이 시작 user 수
```

### Stage 12: Spike & Recovery

```python
# load_tests/scenarios/stage12_spike_recovery.py

목적: 급격한 부하 후 회복 안정성 검증

LoadShape:
  Phase 1 (0-30s): 0 → 500 users (spike)
  Phase 2 (30s-2m30s): 500 users (sustain)
  Phase 3 (2m30s-5m30s): 500 → 50 users (ramp-down)
  Phase 4 (5m30s-10m30s): 50 users (stabilize)
  
수집 지표:
  - circuit_breaker_open_time
  - circuit_breaker_close_time
  - dlq_max_count
  - dlq_replay_success_rate
  - data_consistency (before vs after)
```

### Stage 13: Repeated Spike (Backoff 튜닝)

```python
# load_tests/scenarios/stage13_repeated_spike.py

목적: 반복 장애 시 backoff 과다 여부 검증

LoadShape:
  3 cycles of:
    Spike: 0 → 500 users (30s)
    Sustain: 500 users (1m)
    Recovery: 500 → 50 users (1m)
    Cool: 50 users (2m)
  
수집 지표:
  - recovery_time_per_cycle
  - backoff_duration_cumulative
  - circuit_breaker_transitions
  - final_state (정상화 확인)
```

### Stage 14: DLQ Replay 검증

```python
# load_tests/scenarios/stage14_dlq_replay.py

목적: DLQ 재처리 정확성 및 데이터 정합성

시나리오:
  Step 1: 결제 시도 100건 (50% 강제 실패)
  Step 2: DLQ 적재 확인 (50건 예상)
  Step 3: 강제 실패 해제
  Step 4: DLQ replay 트리거
  Step 5: 결과 검증
  
검증:
  - dlq_replayed == dlq_inserted
  - duplicate_payment == 0
  - idempotent_key_violations == 0
  - stock_after == stock_expected
  - point_after == point_expected
```

### Stage 15: Circuit Breaker 자동 전이

```python
# load_tests/scenarios/stage15_cb_transitions.py

목적: Circuit Breaker 자동 상태 전이 검증

시나리오:
  Phase 1: 정상 요청 → closed 확인
  Phase 2: 연속 5회 실패 주입 → open 전이 확인
  Phase 3: recovery_timeout(60s) 대기 → half_open 전이
  Phase 4: 성공 2회 → closed 복귀
  
검증:
  - 각 전이 정확성
  - 전이 타이밍 (설정값 준수)
  - half_open 요청 제한
  - 전이 로그/audit 기록
```

---

## 구현 우선순위

### Phase 1: 핵심 (1주차)

| 순위 | Stage | 사유 |
|------|-------|------|
| 1 | Stage 12 | Spike & Recovery - 가장 기본적인 Self-Healing 검증 |
| 2 | Stage 15 | CB 자동 전이 - Self-Healing 핵심 동작 |
| 3 | Stage 14 | DLQ Replay - 데이터 정합성 최우선 |

### Phase 2: 고급 (2주차)

| 순위 | Stage | 사유 |
|------|-------|------|
| 4 | Stage 11 | Ramp-up - 운영 임계점 파악 |
| 5 | Stage 13 | Repeated Spike - backoff 튜닝 |

### Phase 3: config.yaml 업데이트

```yaml
# 신규 프로파일 추가
profiles:
  self_healing:
    stages:
      - stage10_self_healing
      - stage12_spike_recovery
      - stage14_dlq_replay
      - stage15_cb_transitions
    description: "Self-Healing 전용 검증 (약 30분)"

  self_healing_full:
    stages:
      - stage11_ramp_threshold
      - stage12_spike_recovery
      - stage13_repeated_spike
      - stage14_dlq_replay
      - stage15_cb_transitions
    description: "Self-Healing 전체 검증 (약 60분)"
```

---

## 검증 항목 체크리스트

### 📋 Self-Healing 테스트 시 반드시 확인할 항목

#### 1. Retry 메커니즘
- [ ] retry 발생 횟수 기록
- [ ] retry 간격 (exponential backoff) 확인
- [ ] max_retry 초과 시 DLQ 적재

#### 2. DLQ (Dead Letter Queue)
- [ ] DLQ 적재 건수
- [ ] DLQ replay 성공률
- [ ] replay 후 중복 처리 없음
- [ ] idempotent key 정상 작동

#### 3. Circuit Breaker
- [ ] open 전이 타이밍 (failure_threshold)
- [ ] half_open 전이 타이밍 (recovery_timeout)
- [ ] closed 복귀 조건 (success_threshold)
- [ ] 수동 override 작동

#### 4. 데이터 정합성
- [ ] 재고 before == 재고 after (실패 시)
- [ ] 포인트 before == 포인트 after (실패 시)
- [ ] 중복 결제 0건
- [ ] 주문 상태 일관성

#### 5. 모니터링/로그
- [ ] 상태 전이 audit log
- [ ] 알림 발송 여부
- [ ] 메트릭 수집 정상

#### 6. 회복 안정성
- [ ] 장애 후 정상화 시간
- [ ] 반복 장애 시 누적 악화 없음
- [ ] 재부팅 후 이어서 복구

---

## 참고

### 업계 표준 Chaos Engineering

- [Netflix Chaos Monkey](https://netflix.github.io/chaosmonkey/)
- [Principles of Chaos Engineering](https://principlesofchaos.org/)
- [Gremlin Chaos Engineering](https://www.gremlin.com/)

### 핵심 원칙

> "Self-Healing 시스템은 기능 테스트 = pass/fail이 아니라
> 상황 반응 테스트 = 감지/조치/결과다."

- 폭탄 방식 하나로는 힐링 시스템의 본질을 검증할 수 없다
- 폭탄은 "죽냐 안 죽냐"만 본다
- Self-Healing은 "죽지 않게, 죽었어도 복구하게" 테스트하는 것

---

## 다음 단계

1. **Stage 12 구현** - Spike & Recovery 테스트
2. **Stage 15 구현** - Circuit Breaker 자동 전이 테스트
3. **Stage 14 구현** - DLQ Replay 검증 테스트
4. **config.yaml 업데이트** - 신규 프로파일 추가
5. **CI/CD 통합** - self_healing 프로파일 자동 실행

---

*작성일: 2025-12-09*
*작성: Self-Healing Load Test 분석*

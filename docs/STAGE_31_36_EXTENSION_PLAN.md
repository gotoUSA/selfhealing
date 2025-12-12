# Stage 31-36: 기존 Stage 확장 및 고급 Chaos 테스트 계획서

> 작성일: 2025-12-12
> 기반 문서: STAGE_28_30_ADVANCED_CHAOS_PLAN.md Phase 2 섹션
> 상태: 계획 단계

---

## 📋 개요

### 목적
STAGE_28_30_ADVANCED_CHAOS_PLAN.md에서 언급된 **기존 Stage 확장 항목**과 **추가 필요한 Chaos 시나리오**를 Stage 31-36으로 구현합니다.

### 근거
- Phase 2에서 기존 Stage 18, 22, 23, 16 확장 필요성 언급
- Gap 분석에서 누락된 프로덕션 시나리오 보완
- 복합 장애 상황에 대한 테스트 부족

---

## 🗂️ Stage 구성 계획

| Stage | 이름 | 원본 | 설명 |
|-------|------|------|------|
| **31** | Cascade Failure Extended | Stage 18 확장 | Redis→DB→CB 연쇄 장애 |
| **32** | Retry Storm Extended | Stage 22 확장 | 메모리 누수, DLQ 폭주 방지 |
| **33** | JWT Cascade Extended | Stage 23 확장 | JWT 만료 스탬피드 |
| **34** | DB Deadlock Locust | Stage 16 확장 | Locust 기반 Deadlock 테스트 |
| **35** | Cache Stampede | 신규 | 캐시 만료 시 Thundering Herd |
| **36** | Memory Pressure | 신규 | OOM 상황 안정성 테스트 |

---

## 🆕 Stage 31: Cascade Failure Extended

### 원본
Stage 18 (Chain Failure) 확장

### 목적
복합 연쇄 장애 시나리오에서 시스템 복원력 검증

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                  Cascade Failure Scenarios                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  시나리오 1: Redis Down → DB Surge → CB Malfunction         │
│                                                              │
│   Redis ──X──► Cache Miss ──► DB Query Surge                │
│                                    │                         │
│                                    ▼                         │
│                          Connection Pool 고갈                │
│                                    │                         │
│                                    ▼                         │
│                          CB가 DB를 장애로 오인               │
│                                    │                         │
│                                    ▼                         │
│                          정상 요청도 거부 (False Positive)   │
│                                                              │
│  시나리오 2: Payment API Down → Retry Storm → Rate Limit    │
│                                                              │
│   Toss API ──X──► Retry 폭주 ──► Rate Limit 도달            │
│                                       │                      │
│                                       ▼                      │
│                              Rate Limit 해제 대기            │
│                                       │                      │
│                                       ▼                      │
│                              추가 재시도 → Deadlock          │
│                                                              │
│  시나리오 3: Health Check Delay → Wrong Decision            │
│                                                              │
│   Health Check 5초 지연 ──► DOWN으로 판단                    │
│                                   │                          │
│                                   ▼                          │
│                            트래픽 차단 (실제는 정상)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-31-1: Redis Down → DB Surge → CB Malfunction
```python
"""
시나리오:
  1. Redis 연결 강제 종료
  2. 캐시 미스로 DB 직접 조회 폭주
  3. DB Connection Pool 고갈
  4. CB가 DB를 장애로 오인
  5. 정상 요청도 거부

검증:
  - CB False Positive 방지
  - DB Connection Pool 복구 시간
  - 캐시 복구 후 정상화 시간
"""
```

#### TC-31-2: Payment API Down → Retry Storm → Rate Limit Deadlock
```python
"""
시나리오:
  1. 토스 API 장애 주입
  2. 재시도 폭주 발생
  3. Rate Limit 도달
  4. Rate Limit 해제 대기 중 추가 재시도
  5. Deadlock 상태

검증:
  - Rate Limit 도달 시 재시도 중단
  - Deadlock 탐지 및 자동 해제
  - 결제 데이터 무결성
"""
```

#### TC-31-3: Health Check Delay → Wrong Decision
```python
"""
시나리오:
  1. Health Check 응답 5초 지연
  2. 서비스 DOWN으로 판단
  3. 트래픽 차단
  4. 실제로는 정상 (느린 것뿐)

검증:
  - Health Check timeout 적정값
  - 느린 응답 vs 장애 구분
  - False Positive 방지
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| CB False Positive | 0건 | 정상 서비스를 장애로 오인 X |
| 연쇄 장애 격리 | < 30초 | 한 컴포넌트 장애가 전체 영향 X |
| Rate Limit Deadlock | 0건 | Deadlock 발생 시 자동 해제 |
| Health Check 정확도 | > 95% | 실제 상태와 판단 일치 |

---

## 🆕 Stage 32: Retry Storm Extended

### 원본
Stage 22 (Rate Limit Conflict) 확장

### 목적
Retry Storm 상황에서 메모리 누수, DLQ 폭주, 중복 처리 방지

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                   Retry Storm Extended                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  검증 1: 메모리 누수 모니터링                                │
│                                                              │
│   Retry 객체 생성 ──► 백오프 대기 ──► GC 정상 동작?         │
│                           │                                  │
│                           ▼                                  │
│                    메모리 사용량 추적                        │
│                                                              │
│  검증 2: DLQ 폭주 방지                                       │
│                                                              │
│   실패 요청 ──► DLQ 입력 ──► Queue Size 제한                │
│                     │                                        │
│                     ▼                                        │
│              입력 속도 throttling                            │
│                                                              │
│  검증 3: Payment Webhook 반복 호출                          │
│                                                              │
│   Toss Webhook ──► 중복 호출 ──► Idempotency 보장           │
│                         │                                    │
│                         ▼                                    │
│                  중복 결제 방지                              │
│                                                              │
│  검증 4: Retry + Clock Skew 복합                            │
│                                                              │
│   Retry 중 ──► Timestamp 검증 ──► Clock Skew 발생           │
│                       │                                      │
│                       ▼                                      │
│              시간 기반 검증 실패 처리                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-32-1: Memory Leak During Backoff
```python
"""
시나리오:
  - 1000개 요청 동시 실패
  - Exponential backoff 대기 중
  - 메모리 사용량 모니터링

검증:
  - Retry 객체 GC 정상 동작
  - 메모리 사용량 안정 (피크 < 500MB)
  - 메모리 누수 없음
"""
```

#### TC-32-2: DLQ Not Exploding
```python
"""
시나리오:
  - 초당 1000건 실패 발생
  - DLQ 입력 속도 모니터링

검증:
  - DLQ 입력 속도 제한 (max 500/sec)
  - Queue size 경고 알림
  - 메모리 OOM 방지
"""
```

#### TC-32-3: Payment Webhook Replay Safety
```python
"""
시나리오:
  - Toss webhook 동일 내용 3회 호출
  - 재시도 상황 시뮬레이션

검증:
  - Idempotency key 검증
  - 중복 결제 0건
  - 중복 재고 차감 0건
"""
```

#### TC-32-4: Retry Timestamp Skew Compound
```python
"""
시나리오:
  - 재시도 중 서버 시계 +5분 drift
  - Timestamp 기반 검증 수행

검증:
  - Clock skew 허용 범위 내 처리
  - 범위 초과 시 명확한 에러
  - 데이터 무결성 유지
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| 메모리 피크 | < 500MB | Retry 중 메모리 안정 |
| DLQ 입력 속도 | < 500/sec | 폭주 방지 throttling |
| 중복 결제 | 0건 | Idempotency 완벽 보장 |
| Clock Skew 허용 | ±30초 | 합리적 오차 범위 |

---

## 🆕 Stage 33: JWT Cascade Extended

### 원본
Stage 23 (Clock Skew) 확장

### 목적
JWT 만료 관련 대규모 재인증 요청 상황 테스트

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                     JWT Cascade Scenarios                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  시나리오 1: JWT Expiry Stampede                            │
│                                                              │
│   1000명 동시 접속 ──► JWT 동시 발급                        │
│                             │                                │
│                             ▼                                │
│                    JWT 동시 만료 (1시간 후)                  │
│                             │                                │
│                             ▼                                │
│                    1000개 재발급 요청 동시                   │
│                             │                                │
│                             ▼                                │
│                    인증 서버 과부하                          │
│                                                              │
│  시나리오 2: Auth Server Fallback                           │
│                                                              │
│   Primary Auth ──X──► Secondary Auth로 전환                 │
│                             │                                │
│                             ▼                                │
│                    세션 유지 + 토큰 유효성                   │
│                                                              │
│  시나리오 3: Re-Auth Storm                                  │
│                                                              │
│   대량 재인증 요청 ──► Throttling 발동                      │
│                             │                                │
│                             ▼                                │
│                    우선순위 기반 처리                        │
│                                                              │
│  시나리오 4: Replay Queue Growth                            │
│                                                              │
│   인증 실패 요청 ──► 대기열 적재 ──► 큐 사이즈 관리        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-33-1: JWT Expiry Stampede
```python
"""
시나리오:
  1. 1000명 사용자 동시 로그인 (같은 시간에 JWT 발급)
  2. 1시간 후 JWT 일괄 만료
  3. 1000개 재발급 요청 동시 발생
  4. 인증 서버 과부하

검증:
  - 재발급 요청 throttling
  - 인증 서버 응답 시간 < 2초
  - 사용자 경험 저하 최소화
"""
```

#### TC-33-2: Auth Server Fallback
```python
"""
시나리오:
  - Primary 인증 서버 장애
  - Secondary로 자동 전환

검증:
  - Fallback 시간 < 5초
  - 기존 세션 유지
  - 토큰 유효성 검증 정상
"""
```

#### TC-33-3: Re-Auth Storm Throttling
```python
"""
시나리오:
  - 초당 500개 재인증 요청
  - 시스템 한계 도달

검증:
  - Throttling 발동 (max 100/sec)
  - 우선순위 기반 처리
  - 대기 중인 요청 상태 안내
"""
```

#### TC-33-4: Replay Queue Growth
```python
"""
시나리오:
  - 인증 실패 요청 대기열 적재
  - 큐 사이즈 급증

검증:
  - 큐 사이즈 제한 (max 10,000)
  - 오래된 요청 만료 처리
  - 메모리 안정성
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| 재발급 응답시간 | < 2초 | 사용자 경험 유지 |
| Fallback 시간 | < 5초 | 인증 서버 전환 |
| Throttle 한계 | 100/sec | 재인증 요청 제한 |
| 큐 사이즈 | < 10,000 | 대기열 메모리 관리 |

---

## 🆕 Stage 34: DB Deadlock Locust

### 원본
Stage 16 (Database Pool Exhaustion) 확장

### 목적
Locust 기반 대규모 동시 트랜잭션 Deadlock 테스트

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                    DB Deadlock Scenarios                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  시나리오 1: 동시 주문 + 재고 차감                          │
│                                                              │
│   User A ──► Order #1 ──► 재고 Lock (Product 1)            │
│   User B ──► Order #2 ──► 재고 Lock (Product 2)            │
│                                                              │
│   User A ──► 재고 Lock (Product 2) ──► 대기                │
│   User B ──► 재고 Lock (Product 1) ──► 대기                │
│                                                              │
│   → Deadlock!                                                │
│                                                              │
│  시나리오 2: 결제 + 포인트 동시 갱신                        │
│                                                              │
│   Payment Task ──► User Balance Lock                        │
│   Point Task ──► User Point Lock                            │
│                                                              │
│   서로 다른 순서로 Lock 획득 시도                           │
│                                                              │
│   → Deadlock!                                                │
│                                                              │
│  시나리오 3: Connection Pool 고갈 + Deadlock                │
│                                                              │
│   100개 Connection 사용 중                                   │
│   Deadlock으로 Connection 해제 지연                         │
│   신규 요청 Connection 획득 실패                             │
│                                                              │
│   → Pool Exhaustion + Deadlock 복합 장애                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-34-1: Concurrent Order Deadlock
```python
"""
시나리오:
  - 100명 동시 주문
  - 동일 상품 재고 차감 경쟁

검증:
  - Deadlock 탐지 및 자동 재시도
  - 재고 일관성 100%
  - 주문 성공률 > 95%
"""
```

#### TC-34-2: Payment + Point Deadlock
```python
"""
시나리오:
  - 결제 처리 중 포인트 적립 동시 실행
  - Lock 순서 충돌

검증:
  - Deadlock 해소 시간 < 5초
  - 금액 일관성 100%
  - 트랜잭션 롤백 정상
"""
```

#### TC-34-3: Pool Exhaustion + Deadlock Compound
```python
"""
시나리오:
  - 100개 Connection 모두 사용 중
  - Deadlock으로 일부 Connection 해제 지연
  - 신규 요청 대기

검증:
  - Connection timeout 적정 설정
  - Deadlock 해소 후 Pool 복구
  - 신규 요청 처리 재개
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| Deadlock 탐지 | < 3초 | 자동 탐지 및 알림 |
| 자동 재시도 성공률 | > 95% | Deadlock 후 재시도 |
| 데이터 일관성 | 100% | 재고/금액 무결성 |
| Pool 복구 시간 | < 10초 | Deadlock 해소 후 |

---

## 🆕 Stage 35: Cache Stampede

### 원본
신규 (Gap 분석에서 식별)

### 목적
대규모 캐시 만료 시 Thundering Herd 방지

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                    Cache Stampede Scenarios                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  정상 상태:                                                  │
│                                                              │
│   Request ──► Cache Hit ──► Response (1ms)                  │
│                                                              │
│  캐시 만료 시:                                               │
│                                                              │
│   Request 1 ──┐                                              │
│   Request 2 ──┤                                              │
│   Request 3 ──┼──► Cache Miss ──► DB Query 동시 실행        │
│   Request 4 ──┤                         │                    │
│   Request 5 ──┘                         ▼                    │
│                                    DB 과부하!                │
│                                                              │
│  해결책:                                                     │
│                                                              │
│   Request 1 ──► Cache Miss ──► DB Query (Lock 획득)         │
│   Request 2 ──► Cache Miss ──► Lock 대기                    │
│   Request 3 ──► Cache Miss ──► Lock 대기                    │
│                                                              │
│   DB Query 완료 ──► Cache 갱신 ──► 대기 요청 Cache Hit     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-35-1: Hot Key Expiry Stampede
```python
"""
시나리오:
  - 인기 상품 캐시 만료
  - 1000개 요청 동시 도착

검증:
  - DB 쿼리 1회만 실행
  - 나머지 요청은 캐시 재생성 대기
  - 응답 시간 < 500ms
"""
```

#### TC-35-2: Probabilistic Early Expiration
```python
"""
시나리오:
  - 캐시 만료 전 확률적 갱신
  - Background refresh

검증:
  - 만료 전 갱신 성공률 > 80%
  - Stampede 발생 0건
  - 캐시 freshness 유지
"""
```

#### TC-35-3: Multi-Key Batch Expiry
```python
"""
시나리오:
  - 100개 캐시 키 동시 만료
  - 각 키에 100개 요청

검증:
  - 키당 DB 쿼리 1회
  - 총 DB 쿼리 100회 이하
  - 시스템 안정성 유지
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| DB 쿼리 중복 | 0건 | 동일 키에 대해 1회만 |
| 응답 시간 | < 500ms | 대기 포함 |
| Stampede 발생 | 0건 | 완벽 방지 |
| 캐시 갱신 성공률 | 100% | 모든 대기 요청 처리 |

---

## 🆕 Stage 36: Memory Pressure

### 원본
신규 (Gap 분석에서 식별)

### 목적
메모리 압박 상황에서 시스템 안정성 테스트

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                   Memory Pressure Scenarios                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  시나리오 1: 점진적 메모리 증가                              │
│                                                              │
│   0% ──► 50% ──► 70% ──► 85% ──► 95% ──► OOM 위험          │
│                              │                               │
│                              ▼                               │
│                    경고 알림 + Throttling                    │
│                                                              │
│  시나리오 2: 대용량 응답 처리                                │
│                                                              │
│   큰 JSON 응답 (50MB) ──► 메모리 할당                       │
│                              │                               │
│                              ▼                               │
│                    Streaming 처리 필요                       │
│                                                              │
│  시나리오 3: 메모리 누수 시뮬레이션                          │
│                                                              │
│   Request 처리 ──► 객체 미해제 ──► 메모리 증가              │
│                                        │                     │
│                                        ▼                     │
│                               GC 강제 실행 필요              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-36-1: Gradual Memory Increase
```python
"""
시나리오:
  - 메모리 사용량 50% → 95% 점진 증가
  - 각 단계별 시스템 반응

검증:
  - 70% 경고 알림
  - 85% throttling 발동
  - 95% 신규 요청 거부 (graceful)
  - OOM 발생 0건
"""
```

#### TC-36-2: Large Payload Handling
```python
"""
시나리오:
  - 50MB JSON 응답 생성
  - 동시 10개 요청

검증:
  - Streaming 응답 사용
  - 메모리 피크 < 1GB
  - 응답 완료율 100%
"""
```

#### TC-36-3: Memory Leak Detection
```python
"""
시나리오:
  - 의도적 메모리 누수 주입
  - 1000 요청 후 메모리 상태

검증:
  - 누수 탐지 알림
  - GC 후 메모리 회수
  - 누수 위치 로깅
"""
```

#### TC-36-4: GC Pause Impact
```python
"""
시나리오:
  - Full GC 발생 시뮬레이션
  - GC 중 요청 처리

검증:
  - GC pause < 100ms
  - 요청 timeout 0건
  - 응답 시간 영향 최소화
"""
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| OOM 발생 | 0건 | 완벽 방지 |
| 메모리 경고 | 70%에서 | 조기 알림 |
| Throttling 발동 | 85%에서 | 과부하 방지 |
| GC pause | < 100ms | 응답 영향 최소화 |

---

## 📊 실행 계획

### Phase 1: Stage 31-34 (기존 확장) - 4일

| 일차 | 작업 | 파일 |
|------|------|------|
| Day 1 | Stage 31 Cascade Failure Extended | stage31_cascade_extended.py |
| Day 2 | Stage 32 Retry Storm Extended | stage32_retry_storm_extended.py |
| Day 3 | Stage 33 JWT Cascade Extended | stage33_jwt_cascade.py |
| Day 4 | Stage 34 DB Deadlock Locust | stage34_db_deadlock.py |

### Phase 2: Stage 35-36 (신규) - 2일

| 일차 | 작업 | 파일 |
|------|------|------|
| Day 5 | Stage 35 Cache Stampede | stage35_cache_stampede.py |
| Day 6 | Stage 36 Memory Pressure | stage36_memory_pressure.py |

### Phase 3: 통합 및 Docker - 1일

| 작업 | 설명 |
|------|------|
| 통합 테스트 | 모든 Stage 연동 테스트 |
| Docker Compose | stage31-36.yml 생성 |
| 문서화 | 완료 기준 업데이트 |

---

## 🗂️ 파일 구조

```
load_tests/
├── scenarios/
│   ├── stage31_cascade_extended.py      # Stage 18 확장
│   ├── stage32_retry_storm_extended.py  # Stage 22 확장
│   ├── stage33_jwt_cascade.py           # Stage 23 확장
│   ├── stage34_db_deadlock.py           # Stage 16 확장
│   ├── stage35_cache_stampede.py        # 신규
│   └── stage36_memory_pressure.py       # 신규

tests/
├── load/
│   ├── test_stage31_cascade_extended.py
│   ├── test_stage32_retry_storm_extended.py
│   ├── test_stage33_jwt_cascade.py
│   ├── test_stage34_db_deadlock.py
│   ├── test_stage35_cache_stampede.py
│   └── test_stage36_memory_pressure.py

docker/
├── docker-compose.stage31.yml
├── docker-compose.stage32.yml
├── docker-compose.stage33.yml
├── docker-compose.stage34.yml
├── docker-compose.stage35.yml
└── docker-compose.stage36.yml
```

---

## ✅ 완료 기준

### Stage 31 (Cascade Failure Extended)
- [ ] 3개 시나리오 구현
- [ ] CB False Positive 방지 검증
- [ ] 연쇄 장애 격리 테스트
- [ ] 테스트 통과

### Stage 32 (Retry Storm Extended)
- [ ] 4개 검증 항목 구현
- [ ] 메모리 누수 방지 검증
- [ ] DLQ 폭주 방지 테스트
- [ ] 테스트 통과

### Stage 33 (JWT Cascade Extended)
- [ ] 4개 시나리오 구현
- [ ] JWT Stampede 방지
- [ ] Auth Server Fallback 검증
- [ ] 테스트 통과

### Stage 34 (DB Deadlock Locust)
- [ ] 3개 시나리오 구현
- [ ] Deadlock 자동 탐지
- [ ] Pool 복구 검증
- [ ] 테스트 통과

### Stage 35 (Cache Stampede)
- [ ] 3개 시나리오 구현
- [ ] Thundering Herd 방지
- [ ] Probabilistic Refresh 검증
- [ ] 테스트 통과

### Stage 36 (Memory Pressure)
- [ ] 4개 시나리오 구현
- [ ] OOM 방지 검증
- [ ] GC 영향 최소화
- [ ] 테스트 통과

---

## 📚 참고 자료

- [STAGE_28_30_ADVANCED_CHAOS_PLAN.md](./STAGE_28_30_ADVANCED_CHAOS_PLAN.md) - 원본 문서
- Stage 16, 18, 22, 23 기존 구현 코드
- [Redis Cache Stampede Prevention](https://redis.io/docs/manual/patterns/cache-stampede/)
- [Python Memory Profiling](https://pympler.readthedocs.io/)

---

## 📝 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 1.0 | 2025-12-12 | 초안 작성 |

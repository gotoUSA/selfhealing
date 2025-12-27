# Stage 10 Extreme V2: 통합 극한 테스트 구현 계획

> **문서 버전**: v1.0.0
> **작성일**: 2025-12-27
> **상태**: 📋 Planning
> **대상 파일**: `load_tests/scenarios/integration/stage10_self_healing.py`

---

## 📋 개요

### 목표

**단일 Stage 10 파일**에서 모든 극한 시나리오를 통합 구현하여,
Self-Healing 시스템의 **Tier-1 수준 복원력**을 검증합니다.

### 왜 한 스테이지에서?

| 분리 접근 | 통합 접근 |
|-----------|-----------|
| 시나리오별 격리 테스트 | **실제 장애는 동시다발적** |
| 개별 메트릭 수집 | **복합 장애 상호작용 검증** |
| 순차적 실행 | **동시 실행으로 진짜 스트레스** |

> **결론**: 실제 프로덕션 장애는 한 번에 여러 문제가 터집니다.
> 통합 테스트가 더 현실적입니다.

---

## 🎯 구현 범위

### Phase 1: SLA 강화 (1일)

```
┌─────────────────────────────────────────────────────────────┐
│  SLA TIER SYSTEM                                             │
├─────────────────────────────────────────────────────────────┤
│  🥉 Bronze   : 2000ms (현재 기본값)                          │
│  🥈 Silver   : 500ms                                         │
│  🥇 Gold     : 250ms                                         │
│  💎 Platinum : 100ms                                         │
├─────────────────────────────────────────────────────────────┤
│  측정 기준                                                   │
│  ├── P50 (Median): 일반 사용자 경험                         │
│  ├── P95: 대부분의 요청                                     │
│  ├── P99: SLA 기준 ⭐                                        │
│  └── P99.9: 극단적 outlier                                  │
└─────────────────────────────────────────────────────────────┘
```

#### 구현 항목

| 항목 | 설명 | 우선순위 |
|------|------|----------|
| `SLATier` Enum | Bronze/Silver/Gold/Platinum 정의 | P0 |
| `PercentileCalculator` | P50/P95/P99/P99.9 계산기 | P0 |
| SLA 위반 감지 | P99 기준 초과 시 FAILED 판정 | P0 |
| 동적 SLA 조정 | 환경변수로 SLA 레벨 선택 | P1 |

#### 코드 구조

```python
class SLATier(Enum):
    BRONZE = {"p99": 2000, "p99_9": 5000}
    SILVER = {"p99": 500, "p99_9": 1000}
    GOLD = {"p99": 250, "p99_9": 500}
    PLATINUM = {"p99": 100, "p99_9": 200}

class SLAValidator:
    def __init__(self, tier: SLATier):
        self.tier = tier
        self.response_times: List[float] = []
    
    def record(self, response_time_ms: float):
        self.response_times.append(response_time_ms)
    
    def validate(self) -> SLAResult:
        p99 = np.percentile(self.response_times, 99)
        p99_9 = np.percentile(self.response_times, 99.9)
        
        return SLAResult(
            passed=p99 <= self.tier.value["p99"],
            p99_actual=p99,
            p99_target=self.tier.value["p99"],
            violations=self._count_violations()
        )
```

---

### Phase 2: Zero Variance (데이터 정합성) (1일)

```
┌─────────────────────────────────────────────────────────────┐
│  ZERO VARIANCE VALIDATOR                                     │
├─────────────────────────────────────────────────────────────┤
│  검증 대상                                                   │
│  ├── 💰 포인트: 적립 - 사용 = 현재잔액                       │
│  ├── 📦 재고: 입고 - 출고 = 현재수량                         │
│  ├── 💳 결제: 요청 = 승인 + 취소 + 실패                      │
│  └── 📋 로그: 이벤트 수 = DB 레코드 수                       │
├─────────────────────────────────────────────────────────────┤
│  검증 시점                                                   │
│  ├── 🔄 실시간: 매 트랜잭션 후                               │
│  ├── ⏱️  주기적: 10초마다 전체 검증                          │
│  └── 🏁 최종: 테스트 종료 시                                 │
├─────────────────────────────────────────────────────────────┤
│  오차 허용                                                   │
│  └── ❌ 0원, 0개, 0건 (ZERO TOLERANCE)                       │
└─────────────────────────────────────────────────────────────┘
```

#### 구현 항목

| 항목 | 설명 | 우선순위 |
|------|------|----------|
| `ZeroVarianceValidator` | 정합성 검증 클래스 | P0 |
| `InventoryReconciler` | 재고 정합성 검증 | P0 |
| `PointReconciler` | 포인트 정합성 검증 | P0 |
| `PaymentReconciler` | 결제 정합성 검증 | P0 |
| Real-time Delta Check | 실시간 오차 감지 | P1 |

#### 코드 구조

```python
class ZeroVarianceValidator:
    def __init__(self, db_client, tolerance: int = 0):
        self.db = db_client
        self.tolerance = tolerance  # 0 = Zero Tolerance
        self.snapshots: Dict[str, Any] = {}
    
    def take_snapshot(self, label: str):
        """테스트 시작/종료 시 스냅샷"""
        self.snapshots[label] = {
            "inventory": self._get_inventory_sum(),
            "points": self._get_points_sum(),
            "payments": self._get_payment_totals(),
            "timestamp": datetime.now()
        }
    
    def validate(self) -> ValidationResult:
        """정합성 검증 - 1이라도 틀리면 FAILED"""
        start = self.snapshots["start"]
        end = self.snapshots["end"]
        
        inventory_delta = self._calc_inventory_delta(start, end)
        point_delta = self._calc_point_delta(start, end)
        payment_delta = self._calc_payment_delta(start, end)
        
        total_variance = abs(inventory_delta) + abs(point_delta) + abs(payment_delta)
        
        if total_variance > self.tolerance:
            raise CriticalDataIntegrityError(
                f"Zero Variance FAILED! "
                f"Inventory: {inventory_delta}, "
                f"Points: {point_delta}, "
                f"Payments: {payment_delta}"
            )
        
        return ValidationResult(passed=True, variance=0)
```

---

### Phase 3: 새로운 극한 시나리오 (2일)

#### 3.1 🧟 좀비 인프라 (Resource Exhaustion)

```
┌─────────────────────────────────────────────────────────────┐
│  ZOMBIE INFRASTRUCTURE SCENARIO                              │
├─────────────────────────────────────────────────────────────┤
│  공격 방식                                                   │
│  ├── CPU 점유: stress-ng --cpu 4 --timeout 30s              │
│  ├── 메모리 점유: stress-ng --vm 2 --vm-bytes 512M          │
│  └── I/O 포화: stress-ng --io 4 --timeout 30s               │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 🔍 좀비 감지: 응답 지연 > 30초인 노드 식별             │
│  ├── 🚫 축출(Eviction): 좀비 노드를 서비스에서 제외         │
│  ├── 🔄 자원 재할당: 새 Worker로 트래픽 전환                │
│  └── ⏱️  복구 시간: 감지→축출→정상화 전체 시간 측정        │
└─────────────────────────────────────────────────────────────┘
```

```python
class ZombieInfrastructureScenario:
    """살아있지만 쓸모없는 좀비 노드 시뮬레이션"""
    
    async def execute(self):
        # 1. 특정 컨테이너에 stress 주입
        await self._inject_cpu_stress(container="web", cpu_percent=99)
        
        # 2. 좀비 상태 확인 (응답 > 30초)
        is_zombie = await self._check_zombie_state()
        
        # 3. 사령탑이 감지하는지 확인
        detected = await self._wait_for_detection(timeout=60)
        
        # 4. Eviction 확인
        evicted = await self._check_eviction()
        
        # 5. 정상 복구 확인
        recovered = await self._check_recovery()
        
        return ZombieTestResult(
            zombie_created=True,
            detected=detected,
            evicted=evicted,
            recovered=recovered,
            detection_time_ms=self.detection_time
        )
```

#### 3.2 🧪 데이터 오염 (Logic Poisoning)

```
┌─────────────────────────────────────────────────────────────┐
│  DATA POISONING SCENARIO                                     │
├─────────────────────────────────────────────────────────────┤
│  오염 데이터 유형                                            │
│  ├── 🔢 음수 재고: quantity = -100                          │
│  ├── 🆔 중복 UUID: 동일 order_id로 여러 건 전송             │
│  ├── 💸 비정상 금액: amount = 999999999999                  │
│  ├── 📅 미래 날짜: created_at = 2099-12-31                  │
│  └── 🔀 타입 불일치: price = "invalid"                      │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 🛡️ 입력 검증: 비정상 값 거부                           │
│  ├── 🔒 DB 보호: 오염 데이터 DB 반영 차단                   │
│  ├── 📦 격리 처리: DLQ로 격리 후 분석                       │
│  └── 📊 정합성 유지: Zero Variance 통과                     │
└─────────────────────────────────────────────────────────────┘
```

```python
class DataPoisoningScenario:
    """비정상 데이터 주입 및 방어 검증"""
    
    POISON_TYPES = [
        {"type": "negative_inventory", "payload": {"quantity": -100}},
        {"type": "duplicate_uuid", "payload": {"order_id": "DUPLICATE-001"}},
        {"type": "overflow_amount", "payload": {"amount": 10**15}},
        {"type": "future_date", "payload": {"created_at": "2099-12-31"}},
        {"type": "type_mismatch", "payload": {"price": "not_a_number"}},
    ]
    
    async def execute(self):
        results = {}
        
        for poison in self.POISON_TYPES:
            # 오염 데이터 주입 시도
            response = await self._inject_poison(poison)
            
            # 거부되었는지 확인 (400/422 응답 기대)
            rejected = response.status_code in [400, 422]
            
            # DB에 반영되었는지 확인 (반영되면 FAIL)
            in_db = await self._check_db_contamination(poison)
            
            results[poison["type"]] = {
                "rejected": rejected,
                "db_protected": not in_db,
                "passed": rejected and not in_db
            }
        
        return DataPoisoningResult(
            all_rejected=all(r["rejected"] for r in results.values()),
            db_protected=all(r["db_protected"] for r in results.values()),
            details=results
        )
```

#### 3.3 🧠 분산 뇌사 (Total Blackout)

```
┌─────────────────────────────────────────────────────────────┐
│  TOTAL BLACKOUT SCENARIO                                     │
├─────────────────────────────────────────────────────────────┤
│  중단 대상                                                   │
│  ├── 🗄️  Redis: 캐시/세션 저장소                             │
│  ├── 🐘 PostgreSQL: 메인 데이터베이스                        │
│  └── 🌐 외부 API: 사령탑/결제/알림 서비스                    │
├─────────────────────────────────────────────────────────────┤
│  중단 시간: 60초                                             │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 💾 메모리 스냅샷: Health Bridge 생존                   │
│  ├── 🔄 Graceful Degradation: 읽기 전용 모드                │
│  ├── 📝 로컬 버퍼링: 쓰기 작업 임시 저장                    │
│  ├── 🔌 연결 복구: 인프라 복귀 감지                         │
│  └── 📊 순차 복구: Gradual Recovery 로직                    │
└─────────────────────────────────────────────────────────────┘
```

```python
class TotalBlackoutScenario:
    """모든 외부 연결 차단 후 생존 테스트"""
    
    BLACKOUT_DURATION = 60  # seconds
    
    async def execute(self):
        # 1. 현재 상태 스냅샷
        pre_snapshot = await self._take_system_snapshot()
        
        # 2. 블랙아웃 시작 (Redis, DB, 외부 API 차단)
        await self._start_blackout()
        
        survival_checks = []
        for i in range(self.BLACKOUT_DURATION // 5):
            await asyncio.sleep(5)
            
            # 3. 메모리 스냅샷으로 생존 확인
            health = await self._check_health_bridge()
            survival_checks.append(health)
        
        # 4. 블랙아웃 종료
        await self._end_blackout()
        
        # 5. 순차 복구 모니터링
        recovery_timeline = await self._monitor_gradual_recovery()
        
        # 6. 최종 상태 검증
        post_snapshot = await self._take_system_snapshot()
        
        return TotalBlackoutResult(
            blackout_duration=self.BLACKOUT_DURATION,
            survival_rate=sum(survival_checks) / len(survival_checks),
            recovery_time=recovery_timeline.total_time,
            data_integrity=self._compare_snapshots(pre_snapshot, post_snapshot)
        )
```

#### 3.4 ⏰ 시간 역행 (Clock Skew Attack)

```
┌─────────────────────────────────────────────────────────────┐
│  CLOCK SKEW ATTACK SCENARIO                                  │
├─────────────────────────────────────────────────────────────┤
│  공격 방식                                                   │
│  ├── 시간 후진: 시스템 시간을 2시간 뒤로                    │
│  ├── 시간 전진: 시스템 시간을 2시간 앞으로                  │
│  └── 시간 점프: 랜덤하게 ±24시간 변동                       │
├─────────────────────────────────────────────────────────────┤
│  영향 범위                                                   │
│  ├── 🔑 JWT 토큰: exp 검증 실패                             │
│  ├── 💾 캐시 TTL: 즉시 만료 또는 영원히 유효                │
│  ├── 📋 스케줄러: Cron 작업 오작동                          │
│  └── 📊 로그 순서: 이벤트 순서 역전                         │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 🛡️ NTP 동기화: 자동 시간 보정                          │
│  ├── 🔄 토큰 갱신: 만료된 토큰 자동 재발급                  │
│  └── 📊 Monotonic Clock: 순서 보장                          │
└─────────────────────────────────────────────────────────────┘
```

#### 3.5 📨 메시지 폭풍 (Message Storm / Idempotency Test)

```
┌─────────────────────────────────────────────────────────────┐
│  MESSAGE STORM SCENARIO                                      │
├─────────────────────────────────────────────────────────────┤
│  공격 방식                                                   │
│  ├── 동일 Webhook 1000회 전송 (1초 내)                      │
│  ├── 동일 주문 ID로 100회 결제 요청                         │
│  └── 동일 포인트 적립 요청 500회                            │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 🔑 Idempotency Key: 중복 요청 차단                     │
│  ├── 🔢 처리 횟수: 정확히 1회만 처리                        │
│  ├── 💰 금액 정확성: 1회분 금액만 반영                      │
│  └── 📊 Zero Variance: 정합성 유지                          │
└─────────────────────────────────────────────────────────────┘
```

```python
class MessageStormScenario:
    """중복 메시지 폭풍 및 Idempotency 검증"""
    
    STORM_SIZE = 1000  # 동일 요청 수
    
    async def execute(self):
        idempotency_key = f"STORM-{uuid.uuid4()}"
        
        # 1. 초기 상태 기록
        initial_balance = await self._get_balance()
        
        # 2. 동일 요청 1000회 동시 전송
        tasks = [
            self._send_payment_request(
                idempotency_key=idempotency_key,
                amount=10000
            )
            for _ in range(self.STORM_SIZE)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 3. 성공 응답 수 확인
        success_count = sum(1 for r in responses if r.status_code == 200)
        duplicate_count = sum(1 for r in responses if r.status_code == 409)
        
        # 4. 실제 처리 횟수 확인
        final_balance = await self._get_balance()
        actual_processed = (final_balance - initial_balance) // 10000
        
        return MessageStormResult(
            total_sent=self.STORM_SIZE,
            success_responses=success_count,
            duplicate_responses=duplicate_count,
            actual_processed=actual_processed,
            passed=actual_processed == 1  # 정확히 1회만 처리
        )
```

#### 3.6 🐌 느린 죽음 (Slow Death / Gradual Degradation)

```
┌─────────────────────────────────────────────────────────────┐
│  SLOW DEATH SCENARIO                                         │
├─────────────────────────────────────────────────────────────┤
│  응답 시간 증가 패턴                                         │
│  ├── 0초:  50ms (정상)                                      │
│  ├── 10초: 100ms                                            │
│  ├── 20초: 500ms                                            │
│  ├── 30초: 2,000ms                                          │
│  ├── 40초: 10,000ms (거의 죽음)                             │
│  └── 50초: 30,000ms (완전 죽음)                             │
├─────────────────────────────────────────────────────────────┤
│  검증 항목                                                   │
│  ├── 🔍 조기 감지: 500ms 초과 시점에서 경고                 │
│  ├── 🔴 CB 발동: 2,000ms 초과 시 회로 차단                  │
│  ├── 🔄 자동 Failover: 다른 인스턴스로 전환                 │
│  └── 📊 SLA 위반 최소화: P99 기준 준수                      │
└─────────────────────────────────────────────────────────────┘
```

---

### Phase 4: 고급 거버넌스 기능 (1일)

#### 4.1 🎚️ Adaptive Throttling

```python
class AdaptiveThrottling:
    """에러 버짓 소진율에 따른 차등 트래픽 제한"""
    
    USER_TIERS = {
        "VIP": {"priority": 1, "throttle_threshold": 0.9},
        "Premium": {"priority": 2, "throttle_threshold": 0.7},
        "Standard": {"priority": 3, "throttle_threshold": 0.5},
        "Free": {"priority": 4, "throttle_threshold": 0.3},
    }
    
    def should_throttle(self, user_tier: str, error_budget_consumed: float) -> bool:
        """에러 버짓 소진율에 따라 차등 차단"""
        tier_config = self.USER_TIERS[user_tier]
        return error_budget_consumed > tier_config["throttle_threshold"]
```

#### 4.2 🎲 Circuit Breaker Jitter

```python
class CircuitBreakerWithJitter:
    """복구 시 Thundering Herd 방지를 위한 Jitter"""
    
    def get_recovery_delay(self) -> float:
        """0~500ms 랜덤 지연"""
        base_delay = 100  # ms
        jitter = random.uniform(0, 400)  # ms
        return base_delay + jitter
    
    async def attempt_recovery(self):
        """회로 복구 시도"""
        delay = self.get_recovery_delay()
        await asyncio.sleep(delay / 1000)
        return await self._probe_service()
```

#### 4.3 📊 Real-time Delta Reconciliation

```python
class RealTimeDeltaRecon:
    """실시간 정합성 검증 및 비상 모드 가동"""
    
    CHECK_INTERVAL = 10  # seconds
    
    async def monitor(self):
        while self.running:
            delta = await self._calculate_delta()
            
            if delta != 0:
                # 즉시 비상 모드!
                await self.emergency_client.trigger(
                    level="LEVEL_2",
                    reason=f"Real-time Delta detected: {delta}"
                )
                
                # 모든 쓰기 작업 일시 중단
                await self._pause_writes()
                
                # 원인 분석
                await self._analyze_root_cause()
            
            await asyncio.sleep(self.CHECK_INTERVAL)
```

---

## 📊 통합 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                     STAGE 10 EXTREME V2                              │
│                    (Single Unified Test)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │ SLA Tier    │  │ Zero        │  │ Governance  │                  │
│  │ Validator   │  │ Variance    │  │ Features    │                  │
│  │             │  │ Validator   │  │             │                  │
│  │ • P99 250ms │  │ • Inventory │  │ • Adaptive  │                  │
│  │ • P99.9 500 │  │ • Points    │  │   Throttle  │                  │
│  │ • Real-time │  │ • Payments  │  │ • CB Jitter │                  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                  │
│         │                │                │                          │
│         └────────────────┼────────────────┘                          │
│                          │                                           │
│                          ▼                                           │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                   CHAOS SCENARIO ENGINE                        │  │
│  ├───────────────────────────────────────────────────────────────┤  │
│  │  EXISTING (V1)                  │  NEW (V2)                    │  │
│  │  ┌─────────────────────────┐   │  ┌─────────────────────────┐ │  │
│  │  │ 🔥 Cascading Failure    │   │  │ 🧟 Zombie Infrastructure│ │  │
│  │  │ 🧠 Brain Storm          │   │  │ 🧪 Data Poisoning       │ │  │
│  │  │ 💀 Death Spiral         │   │  │ 🌑 Total Blackout       │ │  │
│  │  │ 🌪️ Chaos Storm          │   │  │ ⏰ Clock Skew Attack    │ │  │
│  │  │ 🔄 Recovery Race        │   │  │ 📨 Message Storm        │ │  │
│  │  │ 📬 DLQ Flood            │   │  │ 🐌 Slow Death           │ │  │
│  │  │ 🚨 Emergency Escalation │   │  │ 🧩 Split Brain          │ │  │
│  │  └─────────────────────────┘   │  └─────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          │                                           │
│                          ▼                                           │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                   UNIFIED RESULT COLLECTOR                     │  │
│  │  • JSON Export  • HTML Report  • Markdown Doc  • Alerts       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 📅 구현 일정

| Phase | 내용 | 예상 소요 | 우선순위 |
|-------|------|-----------|----------|
| **Phase 1** | SLA 강화 (P99 기준, Tier 시스템) | 1일 | 🔴 P0 |
| **Phase 2** | Zero Variance (데이터 정합성) | 1일 | 🔴 P0 |
| **Phase 3** | 새로운 극한 시나리오 6개 | 2일 | 🟡 P1 |
| **Phase 4** | 고급 거버넌스 기능 3개 | 1일 | 🟢 P2 |
| **Phase 5** | 통합 테스트 및 문서화 | 1일 | 🟡 P1 |
| **Total** | - | **6일** | - |

---

## 🎯 성공 기준

### 필수 조건 (Must Have)

| 항목 | 기준 | 현재 | 목표 |
|------|------|------|------|
| **SLA P99** | 복구 시간 | 399ms | ≤ 250ms |
| **Zero Variance** | 데이터 오차 | 미측정 | 0 |
| **Idempotency** | 중복 처리 | 미측정 | 1회만 |
| **Survival Rate** | 블랙아웃 생존 | 미측정 | ≥ 95% |

### 선택 조건 (Nice to Have)

| 항목 | 기준 |
|------|------|
| Zombie Detection Time | ≤ 30초 |
| Poison Rejection Rate | 100% |
| Clock Skew Recovery | ≤ 5초 |
| Adaptive Throttle Accuracy | ≥ 99% |

---

## 🛠️ 기술 스택

| 도구 | 용도 |
|------|------|
| **Locust** | 부하 테스트 프레임워크 |
| **Docker Compose** | 테스트 환경 구성 |
| **stress-ng** | CPU/Memory 스트레스 주입 |
| **toxiproxy** | 네트워크 장애 시뮬레이션 |
| **faketime** | 시간 조작 |
| **NumPy** | Percentile 계산 |

---

## 📁 파일 구조

```
load_tests/
├── scenarios/
│   └── integration/
│       ├── stage10_self_healing.py      # V1 (현재)
│       └── stage10_self_healing_v2.py   # V2 (통합본)
├── utils/
│   └── selfhealing/
│       ├── sla_validator.py             # NEW: SLA Tier 검증
│       ├── zero_variance.py             # NEW: 정합성 검증
│       ├── chaos_scenarios/             # NEW: 추가 시나리오
│       │   ├── zombie_infra.py
│       │   ├── data_poisoning.py
│       │   ├── total_blackout.py
│       │   ├── clock_skew.py
│       │   ├── message_storm.py
│       │   └── slow_death.py
│       └── governance/                  # NEW: 거버넌스
│           ├── adaptive_throttle.py
│           ├── cb_jitter.py
│           └── realtime_recon.py
└── results/
    └── stage10/
        ├── stage10_extreme_v2_*.json
        ├── stage10_extreme_v2_*.html
        └── stage10_extreme_v2_*.md
```

---

## 🚀 실행 방법

```bash
# 환경변수 설정
export STAGE10_SLA_TIER=GOLD           # Bronze/Silver/Gold/Platinum
export STAGE10_ZERO_VARIANCE=true       # 정합성 검증 활성화
export STAGE10_CHAOS_LEVEL=EXTREME      # Normal/Hard/Extreme
export STAGE10_DURATION=300             # 5분

# Docker 서비스 시작
docker-compose up -d

# Stage 10 V2 극한 테스트 실행
locust -f load_tests/scenarios/integration/stage10_self_healing_v2.py \
  --host=http://localhost:8000 \
  --users=50 \
  --spawn-rate=10 \
  --run-time=5m \
  --headless \
  --html=load_tests/results/stage10/stage10_extreme_v2_report.html
```

---

## 📚 참고 자료

### 업계 사례

| 회사 | 도구/방법론 | 특징 |
|------|-------------|------|
| **Netflix** | Chaos Monkey, FIT | 프로덕션 장애 주입 |
| **Google** | DiRT, Fuzz Testing | 연례 재해 복구 훈련 |
| **Amazon** | GameDay, COE | 실시간 장애 시뮬레이션 |
| **Uber** | Watchtower | 자동 이상 탐지 |
| **LinkedIn** | Kafka Cruise Control | 자동 리밸런싱 |

### 관련 문서

- [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md)
- [19_CHAOS_PROOF_ROADMAP.md](19_CHAOS_PROOF_ROADMAP.md)
- [CHAOS_SAFETY_IMPLEMENTATION_PLAN.md](CHAOS_SAFETY_IMPLEMENTATION_PLAN.md)

---

> **📝 문서 히스토리**
> - 2025-12-27: 초안 작성 (v1.0.0)

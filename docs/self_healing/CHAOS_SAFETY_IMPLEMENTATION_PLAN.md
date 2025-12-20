# Chaos Engine 안전장치 구현 계획

> 이 문서는 카오스 엔진의 안전장치(Safety Mechanisms)를 구현하기 위한 상세 계획입니다.
> **Phase 1 구현 완료 (2025-12-20)**

## 📋 목차

1. [현황 분석](#1-현황-분석)
2. [구현 우선순위](#2-구현-우선순위)
3. [상세 구현 명세](#3-상세-구현-명세)
4. [API 설계](#4-api-설계)
5. [구현 체크리스트](#5-구현-체크리스트)
6. [System Dry Run vs Chaos Dry Run](#6-system-dry-run-vs-chaos-dry-run)

---

## 1. 현황 분석

### 1.1 이미 구현된 안전장치 ✅

| 기능 | 위치 | 상태 |
|------|------|------|
| Pre-flight Safety Check | `safety_guard.py` | ✅ 완료 |
| Error Budget 연동 (20% 차단) | `safety_guard.py` | ✅ 완료 |
| Blast Radius Control | `blast_radius.py` | ✅ 완료 |
| REGION 승인 필수 | `blast_radius.py` | ✅ 완료 |
| Kill Switch | `scheduler.py` | ✅ 완료 |
| `fail_safe_on_error=True` | `safety_guard.py` | ✅ 완료 |
| **Self-Expiration (TTL)** | `experiments.py`, `stop_conditions.py` | ✅ 완료 (Phase 1) |
| **Stop Conditions (자동 중단)** | `stop_conditions.py`, `experiments.py` | ✅ 완료 (Phase 1) |
| **Chaos Dry Run 모드** | `scheduler.py`, `experiments.py` | ✅ 완료 (Phase 1) |
| **Idempotent Rollback** | `experiments.py` | ✅ 완료 (Phase 1) |

### 1.2 구현 필요한 안전장치 ⚠️

| 기능 | 중요도 | 현황 |
|------|--------|------|
| ~~**Stop Conditions (자동 중단)**~~ | ~~🔴 최고~~ | ✅ 완료 |
| ~~**Self-Expiration (TTL)**~~ | ~~🔴 최고~~ | ✅ 완료 |
| ~~**Dry Run 모드**~~ | ~~🟠 높음~~ | ✅ 완료 |
| ~~**Idempotent Rollback**~~ | ~~🟠 높음~~ | ✅ 완료 |
| Stop Conditions API | 🟡 중간 | Phase 2 예정 |

---

## 2. 구현 우선순위

### Phase 1: 핵심 안전장치 (필수) ✅ 완료

```
1. Self-Expiration (TTL) 메커니즘 ✅
   └─ 카오스 엔진이 죽어도 타겟 시스템이 자동 복구

2. Stop Conditions (실시간 자동 중단) ✅
   └─ 에러율/지연시간 임계값 초과 시 즉시 중단

3. Dry Run 모드
   └─ 실제 주입 없이 전체 워크플로우 시뮬레이션
```

### Phase 2: 강화 기능 (권장)

```
4. Idempotent Rollback 강화
   └─ 중복 롤백 명령에도 안전한 복구

5. Governance API 완성
   └─ 모든 설정을 API로 제어
```

---

## 3. 상세 구현 명세

### 3.1 Self-Expiration (TTL) 메커니즘

#### 개념

```
┌─────────────────────────────────────────────────────────────────┐
│                    Self-Expiration Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Chaos Engine              Target System (결제 모듈 등)          │
│  ┌──────────────┐          ┌─────────────────────────────┐      │
│  │ inject_chaos │─────────►│ chaos_config + expires_at   │      │
│  │ (TTL=600s)   │          │ (현재시간 + 10분)            │      │
│  └──────────────┘          └─────────────────────────────┘      │
│        │                              │                          │
│        │                              ▼                          │
│        │                   ┌─────────────────────────────┐      │
│        X (엔진 죽음)        │ 매 요청 시:                  │      │
│                            │ if now() > expires_at:       │      │
│                            │   → 카오스 설정 무시         │      │
│                            │   → 원래 동작 수행           │      │
│                            └─────────────────────────────┘      │
│                                                                  │
│  결과: 엔진이 죽어도 10분 후 자동 복구                           │
└─────────────────────────────────────────────────────────────────┘
```

#### 구현 위치 및 변경 사항

**파일 1: `experiments.py`**

```python
# 모든 실험 클래스의 inject_chaos() 메서드에 추가
class ChaosExperiment(ABC):
    default_ttl_seconds: int = 600  # 기본 10분
    ttl_override: Optional[int] = None  # 실험별 오버라이드
    
    def get_effective_ttl(self) -> int:
        return self.ttl_override or self.default_ttl_seconds
    
    def inject_chaos(self) -> bool:
        expires_at = now() + timedelta(seconds=self.get_effective_ttl())
        
        # 타겟 시스템에 expires_at 전달
        self._apply_chaos_config(
            config=self.get_chaos_config(),
            expires_at=expires_at.isoformat(),
        )
```

**파일 2: `runtime_config.py`**

```python
# get_chaos_config() 메서드에 자동 만료 로직 추가
def get_chaos_config(self) -> Dict[str, Any]:
    config = self._backend.get("runtime_config:chaos")
    
    if config and config.get("expires_at"):
        expires_at = datetime.fromisoformat(config["expires_at"])
        if now() > expires_at:
            # 자동 만료: 원래 상태로 복귀
            self._record_auto_expiration(config)
            self._clear_chaos_config()
            return {}  # 빈 설정 반환 (카오스 비활성)
    
    return config or {}

def _record_auto_expiration(self, config: Dict) -> None:
    """Audit Trail에 자동 만료 기록"""
    # SYSTEM_AUTO_EXPIRATION 이벤트 기록
```

**파일 3: `serializers/chaos.py`**

```python
class ExperimentConfigSerializer(serializers.Serializer):
    # 기존 필드들...
    
    default_ttl_seconds = serializers.IntegerField(
        required=False,
        default=600,
        min_value=60,
        max_value=3600,
        help_text="실험 자동 만료 시간 (초, 기본 600=10분)"
    )
    ttl_override = serializers.IntegerField(
        required=False,
        min_value=60,
        max_value=3600,
        help_text="이 실험만의 TTL 오버라이드"
    )
```

---

### 3.2 Stop Conditions (실시간 자동 중단)

#### 개념

```
┌─────────────────────────────────────────────────────────────────┐
│                    Stop Conditions Flow                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  실험 실행 중                                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                                                           │   │
│  │   [10초마다 메트릭 체크]                                   │   │
│  │        │                                                  │   │
│  │        ▼                                                  │   │
│  │   ┌─────────────────────────────────────────────────┐    │   │
│  │   │ Prometheus Query:                                │    │   │
│  │   │ - error_rate > 5%?          → ABORT             │    │   │
│  │   │ - latency_p99 > 2000ms?     → ABORT             │    │   │
│  │   │ - error_budget < 10%?       → ABORT             │    │   │
│  │   └─────────────────────────────────────────────────┘    │   │
│  │        │                                                  │   │
│  │        ▼ (임계값 초과)                                    │   │
│  │   ┌─────────────────────────────────────────────────┐    │   │
│  │   │ 1. request_kill() 호출                           │    │   │
│  │   │ 2. rollback() 실행                               │    │   │
│  │   │ 3. Audit Trail 기록: AUTO_ABORT                  │    │   │
│  │   │ 4. 알림 발송                                      │    │   │
│  │   └─────────────────────────────────────────────────┘    │   │
│  │                                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 구현 위치 및 변경 사항

**파일 1: `experiments.py` - `_check_sla_breach()` 구현**

```python
@dataclass
class StopConditions:
    """자동 중단 조건 설정"""
    max_error_rate_percent: float = 5.0
    max_latency_p99_ms: int = 2000
    min_error_budget_percent: float = 10.0
    check_interval_seconds: int = 10

class ChaosExperiment(ABC):
    stop_conditions: StopConditions = field(default_factory=StopConditions)
    
    def _check_sla_breach(self) -> Optional[str]:
        """
        실시간 SLA 위반 체크.
        
        Returns:
            위반 사유 문자열, 정상이면 None
        """
        try:
            from selfhealing.services.metrics import get_metrics_collector
            
            metrics = get_metrics_collector()
            
            # 1. 에러율 체크
            error_rate = metrics.get_current_error_rate(
                service=self.target_service
            )
            if error_rate > self.stop_conditions.max_error_rate_percent:
                return f"Error rate {error_rate:.1f}% > {self.stop_conditions.max_error_rate_percent}%"
            
            # 2. 지연시간 체크
            latency_p99 = metrics.get_latency_p99(
                service=self.target_service
            )
            if latency_p99 > self.stop_conditions.max_latency_p99_ms:
                return f"Latency P99 {latency_p99}ms > {self.stop_conditions.max_latency_p99_ms}ms"
            
            # 3. 에러 버짓 체크
            from selfhealing.services.error_budget_service import get_error_budget_service
            budget = get_error_budget_service().get_status()
            remaining = budget.get("remaining_percent", 100)
            if remaining < self.stop_conditions.min_error_budget_percent:
                return f"Error budget {remaining:.1f}% < {self.stop_conditions.min_error_budget_percent}%"
            
            return None  # 정상
            
        except Exception as e:
            logger.warning(f"[Chaos] SLA check failed: {e}")
            return None  # 체크 실패 시 계속 진행 (fail-open)
    
    def _monitoring_loop(self):
        """실험 중 모니터링 루프"""
        while self.status == ExperimentStatus.RUNNING:
            breach = self._check_sla_breach()
            if breach:
                logger.error(f"[Chaos] SLA breach detected: {breach}")
                self._auto_abort(reason=breach)
                break
            time.sleep(self.stop_conditions.check_interval_seconds)
    
    def _auto_abort(self, reason: str):
        """자동 중단 및 롤백"""
        self.status = ExperimentStatus.ABORTED
        self.rollback()
        self._record_audit("AUTO_ABORT", {"reason": reason})
        self._send_alert(f"Chaos experiment auto-aborted: {reason}")
```

**파일 2: 새 파일 `stop_conditions.py`**

```python
"""
Stop Conditions Service

실시간 메트릭 기반 자동 중단 조건 관리.
"""

@dataclass
class StopConditionsConfig:
    """Stop Conditions 전역 설정"""
    
    # 에러율 임계값
    max_error_rate_percent: float = 5.0
    
    # 지연시간 임계값
    max_latency_p99_ms: int = 2000
    max_latency_p95_ms: int = 1000
    
    # 에러 버짓 임계값
    min_error_budget_percent: float = 10.0
    
    # 체크 주기
    check_interval_seconds: int = 10
    
    # 연속 위반 횟수 (노이즈 방지)
    consecutive_breaches_required: int = 2
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
```

---

### 3.3 Dry Run 모드

#### 개념

```
┌─────────────────────────────────────────────────────────────────┐
│                       Dry Run Mode                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  dry_run=True 일 때:                                            │
│                                                                  │
│  ✅ 실행되는 것:                     ❌ 실행 안 되는 것:         │
│  ├─ Pre-flight Safety Check         ├─ inject_chaos()           │
│  ├─ Blast Radius Validation         ├─ 실제 장애 주입           │
│  ├─ Approval Workflow               ├─ 타겟 시스템 설정 변경    │
│  ├─ Scheduling Logic                │                           │
│  ├─ Audit Trail Recording           │                           │
│  ├─ Report Generation               │                           │
│  └─ Metrics Recording (dry_run tag) │                           │
│                                                                  │
│  결과: 모든 프로세스 검증, 실제 영향 없음                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 구현 위치 및 변경 사항

**파일 1: `scheduler.py`**

```python
@dataclass
class SchedulerConfig:
    # 기존 필드들...
    
    dry_run_mode: bool = True  # 기본값 True (안전)
    """
    Dry Run 모드.
    True: 실제 장애 주입 없이 전체 워크플로우만 검증
    False: 실제 장애 주입 (프로덕션 모드)
    """

class ChaosSchedulerService:
    def execute_experiment(self, schedule_id: str) -> ExecutionResult:
        # ... 기존 로직 ...
        
        if self._config.dry_run_mode:
            logger.info(f"[DryRun] Simulating experiment {schedule_id}")
            result = self._simulate_experiment(experiment)
            result.dry_run = True
        else:
            result = self._execute_real_experiment(experiment)
            result.dry_run = False
        
        # Audit Trail 기록 (dry_run 여부 포함)
        self._record_execution(result)
        return result
    
    def _simulate_experiment(self, experiment) -> ExecutionResult:
        """Dry Run: 실제 주입 없이 시뮬레이션"""
        # Pre-flight checks
        safety_result = self._safety_guard.check(...)
        if not safety_result.allowed:
            return ExecutionResult(success=False, skipped=True, dry_run=True)
        
        # Simulate success (실제 inject_chaos 호출 안 함)
        return ExecutionResult(
            success=True,
            dry_run=True,
            simulated_duration=experiment.duration_seconds,
        )
```

**파일 2: `experiments.py`**

```python
class ChaosExperiment(ABC):
    dry_run: bool = False
    
    def run(self) -> ExperimentResult:
        if self.dry_run:
            return self._run_dry()
        return self._run_real()
    
    def _run_dry(self) -> ExperimentResult:
        """Dry Run: 실제 주입 없이 시뮬레이션"""
        self.status = ExperimentStatus.RUNNING
        
        # 검증만 수행
        logger.info(f"[DryRun] Would inject: {self.get_chaos_config()}")
        
        # 대기 시간 시뮬레이션 (실제 대기 없음)
        self.status = ExperimentStatus.COMPLETED
        
        return ExperimentResult(
            success=True,
            dry_run=True,
            message="Dry run completed - no actual chaos injected",
        )
```

---

### 3.4 Idempotent Rollback (멱등성 롤백)

#### 개념

```
┌─────────────────────────────────────────────────────────────────┐
│                    Idempotent Rollback                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  문제 상황:                                                      │
│  ├─ 엔진 복구 후 중복 롤백 명령                                  │
│  ├─ 네트워크 지연으로 롤백 재시도                                │
│  └─ 여러 인스턴스에서 동시 롤백 시도                            │
│                                                                  │
│  해결책:                                                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                                                           │   │
│  │   rollback() 호출 시:                                     │   │
│  │   1. 현재 상태가 이미 "clean"인지 확인                    │   │
│  │   2. 롤백 전 상태 스냅샷 저장 (idempotency_key 기반)      │   │
│  │   3. 같은 key로 재호출 시 → 이전 결과 반환               │   │
│  │   4. 결과: N번 호출해도 1번만 실행, 항상 동일 결과        │   │
│  │                                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 구현 위치 및 변경 사항

**파일: `experiments.py`**

```python
class ChaosExperiment(ABC):
    _rollback_completed: bool = False
    _rollback_key: Optional[str] = None
    
    def rollback(self) -> RollbackResult:
        """멱등성 있는 롤백"""
        
        # 1. 이미 롤백 완료 확인
        if self._rollback_completed:
            logger.info(f"[Rollback] Already rolled back: {self.id}")
            return RollbackResult(success=True, already_clean=True)
        
        # 2. 롤백 키로 중복 체크
        rollback_key = f"rollback:{self.id}"
        if self._is_rollback_in_progress(rollback_key):
            logger.info(f"[Rollback] In progress by another instance: {self.id}")
            return RollbackResult(success=True, deferred=True)
        
        try:
            # 3. 롤백 잠금 획득
            self._acquire_rollback_lock(rollback_key)
            
            # 4. 실제 롤백 수행
            self._do_rollback()
            
            # 5. 완료 표시
            self._rollback_completed = True
            self._record_audit("ROLLBACK_SUCCESS", {"key": rollback_key})
            
            return RollbackResult(success=True)
            
        except Exception as e:
            logger.exception(f"[Rollback] Failed: {self.id}")
            return RollbackResult(success=False, error=str(e))
            
        finally:
            self._release_rollback_lock(rollback_key)
```

---

## 4. API 설계

### 4.1 Governance API 완성 목록

| 기능 | 엔드포인트 | Method | 설명 |
|------|-----------|--------|------|
| Safety Guard | `/api/chaos/config/safety/` | GET/PATCH | 에러 버짓 임계값, 쿨다운 |
| **Stop Conditions** | `/api/chaos/config/stop-conditions/` | GET/PATCH | 자동 중단 메트릭 설정 |
| Blast Radius | `/api/chaos/config/blast-radius/` | GET/PATCH | 스코프별 제한 |
| **TTL Settings** | `/api/chaos/config/ttl/` | GET/PATCH | 기본 TTL, 최대 TTL |
| **Dry Run Toggle** | `/api/chaos/config/dry-run/` | GET/PATCH | Dry Run 모드 온/오프 |
| **Global Kill All** | `/api/chaos/control/kill-all/` | POST | 모든 실험 즉시 중단 |

### 4.2 Stop Conditions API 상세

```http
GET /api/self-healing/chaos/config/stop-conditions/
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "max_error_rate_percent": 5.0,
    "max_latency_p99_ms": 2000,
    "max_latency_p95_ms": 1000,
    "min_error_budget_percent": 10.0,
    "check_interval_seconds": 10,
    "consecutive_breaches_required": 2,
    "enabled": true
  }
}
```

```http
PATCH /api/self-healing/chaos/config/stop-conditions/
```

**Request:**
```json
{
  "max_error_rate_percent": 3.0,
  "max_latency_p99_ms": 1500
}
```

### 4.3 TTL Settings API 상세

```http
GET /api/self-healing/chaos/config/ttl/
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "default_ttl_seconds": 600,
    "min_ttl_seconds": 60,
    "max_ttl_seconds": 3600,
    "auto_expiration_enabled": true
  }
}
```

### 4.4 Dry Run API 상세

```http
GET /api/self-healing/chaos/config/dry-run/
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "enabled": true,
    "reason": "Initial deployment - simulation mode"
  }
}
```

```http
PATCH /api/self-healing/chaos/config/dry-run/
```

**Request:**
```json
{
  "enabled": false,
  "reason": "Enabling production mode after 4 weeks of dry run"
}
```

### 4.5 Kill All API 상세

```http
POST /api/self-healing/chaos/control/kill-all/
```

**Request:**
```json
{
  "reason": "Emergency stop - production incident",
  "operator": "ops@company.com"
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "experiments_killed": 3,
    "rollbacks_initiated": 3,
    "ttl_configs_cleared": 5
  }
}
```

---

## 5. 구현 체크리스트

### Phase 1: 핵심 안전장치 (다음 세션)

- [x] **Self-Expiration (TTL)** ✅ 완료 (2025-12-20)
  - [x] `experiments.py`: `default_ttl_seconds`, `ttl_override` 필드 추가
  - [x] `experiments.py`: `inject_chaos()`에 `expires_at` 전달 로직
  - [x] `runtime_config.py`: `update_chaos_ttl_config()` 메서드 추가
  - [x] `stop_conditions.py`: `TTLConfig` 클래스 정의
  - [x] `serializers/chaos.py`: TTL 관련 필드 추가

- [x] **Stop Conditions** ✅ 완료 (2025-12-20)
  - [x] `stop_conditions.py`: 새 파일 생성, `StopConditionsConfig` 정의
  - [x] `experiments.py`: `_check_sla_breach()` 실제 구현
  - [x] `experiments.py`: `_monitor_with_kill_switch()` 에 Stop Conditions 통합
  - [x] `experiments.py`: 자동 중단 시 `_stop_condition_violation` 기록
  - [x] `runtime_config.py`: `update_chaos_stop_conditions_config()` 추가

- [x] **Dry Run 모드** ✅ 완료 (2025-12-20)
  - [x] `scheduler.py`: `SchedulerConfig.dry_run_mode` 추가 (기본 True)
  - [x] `scheduler.py`: dry_run 플래그 전달 로직
  - [x] `experiments.py`: `_run_dry()` 구현
  - [x] 모든 Audit Trail에 `dry_run` 플래그 추가

- [x] **Idempotent Rollback** ✅ 완료 (2025-12-20)
  - [x] `experiments.py`: `_rollback_lock` 추가
  - [x] `experiments.py`: `_rollback_completed` 플래그로 멱등성 보장
  - [x] 모든 5종 실험 클래스에 적용

### Phase 2: API 및 강화 기능

- [ ] **Governance API**
  - [ ] `views/chaos.py`: `StopConditionsConfigView` 추가
  - [ ] `views/chaos.py`: `TTLConfigView` 추가
  - [ ] `views/chaos.py`: `DryRunConfigView` 추가
  - [ ] `views/chaos.py`: `KillAllView` 추가
  - [ ] `urls.py`: 새 엔드포인트 등록
  - [x] `serializers/chaos.py`: 관련 Serializer 추가 ✅

- [x] **Idempotent Rollback** ✅ Phase 1에서 완료
  - [x] `experiments.py`: 멱등성 롤백 로직 구현
  - [x] 롤백 잠금 메커니즘 (threading.Lock)
  - [x] 중복 롤백 감지 및 처리

### Phase 3: 테스트 및 문서

- [ ] 유닛 테스트 추가
  - [ ] `test_ttl_expiration.py`
  - [ ] `test_stop_conditions.py`
  - [ ] `test_dry_run.py`
  - [ ] `test_idempotent_rollback.py`

- [ ] 문서 업데이트
  - [ ] `13_CHAOS_ENGINEERING.md` 안전장치 섹션 추가
  - [ ] API 레퍼런스 업데이트

---

## 부록: 업계 참조

### Netflix ChAP

- "Chaos experiments should be OFF by default"
- "Every experiment must have an automatic abort condition"
- Halt Condition + Blast Radius Limiting

### Google DiRT

- "Test the tests" - 카오스 시스템 자체도 테스트
- Dry Run → Shadow → Canary → Production 점진적 도입

### AWS Fault Injection Simulator

- Stop Conditions: CloudWatch 알람 연동
- Target Guardrails: IAM 기반 범위 제한
- Experiment Templates: 사전 검증된 템플릿

### Gremlin

- "Thoughtful Chaos" - 항상 롤백 계획 필수
- Pre-built safety checks
- Automatic experiment timeout

---

## 6. System Dry Run vs Chaos Dry Run

### 6.1 개요

이 프로젝트에는 **두 가지 별도의 Dry Run 시스템**이 존재합니다:

| 구분 | System Dry Run | Chaos Dry Run |
|------|----------------|---------------|
| **위치** | `system_control.py` | `chaos/scheduler.py`, `chaos/experiments.py` |
| **범위** | 전체 Self-Healing 시스템 | Chaos 실험 주입만 |
| **용도** | 자동 복구 동작 관찰 (실행 안 함) | 카오스 주입 검증 (주입 안 함) |
| **기본값** | `False` (활성 상태) | `True` (안전 모드) |
| **API** | `/api/self-healing/system/dry-run/enable/` | 스케줄러 설정 |

### 6.2 System Dry Run (기존)

```
┌─────────────────────────────────────────────────────────────────┐
│                   System Dry Run (시스템 수준)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  위치: selfhealing/services/system_control.py                   │
│                                                                  │
│  제어 대상:                                                      │
│  ├─ Circuit Breaker 상태 변경                                   │
│  ├─ Retry 정책 적용                                             │
│  ├─ DLQ 메시지 처리                                             │
│  ├─ Scale In/Out 명령                                           │
│  └─ 모든 자동 복구 액션                                          │
│                                                                  │
│  dry_run=True 일 때:                                            │
│  → 로그만 기록, 실제 시스템 변경 없음                            │
│  → "what would happen" 모드                                      │
│                                                                  │
│  API:                                                            │
│  POST /api/self-healing/system/dry-run/enable/                  │
│  POST /api/self-healing/system/dry-run/disable/                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 Chaos Dry Run (Phase 1 신규)

```
┌─────────────────────────────────────────────────────────────────┐
│                   Chaos Dry Run (실험 수준)                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  위치: selfhealing/services/chaos/scheduler.py                  │
│        selfhealing/services/chaos/experiments.py                │
│                                                                  │
│  제어 대상:                                                      │
│  ├─ Latency 주입                                                │
│  ├─ Error 주입                                                  │
│  ├─ Timeout 주입                                                │
│  ├─ Resource 고갈                                               │
│  └─ Partition 실험                                              │
│                                                                  │
│  dry_run=True 일 때:                                            │
│  → Pre-flight 검증 ✅ 실행됨                                     │
│  → Blast Radius 검증 ✅ 실행됨                                   │
│  → 실제 장애 주입 ❌ 건너뜀                                      │
│  → Audit Trail ✅ 기록됨 (dry_run 태그)                          │
│                                                                  │
│  설정:                                                           │
│  SchedulerConfig(dry_run_mode=True)  # 기본값                   │
│  ExperimentConfig(dry_run=True)      # 개별 실험                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.4 상호 작용

```
                    System Dry Run          Chaos Dry Run
                    (system_control.py)     (chaos/*)
                          │                      │
                          ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  Chaos 실험 발생                                                 │
│       │                                                          │
│       ▼                                                          │
│  Chaos Dry Run 체크 ─── dry_run=True? ──→ 시뮬레이션만          │
│       │                       │                                  │
│       │ (dry_run=False)       │                                  │
│       ▼                       │                                  │
│  실제 장애 주입 (Latency, Error 등)                              │
│       │                                                          │
│       ▼                                                          │
│  Self-Healing 시스템 반응                                        │
│       │                                                          │
│       ▼                                                          │
│  System Dry Run 체크 ─── dry_run=True? ──→ 로그만 기록          │
│       │                       │                                  │
│       │ (dry_run=False)       │                                  │
│       ▼                       ▼                                  │
│  실제 복구 액션 실행     아무 것도 안 함                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.5 사용 시나리오

| 시나리오 | System Dry Run | Chaos Dry Run | 결과 |
|----------|----------------|---------------|------|
| 프로덕션 카오스 테스트 | `False` | `False` | 실제 장애 주입 + 실제 복구 |
| 카오스 시나리오 검증 | `False` | `True` | 장애 주입 없음, 복구 대기 없음 |
| 복구 로직 테스트 | `True` | `False` | 실제 장애 주입, 복구 로그만 |
| 완전 관찰 모드 | `True` | `True` | 모든 것 시뮬레이션 |

---

**작성일**: 2025-12-20  
**작성자**: GitHub Copilot  
**상태**: ✅ Phase 1 완료

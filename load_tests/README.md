# Load Test Suite

Locust 기반 부하, 스트레스, 카오스 테스트 모음입니다.
Django 쇼핑몰 결제 시스템의 동작을 검증합니다.

---

## 목적 및 범위

### 검증 대상

이 테스트 스위트는 다음 시스템 동작을 검증합니다:

- **데이터 정합성**: 재고 차감, 포인트 사용, 주문-결제 일관성
- **동시성 처리**: 중복 결제 방지, 레이스 컨디션 대응
- **장애 복원력**: 랜덤 실패, 지연, 타임아웃 상황에서의 시스템 안정성
- **Self-Healing 메커니즘**: Circuit Breaker 전이, DLQ 재처리, 제어 API 동작

### 비-목표 (Non-Goals)

다음은 이 테스트의 범위에 포함되지 **않습니다**:

- 실제 PG(Payment Gateway) 연동 검증 (모의 응답 사용)
- 프로덕션 환경 SLA 보장
- 외부 서비스(SMS, 이메일 등) 통합 테스트
- 브라우저 기반 UI/UX 테스트

---

## 테스트 구조

### 디렉토리 역할

```
load_tests/
├── config.py              # 전역 설정 (HOST, 사용자 수, API 엔드포인트, SLA 목표)
├── locustfile.py          # Locust 메인 진입점 (가중치 기반 사용자 조합)
│
├── scenarios/             # Stage별 개별 시나리오 파일
│                          # 각 파일은 특정 검증 목표를 가진 독립 실행 단위
│
├── users/                 # 사용자 행동 패턴 정의
│   ├── base.py            # 공통 기능 (로그인, 상품 캐싱)
│   ├── browser.py         # 조회 전용 사용자
│   ├── shopper.py         # 장바구니까지 사용하는 사용자
│   └── buyer.py           # 결제 완료까지 수행하는 사용자
│
├── utils/                 # 공통 헬퍼 함수
│   ├── login_helper.py    # 로그인/인증 처리
│   ├── product_helper.py  # 상품 조회/캐싱
│   ├── cart_helper.py     # 장바구니 조작
│   └── payment_helper.py  # 결제 요청/확인
│
├── validators/            # 데이터 정합성 검증기
│   ├── stock_validator.py # 재고 oversell 검증
│   ├── point_validator.py # 포인트 정합성 검증
│   └── order_validator.py # 주문 상태 검증
│
├── chaos/                 # Chaos Engineering 도구
│   ├── fault_injector.py  # 제어된 장애 주입 (latency, error, timeout)
│   └── toxiproxy_client.py# 네트워크 장애 시뮬레이션 클라이언트
│
├── metrics/               # 커스텀 메트릭 수집
│   ├── custom_metrics.py  # P99.9, 에러 유형별 통계
│   └── event_hooks.py     # Locust 이벤트 훅 설정
│
├── runners/               # 실행 스크립트 및 프로파일
│   ├── run_stage.py       # Stage/Profile 기반 실행기
│   ├── config.yaml        # Stage 정의 및 Profile 구성
│   ├── smoke.sh           # Smoke 테스트 래퍼
│   ├── full_cycle.sh      # Full 테스트 래퍼
│   └── chaos.sh           # Chaos 테스트 래퍼
│
├── setup/                 # 환경 설정 스크립트
│   └── environment.py     # 테스트 데이터 생성 (멱등성 보장)
│
├── fixtures/              # 테스트 데이터 관리
│   ├── seeder.py          # 데이터 시딩
│   └── cleaner.py         # 데이터 정리
│
├── reports/               # 테스트 결과 저장 (HTML, JSON)
│
└── docs/                  # 상세 설계 문서
```

---

## 실행 모델

### Stage Runner를 통한 실행

`runners/run_stage.py`는 Stage 및 Profile 기반 실행을 제공합니다.

```bash
# 단일 Stage 실행
python load_tests/runners/run_stage.py stage0_smoke

# 다른 호스트 지정
python load_tests/runners/run_stage.py stage1_happy --host http://staging:8000

# Profile 실행 (여러 Stage 순차 실행)
python load_tests/runners/run_stage.py --profile quick
python load_tests/runners/run_stage.py --profile full

# 환경 셋업 포함 실행
python load_tests/runners/run_stage.py --setup --profile full

# 사용 가능한 Stage/Profile 목록 확인
python load_tests/runners/run_stage.py --list
```

### Locust 직접 실행

개별 시나리오 파일을 Locust로 직접 실행할 수 있습니다.

```bash
# CLI 모드 (headless)
locust -f load_tests/scenarios/stage0_smoke.py \
  --host=http://localhost:8000 \
  --users=5 --spawn-rate=5 --run-time=30s \
  --headless --html=report.html

# Web UI 모드 (브라우저에서 http://localhost:8089 접속)
locust -f load_tests/locustfile.py --host=http://localhost:8000
```

### Docker 환경 실행

```bash
# Docker Compose 서비스 시작 후 실행
docker compose exec web python load_tests/runners/run_stage.py stage0_smoke

# Docker 환경용 호스트 자동 감지
python load_tests/runners/run_stage.py --docker --profile full
```

### Shell 래퍼 스크립트

`runners/` 디렉토리에 플랫폼별 래퍼 스크립트가 제공됩니다:

- `smoke.sh` / `smoke.ps1`: 환경 검증 테스트
- `full_cycle.sh` / `full_cycle.ps1`: 전체 테스트 사이클
- `chaos.sh` / `chaos.ps1`: Chaos 테스트

---

## Stage / Scenario 의미

### Stage란?

이 코드베이스에서 **Stage**는 특정 검증 목표를 가진 독립적인 테스트 단위입니다.

- 각 Stage는 `scenarios/stage{N}_{name}.py` 형식의 파일로 정의됩니다
- Stage 번호는 실행 순서가 아닌 개발/정의 순서를 나타냅니다
- Stage 간 의존성은 `runners/config.yaml`의 `required` 속성으로 표시됩니다

### Stage 구성 예시 (config.yaml 기반)

Stage 정의는 `runners/config.yaml`에 중앙 관리됩니다:

```yaml
stages:
  stage0_smoke:
    file: scenarios/stage0_smoke.py
    users: 5
    spawn_rate: 5
    duration: "30s"
    required: true  # 실패 시 후속 Stage 중단
    tags: ["smoke", "baseline"]

  stage6_chaos:
    file: scenarios/stage6_chaos_random.py
    users: 100
    spawn_rate: 20
    duration: "5m"
    env:
      CHAOS_ENABLED: "true"
      CHAOS_PROBABILITY: "0.10"
    tags: ["chaos", "random"]
```

### Profile

Profile은 여러 Stage를 논리적으로 그룹화한 실행 단위입니다:

```yaml
profiles:
  smoke:
    stages: [stage0_smoke]

  quick:
    stages: [stage0_smoke, stage1_happy, stage2_idempotent]

  full:
    stages: [stage0_smoke, stage1_happy, stage2_idempotent, 
             stage3_latency, stage4_cancel, stage5_rollback,
             stage6_chaos, stage7_race]

  self_healing:
    stages: [stage0_smoke, stage10_self_healing, stage11_ramp_threshold, ...]
```

새로운 Stage 추가 시 `runners/config.yaml`에 정의하면 자동으로 runner에서 인식합니다.

---

## 결과 및 증거

### 출력 형식

| 유형 | 위치 | 설명 |
|------|------|------|
| HTML 리포트 | `load_tests/reports/` | Locust 기본 HTML 리포트 (타임스탬프 포함) |
| JSON 결과 | `load_tests/reports/` | Stage별 상세 결과 데이터 |
| 콘솔 로그 | stdout | 실행 중 통계 및 진행 상황 |

### 리포트 파일 명명 규칙

```
reports/
├── stage0_smoke_20251207_120005.html
├── stage1_happy_20251207_121530.html
├── stage35_results.json
└── stage35_final_analysis.md
```

### 검증 결과 확인

각 Stage 실행 후 콘솔에 요약 통계가 출력됩니다:

```
📈 요약:
   - 총 요청 수: 12,345
   - 실패 수: 23
   - 평균 응답시간: 156.78ms
   - 에러율: 0.19%
```

Validator 클래스들은 테스트 중 데이터 정합성을 검증하고 불일치 발생 시 로그를 기록합니다.

---

## 안전 및 주의사항

### 실행 환경 요구사항

1. **프로덕션 환경 사용 금지**: 테스트는 반드시 개발/스테이징 환경에서만 실행
2. **테스트 데이터 필수**: `load_test_user_*` 형식의 테스트 사용자가 사전 생성되어야 함
3. **PG 모의 응답**: 코드는 실제 PG 연동 없이 모의 응답을 사용함

### 테스트 데이터 생성

```bash
# 환경 셋업 스크립트 사용 (멱등성 보장)
python load_tests/setup/environment.py --full

# 또는 Django 관리 명령어
python manage.py create_load_test_users --count=1000
```

### 환경 변수

Chaos 테스트 시 다음 환경 변수가 사용됩니다 (코드에서 파생):

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `CHAOS_ENABLED` | Chaos 모드 활성화 | `false` |
| `CHAOS_PROBABILITY` | 장애 주입 확률 (0.0~1.0) | `0.10` |
| `CHAOS_LATENCY_MIN_MS` | 최소 지연 시간 | `500` |
| `CHAOS_LATENCY_MAX_MS` | 최대 지연 시간 | `3000` |
| `LOCUST_HOST` | 대상 서버 URL | `http://localhost:8000` |

### 리소스 영향

- 고부하 테스트(100+ 동시 사용자)는 대상 서버에 상당한 부하를 발생시킴
- Soak 테스트(장시간 실행)는 메모리 누수 감지 목적으로 30분 이상 실행됨
- 일부 Chaos 시나리오는 의도적으로 서비스 불안정 상태를 유발함

### 정리

테스트 후 `load_test_` 접두사가 붙은 데이터는 정리하거나, 다음 테스트 전 환경 셋업 스크립트가 자동으로 초기화합니다.

---

## 참고 문서

추가 설계 문서는 `load_tests/docs/` 디렉토리에 있습니다:

- `LOAD_TEST_GUIDE.md`: 상세 실행 가이드
- `CHAOS_TEST_DESIGN.md`: Chaos 테스트 설계 원칙
- `CHAOS_ENGINEERING_GUIDE.md`: Chaos Engineering 방법론
- `SELF_HEALING_LOAD_TEST_PLAN.md`: Self-Healing 관련 테스트 계획

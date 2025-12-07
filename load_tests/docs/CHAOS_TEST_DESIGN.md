# Payment Load Test Suite — 설계 문서

> **버전**: 1.1  
> **최종 수정**: 2025-12-07  
> **프로젝트**: 솔로 개발 e-commerce Payment 시스템

---

## ⚠️ 범위 및 제약사항

> 본 문서는 **솔로 개발 e-commerce 프로젝트**의 Payment Load Test 설계입니다.
> 
> - **필수 구현**: Stage 0-5 (환경검증 → Rollback 검증)
> - **확장/참고용**: Stage 6-9 (Chaos, Race, Webhook, Soak)
> - **현재 제약**: 클라이언트측 시뮬레이션 (실제 PG/네트워크 장애 주입 아님)

### 현재 구현의 한계

| 테스트 | 문서 목표 | 실제 구현 |
|--------|----------|----------|
| Latency Injection | PG 서버 지연 | Locust 클라이언트 `time.sleep()` |
| Webhook Test | 실 Toss 서명 검증 | 서명 없이 핸들러 직접 호출 |
| Race Condition | DB 레벨 동시성 | Locust 동시 요청 (한계 있음) |

> 📌 진정한 Chaos Engineering (Toxiproxy, 서버측 미들웨어 등)은 향후 확장 시 고려

---

## 📍 Test Stage 개요

### 필수 Stage (Stage 0-5)

| Stage | Name | 목적 | Users | Duration |
|-------|------|------|-------|----------|
| **0** | Smoke | 환경/로그인/기본 플로우 확인 | 5 | 30s |
| **1** | Happy Load | 정상 부하 성능 측정 | 50~200 | 3m |
| **2** | Idempotency | 중복 결제 방지 검증 | 30 | 2m |
| **3** | Latency | 지연 상황 동작 확인 | 50 | 3m |
| **4** | Cancel Storm | 결제 직후 취소 스트레스 | 50 | 2m |
| **5** | Rollback | 재고/포인트 복구 검증 | 30 | 3m |

### 확장 Stage (Stage 6-9) — Optional

| Stage | Name | 목적 | 비고 |
|-------|------|------|------|
| **6** | Chaos Random | 랜덤 실패 주입 | 클라이언트 시뮬레이션 |
| **7** | Race Conflict | 동시 결제 충돌 | Locust 동시성 한계 |
| **8** | Webhook | Webhook 핸들러 테스트 | 서명 검증 미포함 |
| **9** | Soak | 장시간 안정성 | CI 제외 권장 |

---

## 📁 디렉토리 구조

```
load_tests/
├── README.md                    # 빠른 시작 가이드
├── config.py                    # Python 설정
├── locustfile.py                # 메인 진입점
│
├── docs/
│   └── CHAOS_TEST_DESIGN.md     # 이 문서
│
├── scenarios/                   # Stage별 시나리오
│   ├── stage0_smoke.py
│   ├── stage1_happy_load.py
│   ├── stage2_idempotent.py
│   ├── stage3_latency.py
│   ├── stage4_cancel_storm.py
│   ├── stage5_rollback.py
│   ├── stage6_chaos_random.py   # [확장]
│   ├── stage7_race_conflict.py  # [확장]
│   ├── stage8_webhook.py        # [확장]
│   └── stage9_soak.py           # [확장]
│
├── utils/                       # 공통 헬퍼
│   ├── login_helper.py
│   ├── product_helper.py
│   ├── cart_helper.py
│   └── payment_helper.py
│
├── validators/                  # 데이터 무결성 검증
│   ├── stock_validator.py
│   ├── point_validator.py
│   └── order_validator.py
│
├── chaos/                       # 장애 시뮬레이션 (클라이언트측)
│   └── fault_injector.py
│
├── metrics/                     # 커스텀 메트릭
│   ├── custom_metrics.py
│   └── event_hooks.py
│
├── runners/                     # 실행 스크립트
│   ├── config.yaml
│   ├── run_stage.py
│   ├── smoke.sh / smoke.ps1
│   └── full_cycle.sh / full_cycle.ps1
│
├── fixtures/                    # 테스트 데이터
│   ├── seeder.py
│   └── cleaner.py
│
└── reports/                     # 결과 리포트
```

---

## 📑 Stage별 상세

### Stage 0 — Smoke

```python
# 환경 정상 동작 확인
# Users: 5, Duration: 30s

- 로그인 성공 확인
- 제품 조회 성공 확인  
- 단일 상품 주문 + 결제 1회 성공
```

### Stage 1 — Happy Load

```python
# 정상 부하에서 성능 측정
# Users: 50 → 100 → 200

- 전체 결제 플로우 반복
- P95/P99 레이턴시 측정
- 에러율 < 1% 목표
```

### Stage 2 — Idempotency

```python
# 중복 결제 방지 검증

- 동일 payment_key로 2회 요청
- 첫 요청: 200/201, 두 번째: 400/409
- 둘 다 200이면 CRITICAL
```

### Stage 3 — Latency

```python
# 지연 상황 동작 확인
# ⚠️ 클라이언트측 sleep으로 시뮬레이션

- 요청 전 500~3000ms 대기
- Timeout 처리 로직 검증
```

### Stage 4 — Cancel Storm

```python
# 결제 직후 취소 스트레스

- confirm 후 0.1~1초 내 cancel
- 취소 성공률 측정
- 재고 복구 확인
```

### Stage 5 — Rollback

```python
# 실패 시 재고/포인트 롤백 검증

1. stock_before, point_before 저장
2. 강제 결제 실패 트리거
3. stock_after, point_after 확인
4. before == after 검증
```

---

## 📊 측정 지표

| 지표 | 설명 | 목표 |
|------|------|------|
| P95 | 상위 5% 제외 | SLA 기준 |
| P99 | 상위 1% 제외 | 경고 기준 |
| Error Rate (4xx) | 클라이언트 에러 | < 5% |
| Error Rate (5xx) | 서버 에러 | < 0.1% |

---

## 🚀 실행 방법

### 단일 Stage

```bash
# Smoke Test
python load_tests/runners/run_stage.py stage0_smoke

# Happy Load
python load_tests/runners/run_stage.py stage1_happy
```

### 프로파일

```bash
# Quick (Stage 0-2)
python load_tests/runners/run_stage.py --profile quick

# Standard (Stage 0-5)
python load_tests/runners/run_stage.py --profile standard

# Stage 목록 확인
python load_tests/runners/run_stage.py --list
```

---

## ✅ 성공 기준

| Stage | 성공 조건 |
|-------|----------|
| Stage 0 | 100% 성공, 에러 0 |
| Stage 1 | P99 < SLA, 에러율 < 1% |
| Stage 2 | 중복 결제 0건 |
| Stage 3 | 지연 후 정상 처리 |
| Stage 4 | 취소 성공률 > 95% |
| Stage 5 | Rollback 100% 성공 |

---

## 📌 향후 확장 고려사항

진정한 Chaos Engineering 도입 시:

| 방법 | 설명 |
|------|------|
| Toxiproxy | 네트워크 레벨 지연/장애 주입 |
| 서버 TEST_MODE | Django 미들웨어로 장애 주입 |
| DB 레벨 Lock 테스트 | 별도 테스트 스크립트 |
| 실 PG Sandbox | Toss 테스트 환경 연동 |

---

> 본 문서는 솔로 개발 프로젝트의 실용적인 부하 테스트를 목표로 합니다.  
> 엔터프라이즈급 Chaos Engineering은 팀/인프라 확장 시 고려하세요.

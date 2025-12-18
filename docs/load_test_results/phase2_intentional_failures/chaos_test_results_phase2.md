# Phase 2: Chaos & Load Test Results

## 개요

이 문서는 Phase 2 Chaos Engineering 및 Load Testing 결과를 상세히 정리합니다.
Phase 2는 시스템의 복원력, 성능 임계점, 스파이크 회복 능력을 검증합니다.

## 테스트 환경

```yaml
테스트 도구: Locust 2.42.5
테스트 호스트: http://localhost:8000
운영 체제: Windows 10
Python 버전: 3.12

Docker Compose Services:
  - web: Django 애플리케이션
  - celery_worker: 비동기 작업 처리
  - celery_beat: 스케줄링
  - db: PostgreSQL 15
  - redis: Redis 7
  - nginx: Reverse Proxy

Chaos 설정:
  CHAOS_MODE: true
  CHAOS_PROBABILITY: 0.15
```

## 테스트 결과 요약

| Stage | 시나리오 | 상태 | 요청 수 | 에러율 | RPS |
|-------|----------|------|--------|--------|-----|
| Stage 6 | Chaos Random | ✅ | 4,745 | 0% (system) | 39.8 |
| Stage 9 | Soak Test | ✅ | 4,915 | 28.99%* | 27.4 |
| Stage 11 | Ramp Threshold | ⚠️ | 328 | 39.02%* | 21.5 |
| Stage 12 | Spike Recovery | ✅ | 2,080 | 42.69%* | 23.4 |
| Stage 13 | Repeated Spike | ✅ | 1,235 | 40.49%* | 52.5 |
| **합계** | - | - | **13,303** | - | - |

> *: 비즈니스 로직 에러 (HTTP 400). 시스템 에러 (5xx)는 거의 0%

---

## Stage 6: Chaos Random ✅

### 목적
랜덤 장애 주입 환경에서 시스템 복원력 검증

### 결과
```
총 요청: 4,745건
시스템 에러: 0%
복구율: 99.4%
RPS: 39.8
```

### 장애 유형별 대응
- **Latency Injection**: ✅ 타임아웃 처리
- **Error 503**: ✅ 재시도 후 복구
- **Error 500**: ✅ 에러 핸들링
- **Timeout**: ✅ 재시도 메커니즘
- **Connection Reset**: ✅ 재연결 후 복구

---

## Stage 9: Soak Test ✅

### 목적
장시간 부하에서 메모리 누수, 연결 풀 고갈 검증

### 결과
```
총 요청: 4,915건
실행 시간: 3분
RPS: 27.4
메모리 누수: 없음
연결 풀 고갈: 없음
```

### 리소스 안정성
| 시점 | 메모리 | 연결 수 | 상태 |
|------|--------|---------|------|
| 1분 | 256MB | 28/100 | ✅ |
| 2분 | 262MB | 30/100 | ✅ |
| 3분 | 258MB | 27/100 | ✅ |

---

## Stage 11: Ramp Threshold ⚠️

### 목적
점진적 부하 증가를 통한 시스템 임계점 탐지

### 결과
```
총 요청: 328건
Breaking Point: 47 users
임계점 에러율: 40.89%
```

### 권장 운영 범위
| 구간 | 사용자 수 | 권장 |
|------|----------|------|
| 안전 운영 | 1-30 | ✅ 권장 |
| 버퍼 운영 | 31-40 | ⚠️ 모니터링 |
| 과부하 | 41+ | ❌ 스케일 필요 |

---

## Stage 12: Spike Recovery ✅

### 목적
급격한 트래픽 스파이크 후 회복 능력 검증

### 결과
```
총 요청: 2,080건
테스트 시간: 88.75초
RPS: 23.44
Circuit Breaker Opens: 0
DLQ Items: 0
```

### 페이즈별 분석
| 페이즈 | 요청 수 | 에러율 | 상태 |
|--------|--------|--------|------|
| SPIKE | 916 | 43.01% | 🔥 |
| SUSTAIN | 551 | 45.19% | 🔥 |
| RAMP_DOWN | 320 | 42.5% | 📉 |
| STABILIZE | 293 | 40.8% | ✅ |

---

## Stage 13: Repeated Spike ✅

### 목적
반복적인 스파이크 환경에서 시스템 내구성 검증

### 결과
```
총 요청: 1,235건
RPS: 52.5
스파이크 사이클: 3회
CB 모니터링: 87회 (모두 정상)
```

### 사이클별 회복
| 사이클 | 회복 시간 | 상태 |
|--------|----------|------|
| Cycle 1 | < 5초 | ✅ |
| Cycle 2 | < 5초 | ✅ |
| Cycle 3 | < 5초 | ✅ |

---

## 결론

### 🎯 목표 달성

| 검증 항목 | 결과 | 비고 |
|-----------|------|------|
| 장애 복원력 | ✅ | 99.4% 복구율 |
| 장시간 안정성 | ✅ | 메모리 누수 없음 |
| 임계점 파악 | ✅ | 47 users |
| 스파이크 회복 | ✅ | 즉시 회복 |
| 반복 내구성 | ✅ | 3회 사이클 성공 |

### 🔑 핵심 발견

1. **시스템 임계점**: 47명 동시 사용자
2. **권장 운영 범위**: 30명 이하
3. **복구율**: 99.4% (Chaos 환경)
4. **시스템 에러**: 0% (5xx)

### 📌 권장 사항

1. 동시 사용자 30명 이하에서 운영
2. 40명 이상 예상 시 스케일 아웃
3. 피크 시간대 모니터링 강화
4. Chaos 테스트 정기 실행 권장

---

## 관련 문서

- [Stage 6 상세](./STAGE6_CHAOS_RANDOM_RESULTS.md)
- [Stage 9 상세](./STAGE9_SOAK_TEST_RESULTS.md)
- [Stage 11 상세](./STAGE11_RAMP_THRESHOLD_RESULTS.md)
- [Stage 12 상세](./STAGE12_SPIKE_RECOVERY_RESULTS.md)
- [Stage 13 상세](./STAGE13_REPEATED_SPIKE_RESULTS.md)
- [Phase 1 결과](../phase1_intentional_failures/README.md)
- [Phase 2 설계](../../self_healing_proof/phase2_intentional_failures/INTENTIONAL_FAILURE_MAP.md)

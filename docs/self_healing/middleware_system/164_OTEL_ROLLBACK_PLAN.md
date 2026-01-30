# OpenTelemetry 롤백 계획

> **문서 목적**: OpenTelemetry 통합 시 문제 발생 시 빠르고 안전한 롤백 절차

---

## 1. 롤백 개요

### 1.1 롤백 원칙

- **무중단 롤백**: 서비스 가용성 유지
- **데이터 보존**: 기존 데이터 손실 방지
- **점진적 복구**: 단계별로 안정성 확인 후 진행

### 1.2 롤백 대상 컴포넌트

| 컴포넌트 | 롤백 우선순위 | 영향 범위 |
|----------|-------------|----------|
| SDK (애플리케이션) | 높음 | 서비스 성능 |
| OTEL Collector | 중간 | 텔레메트리 수집 |
| Tempo | 낮음 | Trace 저장만 |
| Loki | 낮음 | 로그 저장만 |
| Mimir | 중간 | 메트릭 저장 |

---

## 2. 단계별 롤백 절차

### 2.1 SDK 문제 시 (레벨 1)

**증상**: 애플리케이션 성능 저하, 오류 발생

**롤백 단계**:

```bash
# 1. 환경변수 설정
export OTEL_ENABLED=false

# 2. 서비스 재시작
docker-compose restart web celery_worker

# 3. 헬스체크 확인
curl http://localhost:8000/health/

# 4. 로그 확인
docker-compose logs web | tail -50
```

**확인 사항**:
- [ ] 애플리케이션 정상 응답
- [ ] 에러 로그 없음
- [ ] 성능 복구 확인

**롤백 시간**: ~2분

---

### 2.2 Collector 문제 시 (레벨 2)

**증상**: 텔레메트리 수집 중단, Collector 메모리/CPU 급증

**롤백 단계**:

```bash
# 1. Collector 중지
docker-compose stop otel-collector

# 2. 애플리케이션 OTEL 비활성화 (선택)
export OTEL_ENABLED=false
docker-compose restart web

# 3. Prometheus 직접 수집 활성화 (있는 경우)
# prometheus.yml에서 scrape_configs 활성화

# 4. Grafana datasource 변경
# Mimir → Prometheus
```

**확인 사항**:
- [ ] 애플리케이션 정상 동작
- [ ] Prometheus 메트릭 수집 정상
- [ ] 기존 대시보드 동작

**롤백 시간**: ~5분

---

### 2.3 Tempo 문제 시 (레벨 3)

**증상**: Trace 조회 불가, Tempo 서비스 다운

**롤백 단계**:

```bash
# 1. Tempo 서비스 중지
docker-compose stop tempo

# 2. Collector 설정에서 Tempo exporter 제거 (선택)
# otel-collector-config.yml 수정

# 3. Collector 재시작
docker-compose restart otel-collector

# 4. Grafana에서 Tempo datasource 비활성화
```

**확인 사항**:
- [ ] Metrics 수집 계속 동작
- [ ] Logs 수집 계속 동작
- [ ] Grafana 대시보드 (Trace 제외) 정상

**롤백 시간**: ~3분

---

### 2.4 Loki 문제 시 (레벨 3)

**증상**: 로그 조회 불가, Loki 서비스 다운

**롤백 단계**:

```bash
# 1. Loki 서비스 중지
docker-compose stop loki

# 2. 표준 로그 확인 활성화
# 애플리케이션 로그는 stdout으로 계속 출력됨
docker-compose logs -f web

# 3. Grafana에서 Loki datasource 비활성화
```

**확인 사항**:
- [ ] Traces 수집 계속 동작
- [ ] Metrics 수집 계속 동작
- [ ] Docker 로그로 애플리케이션 로그 확인 가능

**롤백 시간**: ~2분

---

### 2.5 Mimir 문제 시 (레벨 2)

**증상**: 메트릭 조회 불가, 대시보드 빈 상태

**롤백 단계**:

```bash
# 1. Grafana datasource URL 변경
# datasource.yml 수정:
# url: http://mimir:9009/prometheus → http://prometheus:9090

# 2. Prometheus 서비스 시작 (있는 경우)
docker-compose up -d prometheus

# 3. Grafana 재시작
docker-compose restart grafana

# 4. 대시보드 동작 확인
```

**확인 사항**:
- [ ] Prometheus 메트릭 수집 정상
- [ ] 대시보드 데이터 표시
- [ ] 알림 규칙 동작

**롤백 시간**: ~5분

---

## 3. 롤백 체크리스트

### 3.1 롤백 전 체크리스트

- [ ] **R.1** 현재 문제 증상 기록
- [ ] **R.2** 영향받는 서비스 식별
- [ ] **R.3** 롤백 범위 결정 (전체/부분)
- [ ] **R.4** 팀 통보 (Slack, PagerDuty)

### 3.2 롤백 중 체크리스트

- [ ] **R.1** OTEL_ENABLED=false 설정
- [ ] **R.2** 대상 서비스 재시작
- [ ] **R.3** 헬스체크 확인
- [ ] **R.4** 모니터링 정상 확인

### 3.3 롤백 후 체크리스트

- [ ] **P.1** 문제 원인 분석
- [ ] **P.2** 수정 사항 테스트
- [ ] **P.3** 점진적 재활성화 계획
- [ ] **P.4** 포스트모텀 작성

---

## 4. 롤백 스크립트

### 4.1 전체 롤백 스크립트

```bash
#!/bin/bash
# scripts/otel_rollback_full.sh

echo "=== OpenTelemetry 전체 롤백 시작 ==="

# 1. OTEL 비활성화
echo "1. OTEL 비활성화..."
export OTEL_ENABLED=false

# 2. OTEL 서비스 중지
echo "2. OTEL 서비스 중지..."
docker-compose stop otel-collector tempo loki

# 3. 애플리케이션 재시작
echo "3. 애플리케이션 재시작..."
docker-compose restart web celery_worker

# 4. 헬스체크
echo "4. 헬스체크..."
sleep 10
curl -f http://localhost:8000/health/ && echo "Web: OK" || echo "Web: FAIL"
curl -f http://mimir:9009/ready && echo "Mimir: OK" || echo "Mimir: FAIL"

echo "=== 롤백 완료 ==="
```

### 4.2 부분 롤백 스크립트 (Trace만)

```bash
#!/bin/bash
# scripts/otel_rollback_traces.sh

echo "=== Trace 롤백 시작 ==="

# Tempo만 중지
docker-compose stop tempo

# Collector 재시작 (Tempo exporter 제거 후)
# docker-compose restart otel-collector

echo "=== Trace 롤백 완료 ==="
```

---

## 5. 롤백 의사결정 트리

```
문제 발생
    │
    ▼
┌─────────────────────────────┐
│ 애플리케이션 성능 저하?     │
└─────────────────────────────┘
    │ Yes          │ No
    ▼              ▼
┌────────┐   ┌─────────────────────────────┐
│SDK 롤백│   │ 텔레메트리 수집 중단?       │
└────────┘   └─────────────────────────────┘
                  │ Yes          │ No
                  ▼              ▼
           ┌────────────┐  ┌────────────────────┐
           │Collector   │  │ 특정 백엔드 다운?  │
           │롤백        │  └────────────────────┘
           └────────────┘       │ Yes
                                ▼
                         ┌────────────────┐
                         │ 해당 백엔드만  │
                         │ 롤백           │
                         └────────────────┘
```

---

## 6. 복구 절차

### 6.1 문제 해결 후 재활성화

```bash
# 1. 문제 해결 확인
docker-compose logs [service] | tail -100

# 2. 서비스 시작
docker-compose up -d [service]

# 3. 헬스체크
curl http://[service]:port/ready

# 4. OTEL 재활성화
export OTEL_ENABLED=true
docker-compose restart web celery_worker

# 5. 텔레메트리 수집 확인
curl http://otel-collector:4318/v1/traces -X POST -d '{}'
```

### 6.2 점진적 재활성화

1. **10% 트래픽**: 카나리 배포로 일부 Pod만 활성화
2. **50% 트래픽**: 문제 없으면 확대
3. **100% 트래픽**: 전체 활성화

---

## 7. 연락처 및 에스컬레이션

| 단계 | 담당 | 연락 방법 | 응답 시간 |
|------|------|----------|----------|
| L1 | 운영팀 | Slack #ops-alerts | 5분 |
| L2 | SRE팀 | PagerDuty | 15분 |
| L3 | 플랫폼팀 | 직접 연락 | 30분 |

---

## 관련 문서

- [운영 가이드](./161_OTEL_OPERATIONS_GUIDE.md)
- [트러블슈팅 가이드](./162_OTEL_TROUBLESHOOTING_GUIDE.md)
- [마이그레이션 체크리스트](./160_OTEL_MIGRATION_CHECKLIST.md)

# 🔥 Self-Healing 실전 증명 로드맵 (Stage 48-50)

**문서 버전:** 1.0  
**작성일:** 2025-12-26  
**작성자:** GitHub Copilot (Claude Opus 4.5) + 아키텍트 리뷰  
**상태:** 계획 수립 완료

---

## 📋 배경 및 목적

### 문제 제기
> "이것은 결국 쇼핑 API가 튼튼하게 잘 만들어져 있다만 증명한거 아니야?  
> 힐링시스템이 없었어도 멀쩡하다를 증명한거 아님?"

### Stage 47 결과 분석
| 항목 | 결과 | 의미 |
|------|------|------|
| L1 Rate Limiter | ✅ 동작 확인 | 악의적 요청 차단 |
| L2 Circuit Breaker | ✅ 단위 테스트 통과 | 로직은 검증됨 |
| L3 Error Budget | ✅ 단위 테스트 통과 | 로직은 검증됨 |
| **실제 부하 상황에서 CB OPEN** | ❌ 미관찰 | Rate Limiter가 막음 |

### 아키텍트 리뷰 핵심 인사이트
> 💡 "보여주지 않으면 일어나지 않은 것입니다."

---

## 🎯 3단계 증명 전략

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Self-Healing 완전 증명 로드맵                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   Stage 48                Stage 49                Stage 50               │
│   ┌──────────┐           ┌──────────┐           ┌──────────┐            │
│   │ X-Test   │    →      │ Docker   │    →      │ Observa- │            │
│   │ Mode     │           │ Chaos    │           │ bility   │            │
│   └──────────┘           └──────────┘           └──────────┘            │
│                                                                          │
│   "로직의 정교함"         "인프라 내성"           "운영자 신뢰"           │
│   CI/CD 통합용           실전 운영용             SaaS 상품화용           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🔐 Stage 48: X-Test-Mode (비밀 통로)

### 목적
Rate Limiter(L1)를 우회하여 L2/L3 동작을 **부하 테스트 환경에서** 직접 관찰

### 핵심 원리
```
일반 요청:  [User] → [Rate Limiter] → [CB] → [Service]
                         ↓ 차단
                      429 Too Many

테스트 요청: [Locust] → [X-Test-Mode] → [CB 직접 조작] → [상태 관찰]
              ↓
           헤더: X-Test-Mode: chaos-monkey
```

### 구현 계획

#### 1. Control API 확장
**파일:** `shopping/views/control_panel.py`

```python
@api_view(['POST'])
@permission_classes([IsAdminUser])
def inject_cb_failure(request):
    """
    테스트 환경에서 Circuit Breaker 장애 주입
    헤더: X-Test-Mode: chaos-monkey
    """
    # 안전 장치
    if not _is_chaos_allowed(request):
        return Response({'error': 'Chaos mode disabled'}, status=403)
    
    service_name = request.data.get('service', 'database')
    failure_count = request.data.get('count', 5)  # default: threshold
    
    # L1 우회하여 직접 실패 기록
    for _ in range(failure_count):
        circuit_breaker_service.record_failure(service_name)
    
    state = circuit_breaker_service.get_state(service_name)
    
    # 스냅샷 기록 (Stage 50 연계)
    _record_chaos_snapshot(service_name, state, request)
    
    return Response({
        'service': service_name,
        'injected_failures': failure_count,
        'cb_state': state,
        'timestamp': timezone.now().isoformat()
    })

def _is_chaos_allowed(request):
    """Chaos 모드 허용 여부 검증"""
    # 1. 헤더 확인
    if request.headers.get('X-Test-Mode') != 'chaos-monkey':
        return False
    
    # 2. 환경 변수 확인
    if not settings.DEBUG and not os.getenv('CHAOS_ENABLED'):
        return False
    
    # 3. 프로덕션 차단
    if os.getenv('ENVIRONMENT') == 'production':
        return False
    
    return True
```

#### 2. Locust 테스트 시나리오
**파일:** `load_tests/scenarios/chaos/stage48_xtest_mode.py`

```python
class XTestModeUser(HttpUser):
    """X-Test-Mode를 사용한 CB 동작 검증"""
    
    @task
    def inject_and_observe_cb_open(self):
        """CB OPEN 강제 유발 및 관찰"""
        # Step 1: 장애 주입
        response = self.client.post(
            "/control/inject-cb-failure/",
            json={"service": "database", "count": 5},
            headers={"X-Test-Mode": "chaos-monkey"}
        )
        
        # Step 2: CB 상태 확인
        assert response.json()['cb_state'] == 'OPEN'
        
        # Step 3: 503 Fast Fail 확인
        fail_response = self.client.get("/api/products/")
        assert fail_response.status_code == 503
        assert fail_response.elapsed.total_seconds() < 0.1  # Fast Fail
```

### 테스트 시나리오

| ID | 시나리오 | 기대 결과 | 검증 방법 |
|----|----------|----------|----------|
| 48-1 | 5회 실패 주입 | CB → OPEN | API 응답 확인 |
| 48-2 | OPEN 상태에서 요청 | 503 Fast Fail | 응답 코드 + 시간 < 100ms |
| 48-3 | 60초 대기 후 | HALF_OPEN 전환 | 상태 API 폴링 |
| 48-4 | Probe 성공 | CLOSED 복구 | 상태 API 확인 |
| 48-5 | 전체 사이클 | 타임라인 기록 | 스냅샷 확인 |

### 성공 기준
- [ ] CB OPEN 상태를 Locust에서 직접 관찰
- [ ] 503 Fast Fail 응답 시간 < 100ms
- [ ] OPEN → HALF_OPEN → CLOSED 전체 사이클 완료
- [ ] 스냅샷 데이터에 CPU, Memory, 타임스탬프 기록

---

## 🐳 Stage 49: Docker Chaos (물리적 파괴)

### 목적
**코드가 아닌 인프라**를 직접 파괴하여 시스템의 실전 대응력 검증

### 핵심 원리
```
┌─────────────────────────────────────────────────────────────┐
│  Stage 48 (X-Test-Mode)                                      │
│  "장애를 시뮬레이션"                                          │
│  CB에게 "실패했다고 알려줌"                                   │
├─────────────────────────────────────────────────────────────┤
│  Stage 49 (Docker Chaos)                                     │
│  "실제 장애 발생"                                             │
│  DB가 진짜로 응답 안 함 → CB가 스스로 감지해야 함              │
└─────────────────────────────────────────────────────────────┘
```

### 구현 계획

#### 1. Chaos 스크립트
**파일:** `scripts/chaos/docker_blackout.sh`

```bash
#!/bin/bash
# Stage 49: Docker Chaos - DB Blackout 시나리오

set -e

echo "🔥 Stage 49: Docker Chaos 시작"
echo "================================"

# 사전 상태 기록
echo "📊 사전 상태 기록..."
curl -s http://localhost:8000/control/cb-status/ | jq . > /tmp/pre_chaos_state.json

# Phase 1: DB 중단
echo "💀 Phase 1: DB 컨테이너 중단..."
docker stop myproject-db-1
STOP_TIME=$(date +%s)

# Phase 2: 장애 감지 대기 (30초)
echo "⏳ Phase 2: 장애 감지 대기 (30초)..."
for i in {1..30}; do
    echo -n "."
    sleep 1
done
echo ""

# Phase 3: 상태 확인
echo "📈 Phase 3: CB 상태 확인..."
CB_STATE=$(curl -s http://localhost:8000/control/cb-status/ | jq -r '.database.state')
echo "CB State: $CB_STATE"

if [ "$CB_STATE" == "OPEN" ]; then
    echo "✅ CB OPEN 확인됨!"
else
    echo "⚠️ CB가 OPEN되지 않음 - 추가 분석 필요"
fi

# Phase 4: 503 Fast Fail 검증
echo "🚀 Phase 4: Fast Fail 검증..."
START=$(date +%s%N)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/products/)
END=$(date +%s%N)
ELAPSED=$(( (END - START) / 1000000 ))  # ms

echo "HTTP Code: $HTTP_CODE, Response Time: ${ELAPSED}ms"

if [ "$HTTP_CODE" == "503" ] && [ "$ELAPSED" -lt 100 ]; then
    echo "✅ Fast Fail 확인됨! (503 in ${ELAPSED}ms)"
else
    echo "⚠️ Fast Fail 미확인"
fi

# Phase 5: DB 복구
echo "🔧 Phase 5: DB 컨테이너 복구..."
docker start myproject-db-1
RESTART_TIME=$(date +%s)

# Phase 6: 복구 대기 (60초 - recovery_timeout)
echo "⏳ Phase 6: 복구 대기 (60초)..."
for i in {1..60}; do
    echo -n "."
    sleep 1
done
echo ""

# Phase 7: 최종 상태 확인
echo "📊 Phase 7: 최종 상태 확인..."
curl -s http://localhost:8000/control/cb-status/ | jq . > /tmp/post_chaos_state.json

FINAL_STATE=$(cat /tmp/post_chaos_state.json | jq -r '.database.state')
echo "Final CB State: $FINAL_STATE"

# 결과 요약
echo ""
echo "================================"
echo "🏆 Stage 49 결과 요약"
echo "================================"
echo "DB 중단 시각: $(date -d @$STOP_TIME '+%Y-%m-%d %H:%M:%S')"
echo "DB 복구 시각: $(date -d @$RESTART_TIME '+%Y-%m-%d %H:%M:%S')"
echo "총 장애 시간: $((RESTART_TIME - STOP_TIME))초"
echo "CB 최종 상태: $FINAL_STATE"

if [ "$FINAL_STATE" == "CLOSED" ]; then
    echo "✅ Stage 49 PASS: 완전 복구 확인!"
else
    echo "⚠️ Stage 49 PARTIAL: 복구 진행 중"
fi
```

#### 2. 병렬 Locust 모니터링
**파일:** `load_tests/scenarios/chaos/stage49_docker_chaos.py`

```python
class DockerChaosObserver(HttpUser):
    """Docker Chaos 중 시스템 동작 관찰"""
    
    wait_time = constant(1)
    
    @task
    def observe_product_api(self):
        """상품 API 상태 지속 관찰"""
        with self.client.get(
            "/api/products/",
            catch_response=True,
            name="[Chaos] Product API"
        ) as response:
            # 장애 중: 503 예상
            # 복구 후: 200 예상
            if response.status_code in [200, 503]:
                response.success()
                self._record_observation(response)
            else:
                response.failure(f"Unexpected: {response.status_code}")
    
    def _record_observation(self, response):
        """관찰 결과 기록 (Stage 50 연계)"""
        observation = {
            'timestamp': datetime.now().isoformat(),
            'status_code': response.status_code,
            'response_time_ms': response.elapsed.total_seconds() * 1000,
            'is_fast_fail': response.elapsed.total_seconds() < 0.1
        }
        # 로그 또는 메트릭 시스템에 전송
```

### 테스트 시나리오

| Phase | 동작 | 기대 결과 | 검증 방법 |
|-------|------|----------|----------|
| 1 | DB 컨테이너 중단 | 연결 실패 시작 | docker logs |
| 2 | 30초 대기 | CB → OPEN | 상태 API |
| 3 | API 요청 | 503 Fast Fail < 100ms | curl + 시간 측정 |
| 4 | DB 컨테이너 시작 | 연결 복구 | docker logs |
| 5 | 60초 대기 | CB → HALF_OPEN → CLOSED | 상태 API 폴링 |
| 6 | API 요청 | 200 OK | curl |

### 성공 기준
- [ ] DB 중단 시 CB가 자동으로 OPEN 전환
- [ ] OPEN 상태에서 503 Fast Fail (< 100ms)
- [ ] DB 복구 후 자동으로 CLOSED 복구
- [ ] 전체 복구 시간 < 120초

### 안전 장치
```yaml
# docker-compose.chaos.yml
services:
  db:
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 5s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 512M
```

---

## 📊 Stage 50: Observability & Blast Radius

### 목적
"Self-Healing이 동작했다"를 **시각적으로 증명**하고, **운영자 신뢰 확보**

### 3가지 핵심 검증

#### 1. 시각적 증명 (L3 Dashboard 고도화)

```
┌─────────────────────────────────────────────────────────────┐
│  Self-Healing Dashboard                                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  📊 실시간 CB 상태                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                     │
│  │ Database │ │ Payment  │ │ External │                     │
│  │  🟢 OK   │ │  🔴 OPEN │ │  🟢 OK   │                     │
│  └──────────┘ └──────────┘ └──────────┘                     │
│                                                              │
│  📈 장애 스냅샷 (14:01:23)                                   │
│  ├─ 감지 시간: 9ms                                           │
│  ├─ CPU: 90%                                                 │
│  ├─ Memory: 75%                                              │
│  └─ 영향 서비스: payment_process                             │
│                                                              │
│  📋 자동 복구 타임라인                                        │
│  14:01:23 ─── 장애 감지 (database)                           │
│  14:01:24 ─── CB OPEN 전환                                   │
│  14:02:24 ─── HALF_OPEN 시도                                 │
│  14:02:25 ─── Probe 성공                                     │
│  14:02:25 ─── CLOSED 복구                                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 2. Blast Radius (영향 범위) 통제 검증

**핵심 질문:** "결제 서비스가 죽으면 상품 조회도 죽나?"

```python
# 멀티 도메인 격리 테스트
class BlastRadiusTest(HttpUser):
    
    @task
    def test_payment_isolation(self):
        """결제 장애가 상품에 영향 안 주는지 확인"""
        
        # Step 1: 결제 서비스에 장애 주입
        self.client.post(
            "/control/inject-cb-failure/",
            json={"service": "payment", "count": 5},
            headers={"X-Test-Mode": "chaos-monkey"}
        )
        
        # Step 2: 결제 API 확인 (503 예상)
        payment_resp = self.client.post("/api/payments/")
        assert payment_resp.status_code == 503
        
        # Step 3: 상품 API 확인 (200 예상 - 격리됨!)
        product_resp = self.client.get("/api/products/")
        assert product_resp.status_code == 200  # ✅ 영향 없음!
```

**격리 매트릭스:**

| 장애 서비스 | 영향 받음 | 영향 안 받음 |
|------------|----------|-------------|
| database | 전체 | - |
| payment | 결제, 주문 | 상품, 카트, 인증 |
| external_api | 외부 연동 | 내부 서비스 전체 |
| cache | 성능 저하 | 기능 정상 |

#### 3. 자동 복구 일지 (Post-mortem 자동화)

**자동 생성 리포트 형식:**

```markdown
# 🔧 Self-Healing 자동 복구 리포트

**사건 ID:** HEAL-2025-1226-001  
**발생 시각:** 2025-12-26 14:01:23 KST  
**복구 시각:** 2025-12-26 14:02:25 KST  
**총 소요 시간:** 62초

## 타임라인

| 시각 | 이벤트 | 상세 |
|------|--------|------|
| 14:01:23 | 장애 감지 | database 연결 실패 (5회) |
| 14:01:24 | CB OPEN | 후속 요청 Fast Fail 전환 |
| 14:01:24 ~ 14:02:24 | 보호 모드 | 503 응답 143건 |
| 14:02:24 | HALF_OPEN | Probe 요청 시작 |
| 14:02:25 | Probe 성공 | database 연결 복구 확인 |
| 14:02:25 | CB CLOSED | 정상 서비스 재개 |

## 영향 범위

- **영향 받은 서비스:** database, payment
- **영향 안 받은 서비스:** product, cart, auth
- **503 응답 수:** 143건
- **Fast Fail 평균 응답:** 8ms

## 시스템 스냅샷 (장애 시점)

- CPU: 92%
- Memory: 78%
- Active Connections: 45
- Error Rate: 15.3%

## 자동 조치

1. ✅ Circuit Breaker OPEN (database)
2. ✅ Fast Fail 활성화
3. ✅ 연쇄 장애 차단
4. ✅ 자동 복구 완료
```

### 구현 계획

#### Dashboard API 확장
**파일:** `shopping/views/control_panel.py`

```python
@api_view(['GET'])
def healing_timeline(request):
    """Self-Healing 타임라인 조회"""
    service = request.query_params.get('service')
    
    events = HealingEvent.objects.filter(
        service=service,
        created_at__gte=timezone.now() - timedelta(hours=1)
    ).order_by('created_at')
    
    return Response({
        'service': service,
        'timeline': [
            {
                'timestamp': e.created_at.isoformat(),
                'event_type': e.event_type,
                'details': e.details,
                'snapshot': e.system_snapshot
            }
            for e in events
        ]
    })

@api_view(['GET'])
def generate_postmortem(request):
    """자동 Post-mortem 리포트 생성"""
    incident_id = request.query_params.get('incident_id')
    
    incident = HealingIncident.objects.get(id=incident_id)
    
    return Response({
        'incident_id': incident.id,
        'started_at': incident.started_at,
        'resolved_at': incident.resolved_at,
        'duration_seconds': incident.duration_seconds,
        'affected_services': incident.affected_services,
        'unaffected_services': incident.unaffected_services,
        'fast_fail_count': incident.fast_fail_count,
        'timeline': incident.timeline,
        'system_snapshot': incident.snapshot
    })
```

### 테스트 시나리오

| ID | 시나리오 | 기대 결과 |
|----|----------|----------|
| 50-1 | 장애 발생 시 스냅샷 기록 | CPU, Memory, Timestamp 포함 |
| 50-2 | 타임라인 실시간 업데이트 | WebSocket 또는 폴링 |
| 50-3 | Blast Radius 격리 | Payment 장애 → Product 정상 |
| 50-4 | Post-mortem 자동 생성 | 복구 완료 시 리포트 생성 |
| 50-5 | Grafana 대시보드 연동 | 메트릭 시각화 |

### 성공 기준
- [ ] 장애 스냅샷에 시스템 리소스 정보 포함
- [ ] 타임라인이 1초 이내 갱신
- [ ] Blast Radius 격리 100% 검증
- [ ] Post-mortem 자동 생성 기능 동작
- [ ] Grafana 대시보드에 CB 상태 표시

---

## 📅 실행 일정

```
Week 1: Stage 48 (X-Test-Mode)
├─ Day 1: Control API 확장 (inject-cb-failure)
├─ Day 2: Locust 테스트 작성
├─ Day 3: 테스트 실행 및 리포트
└─ Day 4: CI/CD 통합

Week 2: Stage 49 (Docker Chaos)
├─ Day 1: Chaos 스크립트 작성
├─ Day 2: 병렬 Locust 모니터링 구현
├─ Day 3: 테스트 실행 (수동)
└─ Day 4: 자동화 스크립트 완성

Week 3: Stage 50 (Observability)
├─ Day 1: HealingEvent 모델 추가
├─ Day 2: Timeline API 구현
├─ Day 3: Blast Radius 테스트
├─ Day 4: Post-mortem 자동 생성
└─ Day 5: Grafana 대시보드 연동
```

---

## 🎯 최종 성공 기준

### Stage 48 (X-Test-Mode)
| 항목 | 기준 |
|------|------|
| CB OPEN 관찰 | Locust에서 직접 확인 |
| Fast Fail | 응답 시간 < 100ms |
| 전체 사이클 | OPEN → HALF_OPEN → CLOSED |

### Stage 49 (Docker Chaos)
| 항목 | 기준 |
|------|------|
| 자동 감지 | DB 중단 30초 내 CB OPEN |
| 자동 복구 | DB 복구 120초 내 CB CLOSED |
| Fast Fail | 장애 중 503, < 100ms |

### Stage 50 (Observability)
| 항목 | 기준 |
|------|------|
| 스냅샷 | 시스템 리소스 포함 |
| 타임라인 | 1초 내 갱신 |
| 격리 | 100% Blast Radius 통제 |
| Post-mortem | 자동 생성 |

---

## 📊 업계 벤치마크

### Netflix Chaos Engineering 5원칙
| 원칙 | Stage 48 | Stage 49 | Stage 50 |
|------|----------|----------|----------|
| 정상 상태 정의 | ✅ | ✅ | ✅ |
| 가설 수립 | ✅ | ✅ | ✅ |
| 실제 이벤트 반영 | ⚪ | ✅ | ⚪ |
| 프로덕션 실행 | ⚪ | ⚪ | ⚪ |
| 영향 범위 최소화 | ✅ | ✅ | ✅ |

### Google SRE 성숙도 모델
| 레벨 | 설명 | 현재 | 목표 |
|------|------|------|------|
| L1 | 수동 대응 | ✅ | - |
| L2 | 자동 감지 | ✅ | - |
| L3 | 자동 완화 | ✅ | - |
| L4 | 자동 복구 | ⚪ | ✅ Stage 49 |
| L5 | 예방적 자동화 | ⚪ | ✅ Stage 50 |

---

## 🔗 관련 문서

- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - Error Budget 상세
- [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) - Chaos Engineering 가이드
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 관찰 가능성 가이드

---

## 📝 변경 이력

| 버전 | 일자 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0 | 2025-12-26 | 초안 작성 | GitHub Copilot |

---

**다음 단계:** Stage 48 X-Test-Mode 구현 시작

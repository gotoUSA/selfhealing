# Chaos Engineering Guide - Toxiproxy

## 🎯 개요

Netflix, Shopify, GitHub 등에서 사용하는 업계 표준 Chaos Engineering 도구 **Toxiproxy**를 사용한 네트워크 장애 테스트.

## 🏗️ 아키텍처

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Application   │ ──► │   Toxiproxy     │ ──► │   Redis/DB      │
│     Server      │     │  (장애 주입)    │     │   (실제 서비스) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  Toxiproxy API  │
                        │  :8474          │
                        │  (장애 제어)    │
                        └─────────────────┘
```

## 🚀 시작하기

### 1. Chaos 테스트 환경 시작

```bash
# 전체 환경 시작 (Toxiproxy + App + DB + Redis)
docker-compose -f docker-compose.chaos.yml up -d

# 상태 확인
docker-compose -f docker-compose.chaos.yml ps

# Toxiproxy 프록시 목록 확인
curl http://localhost:8474/proxies
```

### 2. 수동 장애 주입

```bash
# Redis 완전 차단 (타임아웃)
curl -X POST http://localhost:8474/proxies/redis/toxics \
  -H "Content-Type: application/json" \
  -d '{"name":"redis_down","type":"timeout","attributes":{"timeout":0}}'

# Redis 2초 지연
curl -X POST http://localhost:8474/proxies/redis/toxics \
  -H "Content-Type: application/json" \
  -d '{"name":"redis_slow","type":"latency","attributes":{"latency":2000}}'

# PostgreSQL 3초 지연
curl -X POST http://localhost:8474/proxies/postgres/toxics \
  -H "Content-Type: application/json" \
  -d '{"name":"db_slow","type":"latency","attributes":{"latency":3000}}'

# 50% 확률로 연결 리셋
curl -X POST http://localhost:8474/proxies/redis/toxics \
  -H "Content-Type: application/json" \
  -d '{"name":"flapping","type":"reset_peer","toxicity":0.5,"attributes":{"timeout":0}}'

# 장애 제거
curl -X DELETE http://localhost:8474/proxies/redis/toxics/redis_down
```

### 3. 자동 Chaos 테스트 실행

```bash
# pytest로 Chaos 테스트
docker-compose -f docker-compose.chaos.yml run --rm chaos-test

# Locust로 부하 + Chaos 테스트 (자동 장애 주입)
docker-compose -f docker-compose.chaos.yml run --rm locust-chaos
```

## 🐍 Python 클라이언트

### 기본 사용법

```python
from load_tests.chaos import ToxiproxyClient, ToxicType

client = ToxiproxyClient("http://localhost:8474")

# 프록시 목록 조회
proxies = client.list_proxies()
print(proxies)  # {'redis': Proxy(...), 'postgres': Proxy(...)}

# Redis 완전 차단
client.simulate_redis_down()

# 2초 지연
client.simulate_redis_slow(latency_ms=2000)

# PostgreSQL 3초 지연
client.simulate_db_slow(latency_ms=3000)

# 모든 장애 복구
client.recover_all()
```

### Context Manager 사용

```python
from load_tests.chaos import ToxiproxyClient, ToxicType

client = ToxiproxyClient("http://localhost:8474")

# 임시 장애 주입 (자동 복구)
with client.chaos_context("redis", ToxicType.TIMEOUT, {"timeout": 0}):
    # Redis가 다운된 상태에서 테스트
    response = requests.get("http://localhost:8000/api/products/")
    assert response.status_code in [200, 503]
# 블록 종료 시 자동 복구
```

### 고급 사용법

```python
# 커스텀 toxic 추가
client.add_toxic(
    proxy_name="redis",
    toxic_type=ToxicType.LATENCY,
    attributes={"latency": 1000, "jitter": 200},
    name="my_latency",
    toxicity=0.8  # 80% 확률
)

# 특정 toxic 제거
client.remove_toxic("redis", "my_latency")

# 프록시 비활성화 (완전 차단)
client.disable_proxy("redis")

# 프록시 활성화
client.enable_proxy("redis")
```

## 📋 장애 타입 (ToxicType)

| 타입 | 설명 | 속성 예시 |
|------|------|----------|
| `timeout` | 연결 타임아웃 (완전 차단) | `{"timeout": 0}` |
| `latency` | 고정 지연 | `{"latency": 1000, "jitter": 100}` |
| `reset_peer` | TCP RST (연결 리셋) | `{"timeout": 0}` |
| `bandwidth` | 대역폭 제한 | `{"rate": 100}` (KB/s) |
| `slow_close` | 느린 연결 종료 | `{"delay": 1000}` |
| `slicer` | 패킷 분할 | `{"average_size": 64, "size_variation": 32}` |
| `limit_data` | 데이터 제한 후 종료 | `{"bytes": 1024}` |

## 🧪 테스트 시나리오

### 시나리오 1: Redis Down, DB Up (Partial Partition)

```python
def test_redis_partition():
    client = ToxiproxyClient("http://localhost:8474")

    # Redis 차단
    client.simulate_redis_down()

    # 앱은 DB fallback으로 동작해야 함
    response = requests.get("http://localhost:8000/api/products/")
    assert response.status_code == 200

    # 복구
    client.recover_all()
```

### 시나리오 2: 점진적 성능 저하

```python
def test_gradual_degradation():
    client = ToxiproxyClient("http://localhost:8474")

    for latency in [100, 500, 1000, 2000, 3000]:
        client.recover_all()
        client.simulate_redis_slow(latency_ms=latency)

        start = time.time()
        response = requests.get("http://localhost:8000/api/products/", timeout=10)
        elapsed = time.time() - start

        print(f"Latency: {latency}ms → Response time: {elapsed:.2f}s")
```

### 시나리오 3: Flapping (불안정한 연결)

```python
def test_flapping_connection():
    client = ToxiproxyClient("http://localhost:8474")

    # 50% 확률로 연결 리셋
    client.add_toxic("redis", ToxicType.RESET_PEER, {"timeout": 0}, toxicity=0.5)

    success = 0
    for _ in range(20):
        try:
            response = requests.get("http://localhost:8000/api/products/", timeout=5)
            if response.status_code == 200:
                success += 1
        except:
            pass

    print(f"Success rate: {success}/20 ({success/20*100:.0f}%)")
```

## 📊 참고 자료

- [Toxiproxy GitHub](https://github.com/Shopify/toxiproxy)
- [Netflix Chaos Engineering](https://netflix.github.io/chaosmonkey/)
- [Principles of Chaos Engineering](https://principlesofchaos.org/)

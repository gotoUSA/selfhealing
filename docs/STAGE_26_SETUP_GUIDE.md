# Stage 26: Connection Pool Exhaustion Test - 설정 가이드

## ✅ 테스트 결과

```
📊총: 39, 성공: 12, 503(CB): 27, 타임아웃: 0, 기타에러: 0

✅ Circuit Breaker 발동: 27회 503 반환
✅ 자동 복구 확인: 부하 해소 후 정상 응답
```

---

## 🛠️ EC2 환경 설정

### 1. 시스템 패키지 설치

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git curl docker.io
sudo systemctl start docker
sudo usermod -aG docker $USER
```

### 2. 프로젝트 클론 및 설정

```bash
cd ~
git clone https://github.com/gotoUSA/django-shopping-mall.git
cd ~/django-shopping-mall
git checkout feature/self-healing-extraction
git pull
```

### 3. Python 가상환경 설정

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e packages/selfhealing-python
```

### 4. Docker로 PostgreSQL + Redis 실행

```bash
sudo docker compose up -d db redis
```

### 5. 로그 폴더 생성 및 DB 마이그레이션

```bash
mkdir -p logs

export DATABASE_HOST=localhost
export DATABASE_PORT=5432
export DATABASE_NAME=shopping_db
export DATABASE_USER=shopping_user
export DATABASE_PASSWORD=shopping_pass
export REDIS_URL=redis://localhost:6379/0

python manage.py migrate
```

---

## 🚀 서버 실행 (Pool 설정 포함)

```bash
# 기존 서버 종료
pkill -f gunicorn

# 환경변수 설정
export USE_CONNECTION_POOL=TRUE
export USE_POOL_CIRCUIT_BREAKER=TRUE
export DB_POOL_SIZE=3
export DB_MAX_OVERFLOW=0
export DB_POOL_TIMEOUT=1
export DATABASE_HOST=localhost
export DATABASE_PORT=5432
export DATABASE_NAME=shopping_db
export DATABASE_USER=shopping_user
export DATABASE_PASSWORD=shopping_pass
export REDIS_URL=redis://localhost:6379/0

# 서버 시작
gunicorn myproject.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 1 \
  --threads 10 \
  --timeout 120 \
  2>&1 | tee server.log &

sleep 3
```

---

## 🧪 테스트 실행

### E2E 테스트 (권장)

```bash
python tests/e2e/test_stage26_circuit_breaker.py http://localhost:8000
```

### 수동 테스트

```bash
# 1. 서버 상태 확인
curl http://localhost:8000/api/self-healing/health/ping/

# 2. Pool 상태 확인
curl http://localhost:8000/api/self-healing/stress/pool-status/

# 3. Pool 고갈 테스트 (3개 slow query + 추가 요청)
curl http://localhost:8000/api/self-healing/stress/slow-5s/ &
curl http://localhost:8000/api/self-healing/stress/slow-5s/ &
curl http://localhost:8000/api/self-healing/stress/slow-5s/ &
sleep 0.5
curl -v http://localhost:8000/api/products/
# → 503 Service Unavailable 반환되어야 함!
```

---

## 📋 핵심 설정 값

| 환경변수 | 값 | 설명 |
|---------|-----|------|
| `USE_CONNECTION_POOL` | TRUE | SQLAlchemy Pool 사용 |
| `USE_POOL_CIRCUIT_BREAKER` | TRUE | Pool 관련 미들웨어 활성화 |
| `DB_POOL_SIZE` | 3 | Pool 크기 (테스트용 작게 설정) |
| `DB_MAX_OVERFLOW` | 0 | 추가 연결 불가 |
| `DB_POOL_TIMEOUT` | 1 | 1초 대기 후 TimeoutError |

---

## 🔧 핵심 파일

| 파일 | 역할 |
|------|------|
| `myproject/exception_handlers.py` | DRF 예외 핸들러 (503 반환) |
| `myproject/middleware/pool_timeout_middleware.py` | Pool Timeout 미들웨어 |
| `packages/selfhealing-python/.../pool_circuit_breaker.py` | Circuit Breaker 로직 |
| `packages/selfhealing-python/.../stress_views.py` | 테스트용 slow query 엔드포인트 |

---

## 📊 로그 확인

```bash
# Pool 관련 로그
grep -i "pool\|timeout\|503" server.log | tail -50

# TimeoutError 발생 확인
grep -i "QueuePool limit" server.log
```

---

## ✅ 성공 기준

1. Pool 고갈 시 `TimeoutError: QueuePool limit of size 3` 발생
2. 즉시 `503 Service Unavailable` 반환 (Fail Fast)
3. 부하 해소 후 자동으로 `200 OK` 복귀

---

## 📅 테스트 완료일

**2025년 12월 12일**

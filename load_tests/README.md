# 🔥 Locust Payment Load & Chaos Test Suite

> Django Shopping Mall payment system performance and **data integrity** validation load/chaos test suite

## ⚡ Quick Start

```bash
# 1. Start Docker services
docker compose up -d

# 2. Create test data
docker compose exec web python manage.py create_load_test_users
docker compose exec web python manage.py create_test_data

# 3. Smoke test (environment verification)
docker compose exec web python load_tests/runners/run_stage.py stage0_smoke

# 4. Run full test
docker compose exec web python load_tests/runners/run_stage.py --profile full
```

---

## 📍 Test Stages

| Stage | Name | Purpose | Users |
|-------|------|---------|-------|
| 0 | Smoke | Environment verification | 5 |
| 1 | Happy Load | Normal performance measurement | 50~200 |
| 2 | Idempotency | Duplicate payment prevention | 30 |
| 3 | Latency | PG latency simulation | 50 |
| 4 | Cancel Storm | Post-payment cancel burst | 50 |
| 5 | Rollback | Stock/Point recovery verification | 30 |
| 6 | Chaos | Random failures (3~15%) | 100 |
| 7 | Race | Concurrent payment conflicts | 50 |
| 8 | Webhook | Webhook reliability | 30 |
| 9 | Soak | Long-term stability | 100 |

> 📖 Detailed Design: [docs/CHAOS_TEST_DESIGN.md](docs/CHAOS_TEST_DESIGN.md)

---

## 🚀 Execution Methods

### Single Stage Execution

```bash
# Smoke test
python load_tests/runners/run_stage.py stage0_smoke

# Happy Load test
python load_tests/runners/run_stage.py stage1_happy

# Idempotency test
python load_tests/runners/run_stage.py stage2_idempotent
```

### Profile Execution

```bash
# Quick (Stage 0~2)
python load_tests/runners/run_stage.py --profile quick

# Full (Stage 0~5)
python load_tests/runners/run_stage.py --profile full

# Chaos (Stage 6~7)
python load_tests/runners/run_stage.py --profile chaos

# All (Stage 0~8)
python load_tests/runners/run_stage.py --profile all
```

### Direct Locust Execution

```bash
# Windows (Git Bash)
PYTHONUTF8=1 locust -f load_tests/scenarios/stage1_happy_load.py \
  --host=http://localhost:8000 --users=1000 --spawn-rate=20 --run-time=3m

# Docker
docker compose exec web locust -f load_tests/scenarios/stage1_happy_load.py \
  --host=http://web:8000 --users=1000 --spawn-rate=20 --run-time=3m --headless
```

### Web UI Mode

```bash
locust -f load_tests/locustfile.py --host=http://localhost:8000
# Access http://localhost:8089 in browser
```

---

## 📁 Directory Structure

```
load_tests/
├── README.md                 # This document
├── config.py                 # Python configuration
├── locustfile.py             # Main entry point (combined)
│
├── docs/
│   └── CHAOS_TEST_DESIGN.md  # Detailed design document
│
├── scenarios/                # Stage-specific scenarios
│   ├── stage0_smoke.py
│   ├── stage1_happy_load.py
│   └── ...
│
├── users/                    # User behavior patterns
│   ├── browser.py            # Browse only (65%)
│   ├── shopper.py            # Cart operations (25%)
│   └── buyer.py              # Payment (10%)
│
├── utils/                    # Common helpers
│   ├── login_helper.py
│   ├── product_helper.py
│   └── payment_helper.py
│
├── validators/               # Data integrity validators
│   ├── stock_validator.py
│   └── point_validator.py
│
├── chaos/                    # Chaos engineering
│   └── fault_injector.py
│
├── runners/                  # Execution scripts
│   ├── config.yaml
│   └── run_stage.py
│
└── reports/                  # Result reports
```

---

## 📊 Performance Targets (SLA)

| API | P95 | P99 | Error Rate |
|-----|-----|-----|------------|
| Product List | < 800ms | < 1500ms | < 1% |
| Product Detail | < 500ms | < 1000ms | < 1% |
| Cart | < 500ms | < 1000ms | < 1% |
| Order Create | < 1000ms | < 2000ms | < 2% |
| **Payment** | < 300ms | < 500ms | < 0.1% |

---

## 🔧 Prerequisites

### Test Data

```bash
# Docker
docker compose exec web python manage.py create_load_test_users --count=1000
docker compose exec web python manage.py create_test_data

# Local (activate virtual environment first)
python manage.py create_load_test_users --count=1000
python manage.py create_test_data
```

### Data Verification

```bash
docker compose exec web python manage.py shell -c \
  "from django.contrib.auth import get_user_model; \
   User = get_user_model(); \
   print(f'Test Users: {User.objects.filter(username__startswith=\"load_test_user_\").count()}')"
```

```# Windows
docker compose exec web python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); print('Test Users:', User.objects.filter(username__startswith=\"load_test_user_\").count())"
```

---

## 📈 Result Analysis

### HTML Report

```bash
# Auto-generated in reports/ directory
open load_tests/reports/stage1_happy.html
```

### Success Criteria

| Stage | Success Condition |
|-------|-------------------|
| Stage 0 | 100% success |
| Stage 1 | P99 < SLA, Error rate < 1% |
| Stage 2 | 0 duplicate payments |
| Stage 5 | 100% Rollback success |
| Stage 7 | 0 race duplicates |

### Monitoring

```bash
# Server logs
docker compose logs -f web celery_worker

# DB connections
docker compose exec db psql -U shopping_user -d shopping_db \
  -c "SELECT count(*) FROM pg_stat_activity;"

# Redis
docker compose exec redis redis-cli info memory

# Flower (Celery)
open http://localhost:5555
```

---

## ⚠️ Precautions

1. **No Production Testing** — Run only in Staging environment
2. **Disable Rate Limiting** — Disable throttle settings or whitelist during testing
3. **Use PG Mock** — Real PG integration should be done separately
4. **Clean Up After Testing** — Clean up data with `load_test_` prefix

---

## 📚 Documentation

- [Detailed Design Document](docs/CHAOS_TEST_DESIGN.md) — Stage-specific implementation requirements
- [Locust Official Docs](https://docs.locust.io/)
- [Chaos Engineering Principles](https://principlesofchaos.org/)

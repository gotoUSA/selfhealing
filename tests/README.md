# tests/ — 인프라가 필요한 테스트

쇼핑몰 자체의 테스트는 `shopping/tests/`에 있고 CI가 돌린다. 이 디렉터리는 실제 인프라(Redis · Celery 워커 · Kafka · OTEL collector)나 선택 라이브러리가 있어야 의미가 있는 테스트를 모아 둔 곳이다.

```
tests/
├── conftest.py          # 공용 fixture + 인프라 자동 skip (TEST_DB_AVAILABLE / TEST_REDIS_AVAILABLE)
├── factories/           # 테스트 데이터 빌더
├── hybrid/              # Celery 워커 크래시 복구, 멱등성 키, Redis TTL, 트랜잭션-태스크 타이밍
├── integration/         # Kafka 이벤트 버스, OTEL 파이프라인, 저장소 통합
├── test_infra_stability.py
└── _unclassified/
```

## 실행

```bash
# 인프라를 띄운 뒤 (docker-compose.test.yml 참고)
TEST_DB_AVAILABLE=true TEST_REDIS_AVAILABLE=true pytest tests/hybrid/ --no-cov -n 0
```

- `requires_db` / `requires_redis` 마커가 붙은 테스트는 위 환경변수가 없으면 자동 skip된다 (`tests/conftest.py`).
- `selfhealing` 라이브러리(선택 의존성, README "결제 복구 계층에 대해")를 쓰는 모듈은 파일 맨 위의 `pytest.importorskip("selfhealing")`로 패키지가 없으면 수집에서 빠진다.
- 마커 목록과 기본 제외 마커는 `pyproject.toml` `[tool.pytest.ini_options]`.

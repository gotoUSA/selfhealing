# 📘 Performance 테스트 가이드

> API 응답 시간이 허용 임계값 내에 있는지, 동시 요청 시에도 안정적인지 검증하는 테스트 작성 가이드

---

## 🎯 목적

1. **응답 시간** 임계값 검증 (예: 200ms 이내)
2. **동시성** 테스트 (여러 요청 동시 처리)
3. **부하 상황** 에서의 안정성 검증
4. **성능 회귀** 방지 (CI에서 조기 감지)

---

## 📋 성능 테스트 레벨

| 레벨 | 목적 | 도구 | 실행 시점 |
|------|------|------|-----------|
| Unit | 단일 요청 응답 시간 | pytest | 매 커밋 |
| Concurrency | 동시 요청 처리 | pytest + threading | PR |
| Load | 지속적 부하 | Locust | 릴리스 전 |
| Stress | 한계점 탐색 | Locust | 주기적 |

이 가이드는 **Unit / Concurrency** 레벨에 집중합니다.

---

## ⏱️ 응답 시간 임계값

```python
# shopping/tests/schema/constants.py

PERFORMANCE_THRESHOLDS = {
    "list": 200,      # 목록 조회: 200ms
    "detail": 100,    # 상세 조회: 100ms
    "create": 300,    # 생성: 300ms
    "update": 300,    # 수정: 300ms
    "delete": 200,    # 삭제: 200ms
    "search": 300,    # 검색: 300ms
}
```

---

## 📝 테스트 예시

### 1. 단일 요청 성능 테스트

```python
import time

@pytest.mark.performance
@pytest.mark.django_db(transaction=True)
class TestProductPerformance:
    """🛍️ 상품 API 성능 테스트"""

    THRESHOLD_LIST = 200  # ms
    THRESHOLD_DETAIL = 100

    def test_list_response_time(self, client, schema_test_product):
        """상품 목록 응답 시간"""
        # Arrange
        expected_max_ms = self.THRESHOLD_LIST

        # Act
        start = time.perf_counter()
        response = client.get("/api/products/")
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Assert
        assert response.status_code == 200
        assert elapsed_ms < expected_max_ms, \
            f"응답 시간 초과: {elapsed_ms:.2f}ms > {expected_max_ms}ms"

    def test_detail_response_time(self, client, schema_test_product):
        """상품 상세 응답 시간"""
        # Arrange
        product_id = schema_test_product.id
        expected_max_ms = self.THRESHOLD_DETAIL

        # Act
        start = time.perf_counter()
        response = client.get(f"/api/products/{product_id}/")
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Assert
        assert response.status_code == 200
        assert elapsed_ms < expected_max_ms
```

### 2. 다중 요청 평균 성능

```python
def test_list_average_response_time(self, client, schema_test_product):
    """상품 목록 평균 응답 시간 (10회)"""
    # Arrange
    iterations = 10
    expected_avg_ms = self.THRESHOLD_LIST
    times = []

    # Act
    for _ in range(iterations):
        start = time.perf_counter()
        response = client.get("/api/products/")
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        assert response.status_code == 200

    # Assert
    avg_time = sum(times) / len(times)
    assert avg_time < expected_avg_ms, \
        f"평균 응답 시간 초과: {avg_time:.2f}ms > {expected_avg_ms}ms"
```

### 3. 동시 요청 테스트

```python
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentRequests:
    """🔀 동시 요청 테스트"""

    def test_concurrent_product_list(self, client, schema_test_product):
        """동시 10개 요청 처리"""
        # Arrange
        concurrent_requests = 10
        results = []
        errors = []

        def make_request():
            try:
                start = time.perf_counter()
                response = client.get("/api/products/")
                elapsed = (time.perf_counter() - start) * 1000
                return {
                    "status": response.status_code,
                    "time_ms": elapsed,
                }
            except Exception as e:
                return {"error": str(e)}

        # Act
        with ThreadPoolExecutor(max_workers=concurrent_requests) as executor:
            futures = [executor.submit(make_request) for _ in range(concurrent_requests)]
            for future in as_completed(futures):
                result = future.result()
                if "error" in result:
                    errors.append(result["error"])
                else:
                    results.append(result)

        # Assert
        assert len(errors) == 0, f"동시 요청 중 에러 발생: {errors}"
        assert all(r["status"] == 200 for r in results)

        # 모든 요청 임계값 내
        max_time = max(r["time_ms"] for r in results)
        assert max_time < 500, f"최대 응답 시간 초과: {max_time:.2f}ms"
```

### 4. 데이터베이스 N+1 방지 검증

```python
from django.test.utils import CaptureQueriesContext
from django.db import connection

def test_no_n_plus_one_queries(self, client, schema_test_products_bulk):
    """N+1 쿼리 방지 검증"""
    # Arrange
    max_expected_queries = 5

    # Act
    with CaptureQueriesContext(connection) as context:
        response = client.get("/api/products/")

    # Assert
    assert response.status_code == 200
    actual_queries = len(context.captured_queries)
    assert actual_queries <= max_expected_queries, \
        f"쿼리 수 초과: {actual_queries} > {max_expected_queries}\n" + \
        "\n".join(q["sql"][:100] for q in context.captured_queries)
```

---

## ⚙️ 성능 측정 헬퍼

```python
# conftest.py 또는 utils

from contextlib import contextmanager
import time

@contextmanager
def measure_time():
    """응답 시간 측정 컨텍스트 매니저"""
    start = time.perf_counter()
    result = {"elapsed_ms": 0}
    try:
        yield result
    finally:
        result["elapsed_ms"] = (time.perf_counter() - start) * 1000


# 사용 예시
def test_with_measure(self, client):
    with measure_time() as timing:
        response = client.get("/api/products/")

    assert timing["elapsed_ms"] < 200
```

---

## 📊 성능 테스트 리포팅

```python
@pytest.fixture(scope="session", autouse=True)
def performance_report(request):
    """성능 테스트 결과 수집 및 출력"""
    results = []

    yield results

    # 세션 종료 시 리포트 출력
    if results:
        print("\n=== 성능 테스트 결과 ===")
        for r in results:
            print(f"  {r['name']}: {r['time_ms']:.2f}ms")
```

---

## 📋 새 API 성능 테스트 체크리스트

### 응답 시간
- [ ] 목록 조회 < 200ms
- [ ] 상세 조회 < 100ms
- [ ] 생성/수정 < 300ms
- [ ] 검색 < 300ms

### 동시성
- [ ] 10개 동시 요청 처리
- [ ] 모든 요청 성공 (200)
- [ ] 에러 없음

### 데이터베이스
- [ ] N+1 쿼리 없음
- [ ] 쿼리 수 임계값 이내

---

## ⚠️ 주의사항

### 1. CI 환경 고려
```python
# CI는 로컬보다 느림 - 여유 있는 임계값 사용
THRESHOLD = 200 if os.getenv("CI") else 100
```

### 2. 웜업 요청
```python
# 첫 요청은 느릴 수 있음 (JIT, 캐시 등)
client.get("/api/products/")  # 웜업
# 이후 측정 시작
```

### 3. 테스트 격리
```python
# 다른 테스트의 영향을 받지 않도록 독립 실행
pytest -m performance -n 0  # 병렬 비활성화
```

---

## 🏃 실행 명령

```bash
# 성능 테스트만 실행
pytest -m performance -v

# 동시성 테스트만 실행
pytest -m concurrency -v

# 성능 + 동시성 테스트
pytest -m "performance or concurrency" -v -n 0
```

---

## ✅ 완료 체크리스트

- [ ] 주요 엔드포인트별 응답 시간 테스트
- [ ] 임계값 상수 정의
- [ ] 동시 요청 테스트 (10개 이상)
- [ ] N+1 쿼리 검증
- [ ] `@pytest.mark.performance` 마커
- [ ] `@pytest.mark.concurrency` 마커
- [ ] AAA 주석 패턴 적용

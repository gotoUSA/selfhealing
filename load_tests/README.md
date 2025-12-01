# 성능 테스트 가이드 (Locust)

> ⚠️ **참고**: 이 폴더는 pytest 테스트가 아닌 **Locust 부하 테스트** 전용입니다.
> 일반 단위/통합 테스트는 `shopping/tests/`에 있습니다.

## 폴더 구조

```
load_tests/
├── locustfile.py              # 메인 부하 테스트 시나리오
├── concurrent_*.py            # 동시성 테스트들
├── setup_*.py                 # 테스트 데이터 생성 스크립트
├── scenarios/                 # 추가 시나리오들
└── *.md                       # 문서
```

## 설치

```bash
pip install locust
```

## 시나리오 선택

`locustfile.py`에서 `CURRENT_SCENARIO`를 변경하여 5가지 프리셋 중 선택:

### 📌 프리셋 1: Light Traffic (브라우징 중심)
```python
CURRENT_SCENARIO = LIGHT_TRAFFIC
```
- **용도**: DB read, 캐시, 페이지네이션 성능 측정
- **비율**: Browsing 80% | Cart 15% | Order 5% | Payment 0%
- **적정 유저**: 100 → 300 → 500 → 700 → 1000

### 📌 프리셋 2: Medium Traffic (장바구니 진입)
```python
CURRENT_SCENARIO = MEDIUM_TRAFFIC
```
- **용도**: Cart DB I/O + 재고 조회 부하
- **비율**: Browsing 70% | Cart 20% | Order 10% | Payment 0%
- **적정 유저**: 30 → 100 → 200 → 300

### 📌 프리셋 3: High Intent (주문 생성 포함)
```python
CURRENT_SCENARIO = HIGH_INTENT_TRAFFIC
```
- **용도**: 주문 생성 로직 + 재고 차감 검증
- **비율**: Browsing 60% | Cart 25% | Order 12% | Payment 3%
- **적정 유저**: 50 → 100 → 200 → 300

### 📌 프리셋 4: Realistic Traffic (현실적 혼합) ✅ 기본값
```python
CURRENT_SCENARIO = REALISTIC_TRAFFIC
```
- **용도**: 실제 프로덕션과 유사한 트래픽
- **비율**: Browsing 65% | Cart 25% | Order 8% | Payment 2%
- **적정 유저**: 100 → 300 → 500 → 700 → 900

### 📌 프리셋 5: Stress Test (극단 시나리오)
```python
CURRENT_SCENARIO = STRESS_TEST
```
- **용도**: 결제 API + 비동기 워커 최대 부하
- **비율**: Browsing 0% | Cart 0% | Order 0% | Payment 100%
- **적정 유저**: 10 → 20 → 50 → 100 ⚠️ (매우 높은 부하!)

### 🎨 커스터마이징
```python
CUSTOM_SCENARIO = {
    BrowsingUser: 50,
    CartUser: 30,
    OrderUser: 15,
    PaymentUser: 5
}
CURRENT_SCENARIO = CUSTOM_SCENARIO
```

## 실행 방법

### 1. 웹 UI 모드 (개발용)

```bash
# Django 서버 실행 (터미널 1)
python manage.py runserver

# Locust 실행 (터미널 2)
locust -f load_tests/locustfile.py --host=http://localhost:8000
```

웹 브라우저에서 http://localhost:8089 접속:
- Number of users: 프리셋별 적정 유저 수 참조
- Spawn rate: 5~10 (초당 증가 사용자 수)
- Host: http://localhost:8000

### 2. CLI 모드 (CI/CD용)

```bash
locust -f load_tests/locustfile.py \
    --host=http://localhost:8000 \
    --users 500 \
    --spawn-rate 10 \
    --run-time 5m \
    --headless \
    --html report.html
```

## 결과 분석

### 주요 지표

1. **RPS (Requests Per Second)**: 초당 처리 요청 수
2. **Response Time**: 응답 시간 (평균, P50, P95, P99)
3. **Failure Rate**: 실패율 (5% 이하 목표)
4. **Concurrent Users**: 동시 사용자 수

### 목표

- 평균 응답 시간 < 1초
- P95 응답 시간 < 2초
- 실패율 < 5%

## 사용자 타입별 행동

### BrowsingUser (브라우징)
- 상품 목록 조회
- 상품 상세 조회
- 상품 검색
- 카테고리 조회

### CartUser (장바구니)
- 상품 보고 장바구니 추가 (50% 확률)
- 장바구니 확인
- 장바구니 수정/삭제

### OrderUser (주문)
- 장바구니 추가 (1~2개)
- 주문 생성
- 결제 전 이탈

### PaymentUser (결제)
- 전체 구매 플로우
- 상품 조회 → 장바구니 → 주문 → 결제
- 10% 장바구니 단계 포기
- 5% 결제 전 포기


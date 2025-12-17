# Logging Guidelines

## 목적

이 문서는 프로젝트 전반에서 일관된 로깅을 사용하기 위한 가이드라인을 제공합니다.

## 로그 레벨 사용 기준

### DEBUG
- **사용 시점**: 개발 중 디버깅을 위한 상세한 정보
- **운영 환경**: 일반적으로 비활성화
- **예시**:
  - 함수 내부 변수 값
  - 루프 반복 상세 정보
  - 세부적인 조건문 분기 정보

```python
logger.debug(f"재고 확인: product_id={product.id}, stock={product.stock}, required={quantity}")
```

### INFO
- **사용 시점**: 정상적인 비즈니스 로직 실행 흐름
- **운영 환경**: 활성화
- **예시**:
  - 서비스 메서드 시작/완료
  - 중요한 비즈니스 이벤트 (주문 생성, 결제 승인 등)
  - 상태 변경 (주문 상태, 결제 상태 등)
  - 외부 API 호출 성공

```python
logger.info(
    f"주문 생성 시작: user_id={user.id}, cart_id={cart.id}, "
    f"use_points={use_points}"
)

logger.info(
    f"주문 생성 완료: order_id={order.id}, order_number={order.order_number}, "
    f"total_amount={total_amount}, final_amount={final_amount}"
)
```

### WARNING
- **사용 시점**: 예상 가능한 문제 상황이지만 시스템은 정상 동작
- **운영 환경**: 활성화
- **예시**:
  - 잘못된 입력값 (validation 실패는 아니지만 이상한 값)
  - 중복 요청 시도
  - 비즈니스 규칙 위반 시도 (권한 없는 작업 등)
  - 리소스 부족 (포인트 부족, 재고 부족 등)

```python
logger.warning(
    f"포인트 부족: user_id={user.id}, "
    f"required={amount}, available={locked_user.points}"
)

logger.warning(
    f"이미 취소된 결제 취소 시도: payment_id={payment_id}, user_id={user.id}"
)
```

### ERROR
- **사용 시점**: 예상치 못한 오류 또는 처리 실패
- **운영 환경**: 활성화
- **예시**:
  - Exception 발생
  - 외부 API 호출 실패
  - 데이터베이스 오류
  - 비즈니스 로직 처리 실패

```python
logger.error(
    f"재고 부족: product_id={product.pk}, product_name={product.name}, "
    f"requested={cart_item.quantity}, available={product.stock}"
)

logger.error(
    f"토스페이먼츠 결제 취소 실패: payment_id={payment_id}, "
    f"error_code={e.code}, error_message={e.message}"
)
```

### CRITICAL
- **사용 시점**: 시스템 전체에 영향을 미치는 심각한 오류
- **운영 환경**: 활성화
- **예시**:
  - 데이터베이스 연결 실패
  - 필수 외부 서비스 전체 장애
  - 데이터 무결성 심각한 위반

```python
logger.critical(f"데이터베이스 연결 실패: {str(e)}")
```

## 로깅 포맷 가이드라인

### 1. Context 정보 포함
항상 충분한 context 정보를 포함하여 로그만으로도 상황을 파악할 수 있도록 합니다.

**Good:**
```python
logger.info(
    f"결제 승인 시작: payment_id={payment.id}, order_id={order.id}, "
    f"order_number={order_id}, amount={amount}, user_id={user.id}"
)
```

**Bad:**
```python
logger.info("결제 승인 시작")
```

### 2. 일관된 키 이름 사용
동일한 개념에 대해 일관된 키 이름을 사용합니다.

- `user_id` (not `uid`, `u_id`, `userId`)
- `order_id` (not `oid`, `o_id`, `orderId`)
- `product_id` (not `pid`, `p_id`, `productId`)
- `payment_id` (not `pay_id`, `paymentId`)

### 3. f-string 사용
가독성을 위해 f-string을 사용합니다.

**Good:**
```python
logger.info(f"주문 생성 완료: order_id={order.id}, user_id={user.id}")
```

**Bad:**
```python
logger.info("주문 생성 완료: order_id=%s, user_id=%s" % (order.id, user.id))
logger.info("주문 생성 완료: order_id={}, user_id={}".format(order.id, user.id))
```

### 4. 민감 정보 제외
로그에 민감한 정보를 포함하지 않습니다.

**절대 포함하면 안 되는 정보:**
- 비밀번호
- 신용카드 번호
- 개인정보 (주민등록번호 등)
- API 키/시크릿

**주의해서 사용:**
- 이메일: 필요한 경우만 포함
- 전화번호: 마지막 4자리만 마스킹
- 주소: 필요한 경우만 포함

### 5. 서비스 메서드 로깅 패턴

서비스 메서드는 다음 패턴으로 로깅합니다:

```python
@transaction.atomic
def some_service_method(user, order, amount):
    """서비스 메서드"""
    # 1. 메서드 시작 로그
    logger.info(
        f"서비스 메서드 시작: user_id={user.id}, order_id={order.id}, "
        f"amount={amount}"
    )

    try:
        # 2. 주요 단계마다 로그
        logger.info(f"재고 차감 시작: order_id={order.id}")
        # ... 비즈니스 로직 ...
        logger.info(f"재고 차감 완료: order_id={order.id}")

        logger.info(f"포인트 적립 시작: user_id={user.id}, amount={points}")
        # ... 비즈니스 로직 ...
        logger.info(f"포인트 적립 완료: user_id={user.id}, points={points}")

        # 3. 메서드 완료 로그 (요약 정보 포함)
        logger.info(
            f"서비스 메서드 완료: user_id={user.id}, order_id={order.id}, "
            f"amount={amount}, points_earned={points}"
        )

        return result

    except SomeSpecificError as e:
        # 4. 예상된 에러는 warning 또는 error
        logger.warning(f"예상된 에러 발생: error={str(e)}")
        raise

    except Exception as e:
        # 5. 예상치 못한 에러는 error
        logger.error(f"예상치 못한 에러 발생: error={str(e)}")
        raise
```

### 6. Exception 로깅

Exception을 로깅할 때는 충분한 context와 함께 기록합니다.

**Good:**
```python
except TossPaymentError as e:
    logger.error(
        f"토스페이먼츠 결제 승인 실패: payment_id={payment.id}, "
        f"error_code={e.code}, error_message={e.message}"
    )
    raise
```

**Bad:**
```python
except Exception as e:
    logger.error(str(e))
    raise
```

## 로깅 Anti-Patterns

### 1. 과도한 로깅
```python
# Bad: 너무 많은 로그
for item in items:
    logger.info(f"Processing item: {item.id}")  # 수천 개의 로그 생성 가능
    process(item)

# Good: 요약 정보만 로깅
logger.info(f"Processing {len(items)} items")
for item in items:
    process(item)
logger.info(f"Processed {len(items)} items successfully")
```

### 2. 중복 로깅
```python
# Bad: View와 Service에서 중복 로깅
# View
logger.info("주문 생성 시작")
OrderService.create_order(...)

# Service
logger.info("주문 생성 시작")  # 중복!

# Good: Service 레이어에서만 로깅
OrderService.create_order(...)  # Service 내부에서 로깅
```

### 3. 의미 없는 로그
```python
# Bad: 의미 없는 로그
logger.info("Starting...")
logger.info("Done")

# Good: 구체적인 정보 포함
logger.info(f"주문 생성 시작: user_id={user.id}, cart_id={cart.id}")
logger.info(f"주문 생성 완료: order_id={order.id}")
```

## 서비스별 로깅 체크리스트

### OrderService
- [ ] 주문 생성 시작/완료
- [ ] 재고 차감/복구
- [ ] 포인트 사용/환불
- [ ] 주문 상태 변경
- [ ] 에러 발생 시 충분한 context

### PaymentService
- [ ] 결제 생성 시작/완료
- [ ] 결제 승인 시작/완료
- [ ] 외부 API (토스페이먼츠) 호출 전/후
- [ ] 결제 취소 시작/완료
- [ ] 재고/포인트 처리
- [ ] 에러 발생 시 충분한 context

### PointService
- [ ] 포인트 추가/차감
- [ ] 포인트 만료 처리
- [ ] 포인트 부족 경고
- [ ] 에러 발생 시 충분한 context

## 운영 환경 고려사항

### 1. 로그 레벨 설정
- 개발: DEBUG
- 스테이징: INFO
- 운영: INFO (필요시 WARNING)

### 2. 로그 로테이션
- 로그 파일이 너무 커지지 않도록 로테이션 설정
- 보관 기간 설정 (예: 30일)

### 3. 로그 모니터링
- 중요한 ERROR 로그는 알림 설정
- 특정 패턴 모니터링 (예: "결제 승인 실패")

### 4. 성능 고려
- 로깅이 성능에 미치는 영향 최소화
- 대용량 데이터 로깅 지양
- 필요시 비동기 로깅 고려

## 예시: 일관된 로깅 적용

### Before
```python
def create_order(user, cart):
    print(f"Creating order for {user.username}")
    order = Order.objects.create(user=user)
    logger.info("Order created")
    return order
```

### After
```python
def create_order(user, cart):
    logger.info(
        f"주문 생성 시작: user_id={user.id}, cart_id={cart.id}, "
        f"total_amount={cart.total_amount}"
    )

    order = Order.objects.create(user=user, total_amount=cart.total_amount)

    logger.info(
        f"주문 생성 완료: order_id={order.id}, order_number={order.order_number}, "
        f"user_id={user.id}"
    )

    return order
```

## 참고 자료
- [Django Logging Documentation](https://docs.djangoproject.com/en/stable/topics/logging/)
- [Python Logging Best Practices](https://docs.python.org/3/howto/logging.html)

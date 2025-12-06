# 📌 Postman Automated Test Guide

## 1. 목적 (Purpose)

본 프로젝트의 Postman 테스트는 단순 API 정상 응답을 확인하는 목적이 아니라,
**금전적 정합성(Money Integrity)**, **보안 및 멱등성(Security & Idempotency)**,
**실제 사용자 흐름(User Journey)** 기반의 **운영 리스크 통제**를 목적으로 한다.

> API가 정상 응답하는지만 확인하는 테스트가 아니라,
> **Before → Action → After 상태 변경을 검증**하여 운영 리스크를 사전에 차단하는 시스템 테스트이다.

---

## 2. 테스트 범위 (Scope)

### 포함되는 영역

| Tier | 포함 대상 |
|------|----------|
| Tier 1 | 결제, 포인트, 재고, 환불, 취소 |
| Tier 2 | 토큰, 권한, 멱등성, Replay, 재시도 |
| Tier 3 | 사용자 End-to-End Journey |

### 제외되는 영역 (자동화 대상 아님)

- 다국어/Locale
- Label/문구/텍스트 표현
- 작은 UX 차이
- 환경별 미세한 응답 차이

> **정합성을 위협하지 않는 요소는 자동화 테스트 우선순위 밖에 둔다.**

---

## 3. 테스트 원칙 (Principles)

| ID | 원칙 |
|----|------|
| P1 | 금전, 포인트, 재고는 실패 시 **배포 중단** |
| P2 | 권한/위변조/멱등성 실패도 **배포 중단** |
| P3 | Journey 실패는 배포 가능 + **경고 기록** |
| P4 | 200코드 확인이 아닌 **상태 변화 검증** |

### 테스트 작성 필수 구조

```
Before State Save → API Call → After State Compare
```

---

## 4. 테스트 도구별 역할 분리

| 도구 | 책임 |
|------|------|
| **Pytest Unit** | 로직 단위 검증 |
| **Schemathesis** | 스키마 기반 경계값, 파라미터 변형 |
| **Postman** | 운영/금전/보안/여정 E2E |
| **Locust** | 부하/동시성 성능 검증 |

> **Postman은 유닛테스트의 확장이 아니라 운영 시뮬레이션이다.**

---

## 5. Tier 구조 및 CI 정책

| Tier | 성격 | 목적 | CI 정책 |
|------|------|------|---------|
| Tier 1 | money integrity | 금전적 손실 방지 | **실패 = 중단** |
| Tier 2 | security/idempotency | 오용·재요청 보호 | **실패 = 중단** |
| Tier 3 | user Journey | UX/Flow 검증 | **실패 = 경고만** |

---

## 6. 파일명 규칙 (Naming Convention)

```
[tier]_[category]_[scenario]_[expected].json
```

**예시:**
- `t1_payment_failRollback_stockrestore.json`
- `t2_idempotent_payment_duplicatekey.json`
- `t3_journey_signup_verify_order.json`

---

## 7. 테스트 작성 필수 섹션

각 테스트는 아래 3섹션을 **반드시 포함**:

| Section | 내용 |
|---------|------|
| **Precondition** | 현재 상태값 저장 (Before State) |
| **Action** | API 요청 |
| **Assertion** | 상태 변화 비교 및 결과 검증 |

---

## 7.1 🐳 Docker에서 테스트 실행하기

### 사전 요구사항

1. **Docker Desktop 실행** (Windows/Mac)
2. **프로젝트 컨테이너 실행 중**:
   ```bash
   docker-compose up -d
   ```

### 간편 실행 스크립트

프로젝트에 포함된 `scripts/run_postman_tests.sh` 스크립트를 사용하면 Newman Docker로 테스트를 쉽게 실행할 수 있습니다.

#### 사용 가능한 테스트 목록 확인

```bash
bash scripts/run_postman_tests.sh --list
```

#### 특정 테스트 실행

```bash
# 테스트 이름 일부만 입력해도 자동으로 찾아서 실행
bash scripts/run_postman_tests.sh t1_cart_pricechanged
bash scripts/run_postman_tests.sh t1_payment_amount
bash scripts/run_postman_tests.sh t2_auth_expired
```

#### Tier 1 전체 테스트 실행

```bash
bash scripts/run_postman_tests.sh tier1
```

### 직접 Docker 명령어 사용

스크립트 없이 직접 Newman Docker를 실행할 수도 있습니다:

```bash
# Windows (Git Bash / WSL)
MSYS_NO_PATHCONV=1 docker run --rm --network myproject_default \
  -v "$(pwd)/postman:/etc/newman" \
  postman/newman:alpine run /etc/newman/collections/tier1_money_integrity/t1_cart_pricechanged_orderblock.json \
  -e /etc/newman/environments/local.json \
  --env-var "base_url=http://nginx/api"

# Mac/Linux
docker run --rm --network myproject_default \
  -v "$(pwd)/postman:/etc/newman" \
  postman/newman:alpine run /etc/newman/collections/tier1_money_integrity/t1_cart_pricechanged_orderblock.json \
  -e /etc/newman/environments/local.json \
  --env-var "base_url=http://nginx/api"
```

### 주요 옵션 설명

| 옵션 | 설명 |
|------|------|
| `--network myproject_default` | Docker Compose 네트워크에 연결 (nginx 컨테이너 접근) |
| `-v "$(pwd)/postman:/etc/newman"` | postman 폴더를 컨테이너에 마운트 |
| `--env-var "base_url=http://nginx/api"` | 컨테이너 내부 nginx 주소 사용 |

### 테스트 결과 해석

```
┌─────────────────────────┬───────────────────┬──────────────────┐
│                         │          executed │           failed │
├─────────────────────────┼───────────────────┼──────────────────┤
│              assertions │                12 │                0 │  ← 모든 assertion 통과!
└─────────────────────────┴───────────────────┴──────────────────┘
```

- **Exit Code 0**: 모든 테스트 통과 ✅
- **Exit Code 1**: 일부 테스트 실패 ❌ (상세 내용은 출력 확인)

---

## 8. 🔥 최종 테스트 목록 (Total: 15개)

### 🔴 tier 1 – money intergrity (7개)

> **"돈이 틀리면 배포 중단"**

| 파일명 | 시나리오 | 검증 포인트 |
|--------|----------|-------------|
| `t1_payment_failrollback_stockrestore.json` | 결제 실패 시 롤백 | stock/point 원복 |
| `t1_order_create_stockdeduct.json` | 주문 생성 시 재고 차감 | stock - qty, sold + qty |
| `t1_order_cancel_stockrestore.json` | 주문 취소 시 재고 복구 | stock + qty, sold = 0 |
| `t1_payment_cancel_pointrefund.json` | 사용 포인트 환불 | user.points += used_points |
| `t1_payment_cancel_earneddeduct.json` | 적립 포인트 회수 | user.points -= earned_points |
| `t1_payment_amount_mismatch.json` | 요청 금액 불일치 차단 | req.amount != final_amount → 거부 |
| `t1_cart_priceChanged_orderblock.json` | 카트 가격과 현재 가격 불일치 | 가격 재검증 후 주문 차단 |

#### 예시: t1_payment_failrollback_stockrestore.json

```javascript
// Precondition
GET /api/products/{{product_id}}/  → stock = 10
GET /api/points/my/                → point = 5000
pm.environment.set("old_stock", stock);
pm.environment.set("old_point", point);

// Action
POST /api/payments/confirm/
Body: { order_id, payment_key, amount }
// Simulate: force payment gateway error

// Assert
pm.test("Stock unchanged after failure", function() {
    var newStock = pm.response.json().stock;
    pm.expect(newStock).to.equal(pm.environment.get("old_stock"));
});
pm.test("Points unchanged after failure", function() {
    var newPoint = pm.response.json().points;
    pm.expect(newPoint).to.equal(pm.environment.get("old_point"));
});
```

---

### 🟡 tier 2 – security & idempotency (5개)

> **"권한/중복은 보안 사고 — 배포 중단"**

| 파일명 | 시나리오 | 검증 포인트 |
|--------|----------|-------------|
| `t2_idempotent_payment_duplicateKey.json` | Idempotency Key 기반 중복 결제 방지 | 2회 요청 → 1회 처리 |
| `t2_auth_expiredtoken_reject.json` | 만료된 Token 거부 | exp < now → 401 |
| `t2_auth_invalidtoken_reject.json` | 위조 Token 거부 | signature mismatch → 401 |
| `t2_order_otherUser_forbidden.json` | 타인 주문 조회 차단 | owner != request.user → 403/404 |
| `t2_webhook_replayattack.json` | 동일 webhook 재수신 무시 | event.id 중복 → 무시 |

#### 예시: t2_idempotent_payment_duplicateKey.json

```javascript
// Precondition
var idempotencyKey = pm.variables.replaceIn("{{$guid}}");
pm.environment.set("idempotency_key", idempotencyKey);

// Action - 첫 번째 요청
POST /api/payments/request/
Headers: { "Idempotency-Key": "{{idempotency_key}}" }
Body: { order_id: {{order_id}} }
pm.environment.set("first_payment_id", pm.response.json().payment_id);

// Action - 두 번째 요청 (동일 키)
POST /api/payments/request/
Headers: { "Idempotency-Key": "{{idempotency_key}}" }
Body: { order_id: {{order_id}} }

// Assert
pm.test("Same payment returned (idempotent)", function() {
    pm.expect(pm.response.json().payment_id)
      .to.equal(pm.environment.get("first_payment_id"));
});
pm.test("Only one payment created", function() {
    // DB에 payment가 1개만 있어야 함
});
```

---

### 🟢 tier 3 – user journey flow (3개)

> **"운영 흐름 증명 — 실패 시 경고만"**

| 파일명 | 시나리오 | Steps |
|--------|----------|-------|
| `t3_journey_signup_verify_order.json` | 회원가입 → 인증 → 주문 | register → verify → cart → order → pay |
| `t3_journey_purchase_cancel_reorder.json` | 구매 → 취소 → 재구매 | pay → cancel → reorder 가능 |
| `t3_journey_point_lifecycle.json` | 포인트 적립 → 사용 → 환불 | pay(earn) → order(use) → cancel(refund) |

#### 예시: t3_journey_purchase_cancel_reorder.json

```javascript
// Step 1: 장바구니 추가
POST /api/cart/add_item/
Body: { product_id: {{product_id}}, quantity: 1 }

// Step 2: 주문 생성
POST /api/orders/
Body: { cart_item_ids: [...], shipping_* }

// Step 3: 결제
POST /api/payments/request/
POST /api/payments/confirm/

// Step 4: 결제 취소
POST /api/payments/cancel/
Body: { payment_id, cancel_reason }

// Step 5: 재주문 (같은 상품)
POST /api/cart/add_item/
POST /api/orders/
POST /api/payments/confirm/

// Assert
pm.test("Reorder successful", function() {
    pm.expect(pm.response.json().order_id).to.exist;
    pm.expect(pm.response.json().status).to.equal("paid");
});
```

---

## 9. CI Pipeline 정책

| Tier | 실패 시 | 배포 |
|------|--------|------|
| Tier 1 | **Fail** | 중단 |
| Tier 2 | **Fail** | 중단 |
| Tier 3 | Warning 기록 | 계속 진행 |

### CI 로그 예시

```
✅ TIER 1: 7/7 PASSED
✅ TIER 2: 5/5 PASSED
⚠️ TIER 3: 2/3 PASSED (1 WARNING)

CI RESULT: SUCCESS WITH WARNING
- Journey scenario "t3_journey_point_lifecycle" failed
- No financial inconsistency detected
- Deployment: PROCEED
```

---

## 10. 실패 후 Follow-up 정책

| 유형 | 후속 조치 |
|------|----------|
| 금전/보안 (Tier 1, 2) | 즉시 수정, 배포 중단 |
| 여정/UX (Tier 3) | 이슈 등록 + 향후 검토 |
| 외부 API 문제 | 알림 + 모니터링 |

---

## 11. 폴더 구조

```
postman/
├── collections/
│   ├── tier1_money_integrity/
│   │   ├── t1_payment_failrollback_stockrestore.json
│   │   ├── t1_order_create_stockdeduct.json
│   │   ├── t1_order_cancel_stockrestore.json
│   │   ├── t1_payment_cancel_pointrefund.json
│   │   ├── t1_payment_cancel_earneddeduct.json
│   │   ├── t1_payment_amount_mismatch.json
│   │   └── t1_cart_pricechanged_orderblock.json
│   ├── tier2_security/
│   │   ├── t2_idempotent_payment_duplicatekey.json
│   │   ├── t2_auth_expiredtoken_reject.json
│   │   ├── t2_auth_invalidtoken_reject.json
│   │   ├── t2_order_otheruser_forbidden.json
│   │   └── t2_webhook_replayattack.json
│   └── tier3_journey/
│       ├── t3_journey_signup_verify_order.json
│       ├── t3_journey_purchase_cancel_reorder.json
│       └── t3_journey_point_lifecycle.json
├── environments/
│   ├── local.json
│   ├── staging.json
│   └── production.json
└── README.md
```

---

## 12. 환경 변수 (Environment Variables)

```json
{
  "base_url": "http://localhost:8000/api",
  "access_token": "",
  "refresh_token": "",
  "user_id": "",
  "product_id": "",
  "order_id": "",
  "payment_id": "",
  "idempotency_key": ""
}
```

---

## 13. 주요 API 엔드포인트 참조

### 인증 (Auth)
```
POST /api/auth/register/          # 회원가입
POST /api/auth/login/             # 로그인
POST /api/auth/token/refresh/     # 토큰 갱신
POST /api/auth/email/verify/      # 이메일 인증
```

### 장바구니 (Cart)
```
POST /api/cart/add_item/          # 상품 추가
GET  /api/cart/                   # 장바구니 조회
GET  /api/cart/check_stock/       # 재고 확인
```

### 주문 (Order)
```
POST /api/orders/                 # 주문 생성
GET  /api/orders/{id}/            # 주문 상세
POST /api/orders/{id}/cancel/     # 주문 취소
```

### 결제 (Payment)
```
POST /api/payments/request/       # 결제 요청
POST /api/payments/confirm/       # 결제 승인
POST /api/payments/cancel/        # 결제 취소
GET  /api/payments/{id}/status/   # 결제 상태
```

### 포인트 (Point)
```
GET  /api/points/my/              # 내 포인트
GET  /api/points/history/         # 포인트 이력
POST /api/points/use/             # 포인트 사용
POST /api/points/cancel/          # 포인트 취소 처리
```

### 웹훅 (Webhook)
```
POST /api/webhooks/toss/          # 토스 웹훅 수신
```

---

## 14. 철학

> **"스케일 대비 테스트가 많은 것이 비용이 아니라 보험이다."**
>
> **"정합성(Consistency)은 속도와 절충할 대상이 아니다."**
>
> **"테스트는 코드가 아니라 리스크를 검증한다."**

---

## 15. 체크리스트

### 테스트 작성 전 확인사항

- [ ] 이 시나리오가 단위 테스트로 이미 커버되어 있는가?
- [ ] 이 시나리오가 Schemathesis로 이미 커버되어 있는가?
- [ ] 금전적 손실을 초래할 수 있는 시나리오인가?
- [ ] Before/After 상태 비교가 가능한가?

### 테스트 작성 후 확인사항

- [ ] Precondition에서 상태값을 저장했는가?
- [ ] Assert에서 상태 변화를 검증했는가?
- [ ] 올바른 Tier에 분류했는가?
- [ ] 파일명 규칙을 준수했는가?

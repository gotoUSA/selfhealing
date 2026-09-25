# Django 쇼핑몰 API

[![Django CI](https://github.com/gotoUSA/selfhealing/actions/workflows/django-ci.yml/badge.svg)](https://github.com/gotoUSA/selfhealing/actions/workflows/django-ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-316192?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.5-37814A?logo=celery&logoColor=white)

토스페이먼츠 결제 · JWT/소셜 로그인 · 포인트 · 비동기 주문 처리가 있는 Django REST API. 2025년 8월부터 12월까지 만든 개인 프로젝트다.

**이 레포 이름이 `selfhealing`인 이유**: 결제 흐름을 만들다가 "결제사 API가 죽으면 우리 서버는 어떻게 되나"를 파기 시작했고, 그 답(서킷 브레이커 · 재시도 · 실패 요청 보관/재실행)을 이 레포 안에서 직접 구현했다. 그 계층이 커져서 2026년 3월 별도 라이브러리로 분리했고, 이후 [baldurhq/baldur](https://github.com/baldurhq/baldur)가 됐다. 이 레포에는 쇼핑몰과, 라이브러리가 되기 전의 결제 복구 코드(`shopping/services/payment_recovery_service.py`, 첫 커밋 2025-12-08)가 남아 있다.

---

## 코드를 보러 오셨다면

파일 200개를 다 읽을 필요는 없다. 설계 결정이 드러나는 곳만 짚으면:

| 주제 | 위치 | 한 줄 |
|---|---|---|
| 재고 차감 동시성 | `shopping/services/order_service.py` `_create_order_items_and_decrease_stock` | `select_for_update` 행 락 + `F("stock") - qty`로 검사와 갱신을 DB 안에서, 상품 ID 순으로 잠가 다품목 주문끼리의 데드락 방지. 재고 1개에 스레드 N개 → 성공 정확히 1 (`tests/integration/test_order_concurrency.py`) |
| 웹훅 진위·멱등성 | `shopping/webhooks/toss_webhook_view.py`, `shopping/services/toss_webhook_service.py` `handle_payment_done` | 토스 결제 웹훅엔 서명이 없어 본문을 믿지 않고 `paymentKey`로 결제 조회 API를 다시 불러 그 응답으로 처리(`test_webhook_authenticity.py`). 그 위에 Redis TTL 60초(빠른 필터 — 키는 커밋 뒤에만 `cache.add`로, 롤백된 처리는 흔적을 안 남긴다, `test_webhook_l1_marking.py`) → Payment 행 락 + `is_paid` 검사(진짜 보장) → Order 상태 검사. confirm 응답과 웹훅의 순서가 뒤바뀌어도 한 번만 처리 (`test_confirm_webhook_race.py`) |
| 결제 승인: 외부 API를 트랜잭션 밖으로 | `shopping/services/payment_service.py` `confirm_payment_sync` vs `confirm_payment_async` | 같은 파일에 두 버전. sync는 행 락을 쥔 채 결제사 HTTP를 기다리고(커넥션·워커가 묶임), async는 `in_progress`로 바꾸고 커밋한 뒤 Celery 체인(`external_api` 큐 → `payment_critical` 큐)으로 넘긴다. 뷰는 async를 쓴다 |
| 트랜잭션 범위와 Celery 발행 시점 | `order_service.py` `create_order_hybrid` | Order 한 행만 짧은 트랜잭션으로 만들고 **커밋 뒤에** `.delay()`. 워커는 다시 행 락을 잡고 재고·포인트를 처리, 실패 시 `failed` + 재고 복구. 커밋 뒤 발행이 실패하거나 워커가 메시지를 잃어 한 번도 처리되지 못한 주문(`pending`·항목 0개)은 2분마다 도는 `republish_stalled_orders`가 5분 뒤 다시 발행하고, 결제 만료 시간까지 안 되면 실패로 닫고 장바구니를 돌려준다 — 주문 행 자체가 "아직 처리되지 않은 메시지" 표시다. 태스크는 `acks_late`라 처리 중 워커 프로세스가 죽으면 곧바로 재배달되고, 행 락 + `pending` 재검사로 두 번 와도 한 번만 처리 (`shopping/tests/tasks/test_order_republish.py`) |
| Celery 재시도 정책 | `shopping/tasks/payment_tasks.py` `call_toss_confirm_api` | 토스 오류 코드를 비재시도/재시도/5xx/기타 4xx로 분류(`shopping/constants.py`). 수동 `self.retry`에도 데코레이터의 지수 백오프 + jitter가 실제로 적용되게 countdown을 직접 계산한다(`shopping/tasks/retry_policy.py` — Celery는 이 옵션을 `autoretry_for`에서만 써서, 그전엔 모든 재시도가 180초 고정이었다). 외부 API 큐(`external_api`)는 전용 워커(`celery_worker_external`)가 처리해 결제사 지연이 주문 처리·결제 마감을 막지 않는다 — 큐만 나누고 워커를 같이 쓰면 격리가 없다(토스 15초 지연 중 주문 확정 0.1초 → 8.8초, 워커 분리 뒤 0.07초). 타임아웃 뒤 재시도가 `ALREADY_PROCESSED_PAYMENT`를 받으면 롤백하지 않고 결제 조회 API로 대사해 승인이면 정상 마감, 조회도 안 되면 롤백 대신 운영자 알림. HTTP 타임아웃(연결 3초·읽기 20초)을 태스크 제한(25초)보다 짧게 둬 느린 응답도 같은 대사 경로를 타고, 재시도를 기다리는 동안 결제는 `in_progress`로 남는다. 실패 처리한 결제에 토스 승인(DONE) 웹훅이 오면 무시하지 않고 운영자 알림. 결제 마감 재시도가 소진되거나 승인 체인이 멈춰 `in_progress`로 10분 넘게 남은 결제는 `reconcile_stalled_payments`(5분)가 토스 조회로 대사해 승인됐으면 다시 마감하고, 아니면 롤백하지 않고 한 번 알린다 (`shopping/tests/tasks/test_payment_tasks.py`, `test_task_at_least_once.py`) |
| 두 번 배달돼도 한 번 | `shopping/tasks/point_tasks.py` `add_points_after_payment`, `shopping/tasks/email_tasks.py` | `acks_late`라 워커가 죽으면 같은 메시지가 다시 온다. 적립은 주문 행 락 → "이미 적립했나" 검사 → 적립·기록을 한 트랜잭션으로(+ 주문당 구매 적립 1건 부분 유니크 제약), 인증 메일은 토큰별 로그가 발송 상태면 건너뛰고, 일반 메일은 Celery task id로 같은 메시지를 알아본다(내용이 같은 두 번의 요청은 두 통). 실패 메일 재발송 스윕은 발송 태스크의 재시도가 끝난 뒤(10분)에만, 최대 3회, 로그를 먼저 집고 보낸다. 워커 프로세스를 죽여 실제로 재배달시키는 실험으로 확인 (`shopping/tests/tasks/test_task_at_least_once.py`) |
| 주문 취소·반품: 돈을 돌려주는 입구 다섯 개, 규칙 하나 | `shopping/services/order_service.py` `cancel_by_customer` · `cancel_order`, `payment_service.py` `cancel_payment` | 돈·재고·포인트를 되돌리는 입구는 주문 취소 버튼·결제 취소 버튼·토스 취소 웹훅·미결제 만료 배치·반품 환불 다섯 곳이다. 주문 행 락을 잡은 뒤 상태를 검사해 다섯 번 동시에 눌러도 한 번만 취소된다(락 한 줄만 빼면 다섯 번 다 성공해 재고가 10개 늘어나는 걸 실험으로 확인). 결제 완료 주문은 주문 취소 버튼으로 눌러도 결제 취소(토스 환불)로 간다. 결제 전 주문은 결제 승인이 진행 중이면 취소하지 않고, 아니면 결제를 먼저 닫아 뒤이은 승인을 막는다 — 만료 배치와 같은 울타리. 가상계좌 입금 대기 주문은 토스 결제 취소 API로 계좌를 먼저 닫고(입금 전이라 환불 계좌 불필요) 취소한다 — 예전엔 주문만 취소하고 계좌를 열어 둬서, 입금되면 취소된 주문이 결제 완료로 되살아났다. 결제 마감과 입금 웹훅은 취소된 주문을 결제 완료로 되살리지 않고, 결제 취소는 되돌릴 수 없는 토스 환불 전에 검증을 끝낸다. 예전엔 이 연결들이 빠져서 결제 완료 주문이 환불 없이 취소되거나, 승인 중 취소한 주문이 결제 완료로 되살아났다 결제 취소는 배송 전 주문만 받고, 배송된 주문의 돈은 반품으로만 돌려준다. 반품 환불은 낸 방식대로 나눈다 — 반품 상품값 중 포인트로 낸 비율만큼은 포인트로, 나머지는 토스 부분 취소로(원래 배송비 제외), 적립 포인트도 같은 비율만 회수 — 누적 차이로 계산해 여러 번 나눠 반품해도 합이 정확하고, 다 돌려받기 전까지 주문은 배송 완료로 남아 나머지도 반품할 수 있다. 토스 환불 전에 검증을 끝내고, 반품별 멱등키를 붙여 재시도해도 한 번만 환불된다(토스 환불 뒤 DB 실패 → 재시도를 실험으로 확인). 예전엔 포인트로 낸 몫을 현금·포인트로 두 번 돌려주고, 부분 반품이 주문 전체를 환불 완료로 닫았고, 적립 포인트를 쓴 고객의 반품은 판매자가 누를 때마다 토스 환불만 반복됐고, 배송 완료 주문을 결제 취소로 전액 환불받을 수 있었다 (`shopping/tests/api/order/test_order_cancel_paths.py`, `shopping/tests/api/return/test_return_refund_paths.py`) |
| 포인트: 잔액과 적립 건, 장부 둘 | `shopping/services/point_service.py` `use_points_fifo` · `refund_used_points` · `expire_points` | 사용은 사용자 행을 잠그고 잔액을 다시 본 뒤 만료 임박 순으로 적립 건을 깎는다(잔액 5,000P에 1,000P 주문 6건을 워커에서 동시에 차감 → 5건 확정·1건 실패를 실험으로 확인). 쓴 포인트 환불은 주문 취소·결제 취소·토스 취소 웹훅·반품·결제 실패가 모두 같은 함수로 하고, 주문의 사용 이력을 거꾸로 따라 원래 적립 건과 만료일까지 되돌린다(원래 만료일이 이미 지났으면 되돌린 몫은 바로 만료). 만료 배치는 적립 건 하나가 트랜잭션 하나라 한 건이 실패해도 나머지는 만료되고, 실패가 있으면 태스크가 실패로 끝나 재시도한다(이미 만료된 건은 건너뛴다). 만료 예정 화면과 만료 안내 메일은 같은 "남은 양" 계산을 쓴다. 예전엔 고객이 직접 부르는 포인트 환불 API(`/api/points/cancel/`)가 금액·횟수 제한 없이 열려 있어 포인트를 만들어 결제까지 됐고(테스트 커버리지 PR에 딸려 들어온 엔드포인트 — 호출하는 곳이 없어 삭제), 환불이 잔액만 올려 주문하고 취소하면 만료 직전 포인트가 만료 없는 포인트가 됐고, 만료 배치 전체가 한 트랜잭션이라 한 건의 DB 에러가 그날 만료분 전체를 되돌렸는데 결과는 성공으로 남았고, 만료 예정 화면이 이미 쓴 포인트까지 셌다 (`shopping/tests/api/point/test_point_ledger_paths.py`) |
| 상품 카드는 쿼리 모양 하나로 | `shopping/services/product_query_service.py` `product_card_queryset` | 평점·리뷰 수·찜 수·내 찜 여부를 LEFT JOIN 둘 + GROUP BY로 붙였더니 상품×리뷰×찜으로 행이 곱해지고(2만 상품에 64만 행, 700ms), `is_wished`의 CASE가 GROUP BY에 들어가 내가 찜한 상품을 남도 찜하면 목록에 두 번 나왔다. `EXPLAIN`을 보다가 찾았고 상관 서브쿼리(`Subquery`/`Exists`)로 바꿔 상품당 한 행, 1ms. 그 모양(판매자·카테고리 JOIN + 이미지 prefetch + 통계 서브쿼리)을 상품 카드를 그리는 모든 곳 — 목록·장바구니·주문 상세·재고 부족 — 이 이 함수로 가져온다. 목록만 고쳐 뒀을 땐 같은 시리얼라이저를 쓰는 장바구니·주문 상세가 항목마다 쿼리를 3개씩 더 보냈고 평점·찜 필드 넷이 응답에서 빠져 있었다. 리뷰 본문은 읽지 않는다(상품당 리뷰 2천 개면 목록 한 번에 2만 4천 행, 쿼리 수는 그대로라 쿼리 수 검사로는 안 보인다). 행 3개와 12개에서 쿼리 수가 같은지, 리뷰를 prefetch·JOIN 하지 않는지 (`shopping/tests/api/test_query_counts.py`, `test_wishlist.py::TestProductListWishlistStats`) |
| 결제 복구 계층(라이브러리의 원형) | `shopping/services/payment_recovery_service.py` | 서킷 브레이커 확인 · 백오프 재시도 · DLQ 이동 규칙을 모듈로 만들고 테스트로 검증했다(2025-12-08). **결제 흐름에는 연결되지 않았다** — 설계 문서에 적은 연결(승인 실패 시 `handle_failure` 호출)을 구현하지 않았고, 테스트가 서비스를 직접 불러 초록이라 빠진 걸 몰랐다. 이메일·소셜 로그인 같은 다른 외부 호출에도 같은 규칙이 필요해 라이브러리로 옮겼고, 분리 뒤 이 모듈의 DLQ 저장 함수는 옮겨간 모델을 가리켜 동작하지 않는다. 원형 기록으로만 남겨 둔 코드다 — 결제 승인·취소의 실제 복구는 `payment_tasks.py`의 재시도·대사 스윕이 한다 |

아래 [알려진 한계](#알려진-한계)도 같이 보면 좋다. 위 코드에서 내가 아는 구멍을 적어 뒀다.

## 기능

- **인증**: JWT(access 30분 / refresh 7일, 회전 + 블랙리스트), 소셜 로그인(구글·카카오·네이버 OAuth 코드 경로, 응답을 하나로 정규화 — 실계정 로그인 확인은 구글·네이버, 카카오 앱은 정지 상태라 모킹 테스트로만 검증), 이메일 인증, 비밀번호 재설정
- **결제**: 토스페이먼츠 연동(카드/계좌/가상계좌 — 발급·입금 대기·입금 완료·기한 만료·입금 취소), 결제 조회 API로 진위를 확인하는 웹훅(`PAYMENT_STATUS_CHANGED`·`DEPOSIT_CALLBACK`), 멱등성 키, 결제 취소·환불, 포인트 전액 결제
- **상품**: 계층형 카테고리(MPTT), 다중 이미지, 리뷰, 상품 문의(Q&A), 판매자 프로필
- **장바구니 · 찜**: 재고 검증, 위시리스트
- **주문 · 반품**: 주문 생성(동기/비동기 두 경로), 취소 시 재고·판매량 복구, 미결제 주문은 30분(`ORDER_PAYMENT_TIMEOUT_MINUTES`) 뒤 자동 취소·재고 반환, 반품 요청
- **포인트**: 등급별 적립률, FIFO 사용·만료, 만료 예정 알림
- **비동기**: Celery 큐 6개(`external_api`, `payment_critical`, `order_processing`, `points`, `notifications`, `default`)와 워커 2종(외부 API 전용 / 나머지), Beat 스케줄(포인트 만료, 미결제 주문 만료, 미인증 계정 정리, 고아 주문 감지 등)
- **운영**: OpenAPI(drf-spectacular), Prometheus 메트릭, Docker Compose(Grafana 스택 포함), Locust 부하 테스트(`load_tests/`), 카오스 주입(`shopping/chaos/` — 결제 승인 뒤 부분 실패, PG 승인 후 DB 실패 같은 고장을 일부러 만들어 복구 경로를 시험)

## 구조

```
shopping/
├── models/        # User, Product, Category, Cart, Order, Payment, PointHistory, Return, ... (22개)
├── services/      # 비즈니스 로직 (order, payment, toss_webhook, point, cart, return, social_auth, token, ...)
├── views/         # DRF 뷰 (62개 엔드포인트)
├── serializers/
├── tasks/         # Celery 태스크 (payment, order, email, cleanup, point)
├── webhooks/      # 토스 웹훅 진입점 (결제 조회로 진위 확인 → 상태별 디스패치)
├── handlers/      # 실패 요청 재실행 핸들러 (결제 복구 계층)
├── chaos/         # 고장 주입 (CHAOS_MODE 환경변수로 켬)
├── admin/         # Django Admin
└── tests/         # 2,000여 개 (아래 참고)
myproject/
├── settings/      # base / local / test / production + components/
└── celery.py      # 큐·라우팅·Beat 스케줄
tests/             # hybrid/ (Celery 워커 크래시·멱등성), integration/ (Kafka·OTEL, 인프라 필요)
load_tests/        # Locust
k8s/               # 배포 매니페스트 (KEDA 스케일링 포함, 실험용)
docker/            # Prometheus / Grafana / Loki / Tempo / OTEL collector 설정
```

## 실행

Docker Desktop이 필요하다.

```bash
git clone https://github.com/gotoUSA/selfhealing.git
cd selfhealing
cp .env.example .env
```

`.env`에서 컨테이너용으로 다섯 줄만 바꾼다:

```
DATABASE_HOST=db
DATABASE_PASSWORD=shopping_pass
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

```bash
docker-compose up -d                                   # 첫 빌드 2~3분
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
docker-compose exec web python manage.py create_test_data --preset basic
```

| 서비스 | URL |
|---|---|
| Swagger | http://localhost:8000/api/docs/swagger/ |
| ReDoc | http://localhost:8000/api/docs/redoc/ |
| Admin | http://localhost:8000/admin/ |
| Flower (Celery) | http://localhost:5555/ |

토스페이먼츠 테스트 키(`TOSS_CLIENT_KEY`, `TOSS_SECRET_KEY`)는 실제 결제 흐름을 볼 때만 필요하다.

Docker 없이 돌리려면 PostgreSQL 15 · Redis 7을 띄우고 `pip install -e ".[dev]"` → `python manage.py migrate` → `runserver` + `celery -A myproject worker` + `celery -A myproject beat`.

## 테스트

```bash
# CI와 같은 실행 (PostgreSQL · Redis 필요)
pytest shopping/tests/ --reuse-db -n 6 --dist loadscope

# 동시성 불변식만 (스레드 + 실제 DB)
pytest shopping/tests/integration/test_order_concurrency.py shopping/tests/integration/test_confirm_webhook_race.py -n 0
```

```
shopping/tests/
├── api/            # 엔드포인트 (919)
├── unit/           # 모델·서비스·시리얼라이저 (607)
├── schema/         # OpenAPI 계약·상태 기반 워크플로·퍼즈 (288)
├── integration/    # 동시성 불변식, 상태 전이 (71)
├── tasks/          # Celery 태스크 (44)
├── commands/       # management command (38)
└── admin/          # Django Admin (21)
```

기본 `pytest`는 무거운 마커(`concurrency`, `load_test`, `heavy`, `flaky` 등)를 뺀다 — `pyproject.toml`의 `addopts` 참고. `tests/hybrid/`·`tests/integration/`은 Redis/Kafka/OTEL 같은 실제 인프라가 필요하고, 일부는 아래 결제 복구 라이브러리가 설치된 환경에서만 수집된다.

## 결제 복구 계층에 대해

이 레포는 원래 결제 복구 라이브러리(서킷 브레이커 · DLQ · 재실행 · 감사)를 `selfhealing`이라는 이름의 pip 패키지로 붙여 썼다. 그 패키지는 이 레포에서 추출된 뒤 `baldur`로 개명됐고, 여기 남은 통합 코드는 개명 전 API를 쓴다. 그래서:

- 라이브러리는 **선택 의존성**이다. `myproject/settings/base.py`의 `SELFHEALING_AVAILABLE`이 설치 여부를 보고 앱·예외 핸들러·URL·Beat 스케줄을 붙이거나 뺀다. 없어도 쇼핑몰은 그대로 동작한다.
- 그 계층이 어떻게 생겼는지는 `shopping/services/payment_recovery_service.py`, `shopping/tasks/payment_recovery_tasks.py`, `shopping/handlers/replay_handlers.py`에서 볼 수 있다. 완성본은 [baldurhq/baldur](https://github.com/baldurhq/baldur).

## 알려진 한계

코드를 읽으면서 확인한 것들이다. 고치는 방법도 같이 적었다.

- **웹훅 계약을 지어낸 채 9개월을 갔다**: 처음 코드는 토스에 없는 `X-Toss-Webhook-Signature` HMAC 서명과 `PAYMENT.DONE` 같은 이벤트 이름을 구현했고, 그 계약을 검증하는 테스트가 초록이어서 아무도 몰랐다. 실제 계약(`PAYMENT_STATUS_CHANGED` + Payment 객체, 결제 웹훅엔 서명 없음)은 토스 문서를 대조하고서야 알았다. 같은 대조에서 가상계좌 승인 응답(`WAITING_FOR_DEPOSIT`)을 결제 완료로 처리하던 것과 취소 사유를 잘못된 필드에서 읽던 것도 나왔다. 외부 API는 문서를 읽고 계약 테스트를 그 문서에서 뽑아야지, 코드에서 뽑으면 안 된다.
- **적립 포인트를 이미 썼으면 웹훅 취소가 포인트를 회수하지 못한다**: 토스가 결제를 취소하거나 가상계좌 입금을 되돌리면(`CANCELED`, `DONE`→`WAITING_FOR_DEPOSIT`) 그 주문으로 적립한 포인트를 FIFO로 회수하는데, 고객이 이미 써 버렸으면 회수할 게 없다. 토스 쪽은 이미 끝난 일이라 주문 상태는 토스를 따라가고, 회수하지 못한 양은 결제 로그(`적립 포인트 미회수`)에 남긴다(입금 되돌림은 주문의 `earned_points`도 지우지 않는다). 자동 차감(음수 잔액)이나 청구는 정책 결정이라 사람이 한다.
- **결제 취소는 PG 취소 성공 뒤 DB 갱신이 실패할 수 있다**: `cancel_payment`는 하나의 `@transaction.atomic` 안에서 토스 취소 API를 부르고(승인의 sync 버전과 같은 모양) 이어서 결제·재고·주문·포인트를 갱신한다. 취소 API가 성공한 뒤 DB 쪽 어느 단계든 실패하면 트랜잭션 전체가 되돌아가 토스는 취소됐는데 우리는 `paid`. 예전엔 "적립 포인트를 회수할 수 있나" 검사가 토스 호출 **뒤**에 있어서, 적립 포인트를 이미 쓴 고객이 취소하면 매번 이렇게 됐다(돈은 환불, 응답은 500) — 검사를 호출 앞으로 옮겼다. 남은 건 예상 밖의 DB 오류뿐이고, 2초 뒤 오는 토스 취소 웹훅이 결제·주문·재고·포인트를 맞춘다. 맞는 처리는 외부 호출 결과를 먼저 기록하는 outbox 테이블인데, 이벤트 테이블을 들이는 설계 결정이라 여기서는 안 했다. 같은 이유로 취소 API가 실패했을 때 남기려는 에러 `PaymentLog`도 안쪽 `atomic`이 savepoint일 뿐이라 바로 뒤 `raise`에 함께 롤백돼 남지 않는다(주석의 "트랜잭션 밖에서 로그 기록"과 반대) — 흔적은 애플리케이션 로그뿐이다.
- **울타리 밖 경합으로 취소된 주문에 승인·입금이 오면 사람이 환불한다**: 주문 취소가 승인 중인 결제를 막고 입금 대기 계좌를 먼저 닫으므로 정상 경로로는 생기지 않지만, 그래도 오면 결제 마감·입금 웹훅은 주문을 되살리지 않고 결제를 `done`으로 남긴 뒤 critical 알림을 보낸다(가상계좌 입금분 환불에는 고객 환불 계좌가 필요하다). 환불은 결제 취소 API로 한다(주문이 이미 취소돼 있으면 돈만 돌려주고 재고·포인트는 건드리지 않는다). 자동 환불은 도달할 경로가 없어서 만들지 않았다.
- **반품 환불의 정책 두 가지는 코드로 고정했다**: 원래 배송비는 돌려주지 않고(반품 배송비는 현금 몫에서 뺀다), 적립 포인트를 이미 쓴 고객의 반품은 환불 전에 거절한다(주문 취소와 같은 규칙 — 환불액에서 빼거나 음수 잔액을 허용하는 건 사람이 정할 정책). 토스 부분 취소 웹훅(`PARTIAL_CANCELED`)은 무시하므로, 토스 환불 뒤 우리 DB가 실패하면 판매자가 다시 눌러야 맞춰진다(같은 멱등키라 돈은 한 번만 나간다).
- **메일은 드물게 한 통 중복될 수 있다**: 발송 태스크는 같은 메시지를 알아보지만, SMTP 서버가 메일을 받은 직후 기록하기 전에 워커가 죽으면 재배달이 한 번 더 보낸다. SMTP에는 멱등 키가 없어서 막을 방법이 없고, 인증·안내 메일 한 통 중복은 받아들이는 쪽을 택했다(체계적 중복 — 재발송 스윕이 재시도 중인 메일을 또 보내던 것 — 은 막았다).
- **관리자 화면은 목록에 필요한 데이터를 한 번에 읽어 오지 않는다**: Django 관리자 목록 17곳 중 10곳이 행마다 쿼리를 더 보낸다(카테고리 +4, 상품·포인트 이력·반품·장바구니 +2, 주문·문의·답변·반품 항목·결제 로그 +1; 한 페이지 100행). 원인은 `list_select_related`를 쓰지 않은 것과, `Category.__str__`가 부를 때마다 조상 카테고리를 조회하는 것이다(그래서 상품 관리 화면은 결과가 0건이어도 카테고리 수만큼 쿼리가 나간다). 고객이 쓰는 API는 행 수와 무관하게 고쳤고(`shopping/tests/api/test_query_counts.py`), 운영자만 쓰는 이 화면은 남겼다.
- **관리자 결제·결제 로그 화면의 검색은 동작하지 않는다**: `search_fields`의 `order_id`·`payment__order_id`가 OneToOne 필드라 무엇을 검색해도 500(`FieldError`)이다. 주문번호(`order__order_number`) 같은 문자열 필드로 바꾸면 된다.
- **장바구니 일괄 담기는 항목마다 상품을 다시 읽는다**: 요청의 상품을 한 번에 읽어 두고도 `_add_or_update_item`이 항목마다 `Product.objects.get`으로 다시 조회한다. 읽어 둔 상품을 넘기면 항목당 쿼리가 하나 준다.
- **포인트 적립 건별 사용량이 JSON 컬럼**(`PointHistory.metadata.used_amount`): 집계·인덱스가 안 된다. 사용자당 적립 건이 수천 개가 되면 할당 테이블로 빼야 한다.
- **부하는 재지 않았다**: Locust 스크립트(`load_tests/`)와 캐시 설정은 있지만 수치를 남긴 측정은 없다. "몇 RPS까지 버티나"에는 답이 없다고 말하는 게 맞다.
- **백업·시크릿 관리·온콜은 코드 밖**: `.env`와 CI 시크릿으로 키를 넘기는 것까지가 이 레포의 범위다. DB 백업 주기, 키 회전, 장애 시 누가 받는지는 정해 둔 게 없다.
- **한 번의 머지가 코드를 떨어뜨렸던 일**: 2025-12-14, 쇼핑몰 브랜치와 결제 복구 브랜치를 합치면서 한쪽의 테스트와 다른 쪽의 코드가 섞여 들어갔다(취소 시 포인트 환불, 회원 등급 스냅샷, 락 순서 고정, 주문 실패 사유가 사라짐). CI가 사설 의존성 때문에 돌지 않아 9개월 뒤에야 발견했고, 사라진 코드는 git에서 되살렸다. 두 갈래를 오래 벌려 두지 말 것, 그리고 CI가 빨간 채로 두지 말 것 — 이 레포에서 배운 가장 비싼 교훈이다.

## 개발 노트

Claude(Anthropic)와 함께 만들었다. 구조 설계, 토스 결제·웹훅 처리, Celery 구성, 테스트 작성에 AI를 썼고, 무엇을 만들지 · 어디까지 만들지 · 위의 한계를 어떻게 볼지는 내가 결정했다. 결제 실패 질문에서 시작해 라이브러리를 만들고 출시하고 접기까지의 과정은 [POSTMORTEM](https://github.com/baldurhq/baldur/blob/main/POSTMORTEM.ko.md)에 있다.

2026년 9월에 이 레포를 다시 열어 실제 환경에 붙여 검증했다. 토스 테스트 상점에 실제 결제·웹훅을 보내고, Celery 워커를 실제로 띄워 동시 주문·재배달·워커 강제 종료를 돌리고, API마다 행 수를 바꿔 가며 쿼리 수를 셌다. 찾은 결함은 옛 코드에서 먼저 실패하는 테스트를 쓰고 고쳤고(2026-09-20~26 커밋), 고치지 않은 것은 위 알려진 한계에 이유와 함께 적었다.

## 라이선스

Apache License 2.0 — [LICENSE](LICENSE)

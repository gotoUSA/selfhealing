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
| 재고 차감 동시성 | `shopping/services/order_service.py` `_create_order_items_and_decrease_stock` | `select_for_update` 행 락 + `F("stock") - qty`로 검사와 갱신을 DB 안에서. 재고 1개에 스레드 N개 → 성공 정확히 1 (`tests/integration/test_order_concurrency.py`) |
| 웹훅 멱등성 | `shopping/services/toss_webhook_service.py` `handle_payment_done` | Redis TTL 60초(빠른 필터) → Payment 행 락 + `is_paid` 검사(진짜 보장) → Order 상태 검사. confirm 응답과 웹훅의 순서가 뒤바뀌어도 한 번만 처리 (`test_confirm_webhook_race.py`) |
| 결제 승인: 외부 API를 트랜잭션 밖으로 | `shopping/services/payment_service.py` `confirm_payment_sync` vs `confirm_payment_async` | 같은 파일에 두 버전. sync는 행 락을 쥔 채 결제사 HTTP를 기다리고(커넥션·워커가 묶임), async는 `in_progress`로 바꾸고 커밋한 뒤 Celery 체인(`external_api` 큐 → `payment_critical` 큐)으로 넘긴다. 뷰는 async를 쓴다 |
| 트랜잭션 범위와 Celery 발행 시점 | `order_service.py` `create_order_hybrid` | Order 한 행만 짧은 트랜잭션으로 만들고 **커밋 뒤에** `.delay()`. 워커는 다시 행 락을 잡고 재고·포인트를 처리, 실패 시 `failed` + 재고 복구 |
| Celery 재시도 정책 | `shopping/tasks/payment_tasks.py` `call_toss_confirm_api` | 토스 오류 코드를 비재시도/재시도/5xx/기타 4xx로 분류(`shopping/constants.py`), 지수 백오프 + jitter, 큐 분리로 결제사 지연이 다른 태스크를 막지 않게 |
| 포인트 FIFO | `shopping/services/point_service.py` `use_points_fifo` | 만료 임박 순으로 적립 건을 잠그고 차감, 만료 배치는 `Greatest(F("points") - n, 0)`으로 음수 방지 |
| 결제 복구 계층(라이브러리의 원형) | `shopping/services/payment_recovery_service.py` | 서킷 브레이커 확인 · SLA 타임아웃 · 백오프 재시도 · DLQ 이동 규칙. 이 510줄이 세 군데 필요해지는 시점에 라이브러리로 뺐다 |

아래 [알려진 한계](#알려진-한계)도 같이 보면 좋다. 위 코드에서 내가 아는 구멍을 적어 뒀다.

## 기능

- **인증**: JWT(access 30분 / refresh 7일, 회전 + 블랙리스트), 소셜 로그인(구글·카카오·네이버, 3사 응답을 하나로 정규화), 이메일 인증, 비밀번호 재설정
- **결제**: 토스페이먼츠 연동(카드/계좌/가상계좌), HMAC 서명 검증 웹훅, 멱등성 키, 결제 취소·환불, 포인트 전액 결제
- **상품**: 계층형 카테고리(MPTT), 다중 이미지, 리뷰, 상품 문의(Q&A), 판매자 프로필
- **장바구니 · 찜**: 재고 검증, 위시리스트
- **주문 · 반품**: 주문 생성(동기/비동기 두 경로), 취소 시 재고·판매량 복구, 반품 요청
- **포인트**: 등급별 적립률, FIFO 사용·만료, 만료 예정 알림
- **비동기**: Celery 워커 4개 큐(`external_api`, `payment_critical`, `order_processing`, `default`), Beat 스케줄(포인트 만료, 미인증 계정 정리, 고아 주문 감지 등)
- **운영**: OpenAPI(drf-spectacular), Prometheus 메트릭, Docker Compose(Grafana 스택 포함), Locust 부하 테스트(`load_tests/`), 카오스 주입(`shopping/chaos/` — 결제 승인 뒤 부분 실패, PG 승인 후 DB 실패 같은 고장을 일부러 만들어 복구 경로를 시험)

## 구조

```
shopping/
├── models/        # User, Product, Category, Cart, Order, Payment, PointHistory, Return, ... (22개)
├── services/      # 비즈니스 로직 (order, payment, toss_webhook, point, cart, return, social_auth, token, ...)
├── views/         # DRF 뷰 (62개 엔드포인트)
├── serializers/
├── tasks/         # Celery 태스크 (payment, order, email, cleanup, point)
├── webhooks/      # 토스 웹훅 진입점 (서명 검증 → 디스패치)
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

- **재고 락 순서**: 주문 생성이 장바구니 아이템을 담은 순서(`CartItem.Meta.ordering = ["-added_at"]`)로 상품 행을 잠근다. A→B로 담은 사람과 B→A로 담은 사람이 같은 두 상품을 동시에 주문하면 데드락이 가능하다(PostgreSQL이 한쪽을 죽이므로 재고는 안 깨지지만 그 요청은 실패). 상품 ID 순으로 잠그면 해결된다. 동시성 테스트가 전부 상품 1개짜리라 잡히지 않았다.
- **웹훅 중복 마킹이 커밋보다 먼저**: `mark_webhook_processed`가 DB 트랜잭션 커밋 전에 Redis 키를 쓴다. 핸들러가 예외로 롤백돼도 키는 60초 남아, 그 안의 재전송은 1층에서 버려진다. `transaction.on_commit`으로 옮기면 된다(2층 행 락이 있어서 중복 처리는 안 생긴다).
- **타임아웃 뒤 실제로는 승인된 결제**: 결제사 호출이 타임아웃된 뒤 재시도가 `ALREADY_PROCESSED_PAYMENT`를 받으면 비재시도 오류로 분류돼 주문을 롤백한다 — 고객은 결제됐는데 우리 DB는 실패. 맞는 처리는 결제 조회 API로 상태를 확인해 성공으로 마감하는 것이다. 카오스 모듈에 이 시나리오(`BP-21`)가 있지만 감지만 있고 정정은 없다.
- **회원 등급 스냅샷 미저장**: `Order.membership_at_order` 필드가 있고 결제 서비스가 읽지만, 주문 생성이 채우지 않는다.
- **포인트 적립 건별 사용량이 JSON 컬럼**(`PointHistory.metadata.used_amount`): 집계·인덱스가 안 된다. 사용자당 적립 건이 수천 개가 되면 할당 테이블로 빼야 한다.
- **테스트 34건이 현재 코드와 어긋남**: 포인트 환불/적립 계산, 회원 등급 스냅샷, 비동기 응답 코드(202 vs 200), 주문 실패 사유 미기록. 라이브러리 분리 기간에 CI가 사설 의존성 때문에 돌지 않아 쌓였다. 하나씩 코드 결함인지 낡은 테스트인지 가르는 중이다.

## 개발 노트

Claude(Anthropic)와 함께 만들었다. 구조 설계, 토스 결제·웹훅 처리, Celery 구성, 테스트 작성에 AI를 썼고, 무엇을 만들지 · 어디까지 만들지 · 위의 한계를 어떻게 볼지는 내가 결정했다. 결제 실패 질문에서 시작해 라이브러리를 만들고 출시하고 접기까지의 과정은 [POSTMORTEM](https://github.com/baldurhq/baldur/blob/main/POSTMORTEM.ko.md)에 있다.

## 라이선스

Apache License 2.0 — [LICENSE](LICENSE)

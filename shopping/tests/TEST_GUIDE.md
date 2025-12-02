📌 1. 기본 원칙

일관성 우선 — 같은 도메인은 같은 구조로 작성한다.

단순함 우선 — 지나친 구조화/중복 검증 금지.

점진적 개선 — 새 파일에만 우선 적용하고, 기존 파일은 필요 시 개선.

표준 우선 — Django/DRF/pytest의 기본 패턴을 따른다.

📌 2. 파일·클래스·메서드 구조
2.1 파일 원칙

파일 = 도메인 단위
예: test_product_views.py, test_payment_views.py

정상/경계/예외를 파일로 나누지 않는다.

하나의 도메인 기능군은 반드시 하나의 파일에 포함.

2.2 클래스 원칙

클래스 = 기능 단위
예:

class TestProductList:
class TestProductDetail:
class TestPaymentConfirm:

Happy/Boundary/Exception을 클래스로 분리하지 않는다.

2.3 메서드 원칙

메서드 = 시나리오 (상황 + 기대결과)

네이밍 규칙:

test_<상황>_<기대결과>()

예:

test_filter_by_category_returns_products
test_unauthenticated_user_gets_403
test_invalid_status_returns_400

⚠ 지나치게 긴 이름 금지
핵심 정보만 포함.


📌 3. 작성 규칙
3.1 AAA 패턴

반드시 사용 및 설명이 필요하면 2줄 이내로 작성

# Arrange
# Act
# Assert

3.2 Factory 중심

테스트 데이터는 반드시 Factory로 생성

fixture는 인증/클라이언트 등 최소한만 사용

3.3 응답 검증

전체 필드 검증 금지

핵심 필드만 검증

Serializer의 내부 검증을 중복 테스트하지 않음

3.4 메서드 순서 (클래스 내부)

Happy

Boundary

Exception

※ pytest의 알파벳 순서 그대로 사용.

3.5 금지사항 (필수 이해)

테스트 파일에서 아래는 금지:

과도한 assert message, print, try/except

상태 코드는 숫자 대신 http 상수를 사용한다

전체 serializer 필드 검증

fixture 남용

dummy 값 의미 없이 넣는 것

비즈니스 의미 없는 값은 사용 금지

테스트는 독립적으로 실행되어야 한다

테스트 주석은 Why 중심으로 최소한만 작성


📌 4. 고급 규칙 (과하지 않은 수준만 포함)

반복 입력 → @pytest.mark.parametrize 사용(필요한 범위내에서)

인증/권한 테스트는 최소한만 포함


📌 5. 작성 단계 (AI와 사람이 모두 따라야 함)

도메인 확인

기능 목록으로 클래스 나누기

클래스 내부 시나리오(메서드) 설계

Factory로 데이터 생성

AAA 기반 테스트 작성

불필요한 검증 제거

parametrize 적용 가능한지 확인


📌 6. 리뷰 체크리스트

작성 후 자체 코드 리뷰를 통해 최선의 방법이였는지 확인
(불필요한 검증 제거, 케이스 누락 여부 확인, 구조 일관성 유지)

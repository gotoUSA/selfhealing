# test_schema_discovery.py
# 스키마 유효성 검증 테스트
# 원본 줄: 1-269 (docstring + imports + fixture + helper functions + TestSchemaDiscovery)

"""
OpenAPI 스키마 기반 API Contract 테스트 (Schemathesis Phase 2)
=============================================================

📋 개요
-------
Schemathesis 4.x를 사용하여 OpenAPI 스키마와 실제 API 응답이 일치하는지 자동으로 검증합니다.
이 테스트는 API 문서(스키마)와 실제 구현 간의 불일치를 조기에 발견하기 위해 설계되었습니다.

🎯 테스트 목적
-------------
1. **스키마 일치 검증**: OpenAPI 문서와 실제 응답 구조가 일치하는지 확인
2. **타입 검증**: 필드 타입(string, int, bool 등)이 스키마와 일치하는지 확인
3. **필수 필드 검증**: required 필드가 응답에 포함되어 있는지 확인
4. **에러 응답 검증**: 4xx 에러도 일관된 형식을 갖추는지 확인
5. **Contract 보장**: API 변경 시 Consumer에 영향 여부 조기 탐지

📊 테스트 클래스 구조
-------------------
1. TestSchemaDiscovery (3 tests)
   - 스키마가 유효한지, 필수 엔드포인트가 정의되어 있는지 확인

2. TestPublicEndpointsContract (5 tests)
   - 인증 없이 접근 가능한 공개 API + 스키마 검증

3. TestAuthenticatedEndpointsContract (17 tests)
   - JWT 인증이 필요한 엔드포인트 + 스키마 검증

4. TestPathParameterEdgeCases (12 tests)
   - 경로 파라미터({id}) 다양한 형식 테스트
   - 음수/문자열/float/UUID 오염 등 엣지 케이스

5. TestQueryParameterEdgeCases (6 tests) ⭐ 신규
   - 쿼리 파라미터 검증 (page, search, ordering)
   - SQL Injection, XSS 등 악성 입력 처리

6. TestAccessControlContracts (3 tests) ⭐ 신규
   - 타인 주문 접근 → 403/404
   - 일반 사용자 → 판매자 API 접근 거부
   - 비활성 상품 접근 → 404

7. TestErrorResponseContracts (10+ tests)
   - 실패 케이스의 에러 응답 구조 검증
   - 400/401/403/404 응답이 일관된 형식인지 확인

8. TestSchemathesisNativeValidation (3 tests)
   - Schemathesis case.validate_response() 활용
   - 자동 스키마 검증

9. TestPostEndpointsContract (8 tests)
   - POST 요청의 성공 + 실패 케이스 모두 검증

10. TestPostFailureContracts (6 tests) ⭐ 신규
    - 중복 사용자 등록, 재고 부족, 비밀번호 불일치 등
    - 비즈니스 규칙 위반 시 에러 응답 검증

11. TestStrictSchemaValidation (2 tests) ⭐ 신규
    - strict=True로 추가 필드 검증
    - 스키마 drift 감지

12. TestFullSchemaValidation (3 tests, @slow)
    - 전체 엔드포인트 스키마 준수 검증

🚀 실행 방법
-----------
```bash
# 스키마 테스트만 실행
pytest shopping/tests/schema/test_api_contract.py -v -m schema

# 느린 테스트 제외
pytest shopping/tests/schema/test_api_contract.py -v -m "schema and not slow"

# 특정 클래스만 실행
pytest shopping/tests/schema/test_api_contract.py -v -k "TestErrorResponseContracts"

# 새로 추가된 테스트만 실행
pytest shopping/tests/schema/test_api_contract.py -v -k "TestQueryParameter or TestAccessControl or TestPostFailure"
```

⚠️ 제외된 엔드포인트 (conftest.py EXCLUDED_ENDPOINTS)
----------------------------------------------------
- Webhook: /api/webhooks/toss/ (외부 서비스 콜백)
- 이메일 발송: /api/auth/password/reset/request/ 등 (실제 발송됨)
- 소셜 로그인: /api/auth/social/* (외부 OAuth 의존)
- 테스트 페이지: /api/payment/test/ (HTML 반환)

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터, Response Schema Helpers
- test_fuzz.py: Phase 5.1 - Fuzz 테스트 (무작위 입력)
- test_negative.py: Phase 5.2 - Negative 테스트 (의도적 잘못된 입력)
- test_stateful_workflow.py: Phase 3 - Stateful 워크플로우 테스트

📅 작성 정보
-----------
- 작성일: 2025-12-05
- 최종 수정: 2025-12-05 (Query Parameter, Access Control, Failure Contract 추가)
- Schemathesis 버전: 4.6.7
"""

import json

import pytest
import schemathesis



# ==========================================
# 📦 스키마 로드 Fixture
# ==========================================


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    🔧 OpenAPI 스키마 로드

    Django 테스트 클라이언트로 스키마를 가져와서 schemathesis 스키마 객체 생성.
    scope="module"로 모듈당 한 번만 로드하여 성능 최적화.

    Returns:
        schemathesis.Schema: 파싱된 OpenAPI 스키마 객체
    """
    from django.test import Client

    with django_db_blocker.unblock():
        client = Client()
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200, "OpenAPI 스키마를 가져올 수 없습니다"
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)


# ==========================================
# 🔧 헬퍼 함수
# ==========================================


def get_operations_by_path(schema, path_contains: str, method: str = None):
    """
    스키마에서 특정 경로 패턴의 operation 추출

    Args:
        schema: Schemathesis 스키마 객체
        path_contains: 경로에 포함되어야 할 문자열
        method: HTTP 메서드 필터 (None이면 모두)

    Returns:
        list: 매칭되는 operation 목록 (path parameter 있는 것 제외)
    """
    operations = []
    for result in schema.get_all_operations():
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        if "{" in op.path:
            continue

        if path_contains in op.path:
            if method is None or op.method.upper() == method.upper():
                operations.append(op)
    return operations


def get_all_operations(schema, method: str = None, include_path_params: bool = False):
    """
    스키마에서 모든 operation 추출

    Args:
        schema: Schemathesis 스키마 객체
        method: HTTP 메서드 필터 (None이면 모두)
        include_path_params: True이면 path parameter 있는 것도 포함

    Returns:
        list: 매칭되는 operation 목록
    """
    operations = []
    for result in schema.get_all_operations():
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        if not include_path_params and "{" in op.path:
            continue

        if method is None or op.method.upper() == method.upper():
            operations.append(op)
    return operations


# ==========================================
# 🔍 스키마 발견 테스트
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db
class TestSchemaDiscovery:
    """
    📋 스키마 구조 검증 및 엔드포인트 발견

    OpenAPI 스키마가 정상적으로 로드되고 필수 엔드포인트가
    정의되어 있는지 확인합니다.

    ✅ 검증 항목:
    - 스키마 유효성
    - 경로 정의 존재
    - 핵심 엔드포인트 포함
    """

    def test_schema_is_valid(self, openapi_schema):
        """✅ OpenAPI 스키마가 유효한지 확인"""
        # Arrange - openapi_schema is provided by fixture

        # Act & Assert
        assert openapi_schema is not None, "스키마가 로드되지 않음"

    def test_schema_has_paths(self, openapi_schema):
        """✅ 스키마에 경로가 정의되어 있는지 확인"""
        # Arrange - openapi_schema is provided by fixture

        # Act
        operations = list(openapi_schema.get_all_operations())

        # Assert
        assert len(operations) > 0, "스키마에 정의된 엔드포인트가 없습니다"

    def test_critical_endpoints_exist(self, openapi_schema):
        """✅ 핵심 엔드포인트가 스키마에 정의되어 있는지 확인"""
        # Arrange
        critical_paths = [
            "/api/products/",
            "/api/categories/",
            "/api/cart/",
            "/api/orders/",
            "/api/auth/login/",
            "/api/auth/register/",
        ]

        # Act
        all_paths = set()
        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result
            all_paths.add(op.path)

        # Assert
        missing_paths = [p for p in critical_paths if p not in all_paths]
        assert not missing_paths, f"누락된 핵심 엔드포인트: {missing_paths}"

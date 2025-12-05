"""
SSTI (Server-Side Template Injection) 테스트
=============================================

SSTI 공격 시도에 대해 API가 적절히 방어하는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_ssti.py -v --no-cov
"""

import json

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestSSTIInputs:
    """
    🔧 SSTI (Server-Side Template Injection) 테스트

    서버 사이드 템플릿 인젝션 시도에 대해
    API가 적절히 방어하는지 검증합니다.

    📋 테스트 시나리오:
    - Jinja2 템플릿 표현식 주입
    - Django 템플릿 표현식 주입
    - Python 코드 실행 시도

    ⚠️ 주의: 이 테스트는 실제 공격 페이로드를 사용합니다.
    """

    @pytest.mark.parametrize(
        "ssti_payload,description",
        [
            ("{{7*7}}", "Jinja2 기본 표현식"),
            ("{{7*'7'}}", "Jinja2 문자열 곱셈"),
            ("${7*7}", "일반 템플릿 표현식"),
            ("{{config}}", "Jinja2 config 접근"),
            ("{{self}}", "Jinja2 self 접근"),
            ("{%import os%}{{os.popen('id').read()}}", "Jinja2 os 모듈 import"),
            ("{{request.application.__globals__}}", "Jinja2 globals 접근"),
            ("{% debug %}", "Django debug 태그"),
            ("{{settings.SECRET_KEY}}", "Django settings 접근"),
            ("{{''.__class__.__mro__[2].__subclasses__()}}", "Python 클래스 체인"),
        ],
        ids=[
            "jinja2_basic",
            "jinja2_string_mult",
            "generic_template",
            "jinja2_config",
            "jinja2_self",
            "jinja2_os_import",
            "jinja2_globals",
            "django_debug",
            "django_settings",
            "python_class_chain",
        ],
    )
    def test_ssti_attempt_in_search(self, client, ssti_payload, description):
        """
        SSTI 시도 테스트 (검색 파라미터)

        템플릿 인젝션 페이로드를 검색 파라미터에 주입하여
        실제 템플릿으로 실행되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 5xx 에러 발생하지 않음
        - 템플릿 표현식이 평가되지 않음
        - {{7*7}}이 49로 반환되지 않음

        Args:
            ssti_payload: SSTI 페이로드
            description: 페이로드 설명
        """
        # Act - 검색 파라미터로 주입
        response = client.get(f"/api/products/?search={ssti_payload}")

        # Assert - 서버 에러 없음
        assert response.status_code < 500, (
            f"SSTI 페이로드로 서버 에러 발생!\n"
            f"payload={ssti_payload}\n"
            f"description={description}\n"
            f"status_code={response.status_code}"
        )

        # Assert - 템플릿 표현식이 실행되지 않음
        if response.status_code == status.HTTP_200_OK:
            content = response.content.decode("utf-8", errors="ignore")
            # {{7*7}}이 49로 평가되면 SSTI 취약점
            assert "49" not in content or "{{7*7}}" in content, (
                f"SSTI 취약점 발견! 템플릿이 실행됨\n" f"payload={ssti_payload}"
            )

    def test_ssti_attempt_in_post_data(self, client, auth_headers, schema_test_product):
        """
        SSTI 시도 테스트 (POST 데이터)

        상품 문의 등 사용자 입력을 받는 API에 SSTI 페이로드를 주입하여
        저장 및 반환 시 템플릿으로 실행되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 저장 시 에러 발생하지 않음
        - 반환 시 템플릿이 실행되지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        ssti_payloads = [
            "{{7*7}}",
            "{{config}}",
            "${7*7}",
            "{% debug %}",
        ]

        for payload in ssti_payloads:
            data = {"content": payload}

            # Act - 상품 문의 작성 시도
            response = client.post(
                f"/api/products/{schema_test_product.id}/questions/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"SSTI 페이로드로 서버 에러 발생!\n" f"payload={payload}"

            # 성공적으로 저장된 경우, 반환값 확인
            if response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
                content = response.content.decode("utf-8", errors="ignore")
                # {{7*7}}이 49로 평가되면 SSTI 취약점
                if "7*7" in payload:
                    assert "49" not in content or payload in content, (
                        f"SSTI 취약점 발견! 템플릿이 실행됨\n" f"payload={payload}"
                    )

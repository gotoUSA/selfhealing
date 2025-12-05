"""
Path Traversal 테스트
=====================

Path Traversal 공격 시도에 대해 API가 적절히 방어하는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_path_traversal.py -v --no-cov
"""

import io
import json

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPathTraversal:
    """
    🔐 Path Traversal 테스트

    파일 시스템 경로 조작 시도에 대해 API가 적절히 방어하는지 검증합니다.
    """

    @pytest.mark.parametrize(
        "path_payload",
        [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "....//....//....//etc/passwd",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2f",
            "..%252f..%252f..%252f",
        ],
        ids=[
            "unix_path",
            "windows_path",
            "double_slash",
            "url_encoded",
            "double_encoded",
        ],
    )
    def test_path_traversal_attempt(self, client, path_payload):
        """
        Path Traversal 시도 테스트

        파일 시스템 경로 조작 시도에 대해
        적절히 차단되는지 확인합니다.

        🔍 검증 포인트:
        - 404 또는 400 반환 (파일 내용 노출 안 됨)
        - 5xx 에러 발생하지 않음
        """
        # Arrange - path_payload is provided by parametrize

        # Act - 상품 ID 위치에 Path Traversal 시도
        response = client.get(f"/api/products/{path_payload}/")

        # Assert
        assert response.status_code < 500, f"Path Traversal로 서버 에러 발생!\n" f"payload={path_payload}"
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"예상치 못한 응답: {response.status_code}"


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestFileUploadPathTraversal:
    """
    🔐 파일 업로드 Path Traversal 테스트

    파일 업로드 시 파일명에 Path Traversal 패턴을 주입하여
    서버가 적절히 방어하는지 검증합니다.

    📋 테스트 시나리오:
    - 파일명에 상위 디렉토리 이동 패턴 (../)
    - 파일명에 절대 경로 패턴 (/etc/passwd)
    - 파일명에 null 바이트 주입
    - 파일명에 특수 문자 주입
    """

    PATH_TRAVERSAL_FILENAMES = [
        ("../../../etc/passwd", "unix_parent_dirs"),
        ("..\\..\\..\\windows\\system32\\config\\sam", "windows_parent_dirs"),
        ("....//....//etc/passwd", "double_slash_bypass"),
        ("/etc/passwd", "absolute_unix_path"),
        ("C:\\windows\\system32\\config\\sam", "absolute_windows_path"),
        ("test.jpg\x00.php", "null_byte_injection"),
        (".htaccess", "hidden_file"),
        ("..%2f..%2f..%2fetc%2fpasswd", "url_encoded_traversal"),
        ("..%252f..%252f..%252fetc%252fpasswd", "double_encoded_traversal"),
        ("....//....//....//etc/shadow", "shadow_file_attempt"),
        ("..\\..\\..\\.ssh\\id_rsa", "ssh_key_attempt"),
        ("../../../proc/self/environ", "proc_environ_attempt"),
    ]

    def _create_test_image_file(self, filename: str):
        """테스트용 이미지 파일 생성"""
        # 간단한 1x1 PNG 이미지 바이트
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        file_obj = io.BytesIO(png_bytes)
        file_obj.name = filename
        return file_obj

    @pytest.mark.parametrize(
        "malicious_filename,test_id",
        PATH_TRAVERSAL_FILENAMES,
        ids=[item[1] for item in PATH_TRAVERSAL_FILENAMES],
    )
    def test_image_upload_path_traversal(self, client, auth_headers, schema_test_product, malicious_filename, test_id):
        """
        이미지 업로드 시 Path Traversal 시도 테스트

        상품 이미지 업로드 시 악의적인 파일명을 주입하여
        서버가 적절히 방어하는지 확인합니다.

        🔍 검증 포인트:
        - 서버 에러(5xx) 발생하지 않음
        - 파일이 의도하지 않은 위치에 저장되지 않음
        - 400 에러 또는 파일명 정규화 후 정상 처리
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 악의적인 파일명으로 이미지 파일 생성
        test_file = self._create_test_image_file(malicious_filename)

        # Act - 상품 이미지 업로드 시도
        # 실제 API 엔드포인트에 맞게 조정 필요
        # 옵션 1: 상품 수정 시 이미지 추가
        # 옵션 2: 별도 이미지 업로드 API

        # 여기서는 multipart form data로 직접 전송 시도
        response = client.post(
            f"/api/products/{schema_test_product.id}/images/",
            data={"image": test_file, "alt_text": "테스트 이미지"},
            format="multipart",
            **headers,
        )

        # Assert - 서버 에러 없음
        assert response.status_code < 500, (
            f"Path Traversal 파일명으로 서버 에러!\n" f"filename={malicious_filename}\n" f"status_code={response.status_code}"
        )

        # 만약 성공했다면 저장된 경로 확인
        if response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
            response_data = response.json()

            # 응답에 파일 경로가 포함된 경우
            saved_path = response_data.get("image") or response_data.get("image_url", "")

            # Path Traversal 패턴이 그대로 저장되면 안 됨
            dangerous_patterns = ["../", "..\\", "/etc/", "C:\\", "\\windows\\"]
            for pattern in dangerous_patterns:
                assert pattern not in saved_path, (
                    f"위험한 경로 패턴이 저장된 파일명에 포함됨!\n" f"pattern={pattern}\n" f"saved_path={saved_path}"
                )

    def test_file_upload_with_directory_structure(self, client, auth_headers, schema_test_product):
        """
        디렉토리 구조가 포함된 파일명 업로드 테스트

        파일명에 디렉토리 구분자가 포함된 경우
        서버가 적절히 처리하는지 확인합니다.

        🔍 검증 포인트:
        - 디렉토리 구조가 무시되거나 에러 반환
        - 서버 에러 발생하지 않음
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        directory_filenames = [
            "subdir/image.jpg",
            "deeply/nested/path/image.png",
            "uploads/../../../image.gif",
        ]

        for filename in directory_filenames:
            test_file = self._create_test_image_file(filename)

            response = client.post(
                f"/api/products/{schema_test_product.id}/images/",
                data={"image": test_file, "alt_text": "디렉토리 테스트"},
                format="multipart",
                **headers,
            )

            # Assert - 서버 에러 없음
            assert response.status_code < 500, (
                f"디렉토리 경로 파일명으로 서버 에러!\n" f"filename={filename}\n" f"status_code={response.status_code}"
            )

    def test_file_upload_with_special_characters(self, client, auth_headers, schema_test_product):
        """
        특수 문자가 포함된 파일명 업로드 테스트

        파일명에 다양한 특수 문자가 포함된 경우
        서버가 적절히 처리하는지 확인합니다.

        🔍 검증 포인트:
        - 특수 문자 처리 (제거 또는 인코딩)
        - 서버 에러 발생하지 않음
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        special_filenames = [
            "image<script>.jpg",
            "image|cat /etc/passwd.jpg",
            "image;rm -rf /.jpg",
            "image`whoami`.jpg",
            "image$(id).jpg",
            "image%00.jpg.php",
            "image\x00.jpg",
        ]

        for filename in special_filenames:
            test_file = self._create_test_image_file(filename)

            response = client.post(
                f"/api/products/{schema_test_product.id}/images/",
                data={"image": test_file, "alt_text": "특수문자 테스트"},
                format="multipart",
                **headers,
            )

            # Assert - 서버 에러 없음
            assert response.status_code < 500, (
                f"특수문자 파일명으로 서버 에러!\n" f"filename={repr(filename)}\n" f"status_code={response.status_code}"
            )

    def test_file_extension_bypass_attempts(self, client, auth_headers, schema_test_product):
        """
        파일 확장자 우회 시도 테스트

        이중 확장자, null 바이트 등을 사용한
        확장자 우회 시도에 대해 검증합니다.

        🔍 검증 포인트:
        - 이중 확장자 (.jpg.php) 차단
        - null 바이트 우회 차단
        - 허용되지 않은 확장자 거부
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        bypass_filenames = [
            ("image.jpg.php", "double_extension_php"),
            ("image.jpg.exe", "double_extension_exe"),
            ("image.jpg.sh", "double_extension_sh"),
            ("image.jpg%00.php", "null_byte_urlencoded"),
            ("image.pHp", "case_variation"),
            ("image.jpg.phtml", "alternate_php_extension"),
        ]

        for filename, test_id in bypass_filenames:
            test_file = self._create_test_image_file(filename)

            response = client.post(
                f"/api/products/{schema_test_product.id}/images/",
                data={"image": test_file, "alt_text": "확장자 우회 테스트"},
                format="multipart",
                **headers,
            )

            # Assert - 서버 에러 없음
            assert response.status_code < 500, (
                f"확장자 우회 파일명으로 서버 에러!\n"
                f"filename={filename}\n"
                f"test_id={test_id}\n"
                f"status_code={response.status_code}"
            )

            # 만약 업로드가 성공했다면 저장된 파일이 실행 가능하지 않아야 함
            if response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
                response_data = response.json()
                saved_path = response_data.get("image") or response_data.get("image_url", "")

                # 위험한 확장자가 실행 가능한 위치에 저장되면 안 됨
                dangerous_extensions = [".php", ".exe", ".sh", ".phtml", ".asp", ".aspx", ".jsp"]
                for ext in dangerous_extensions:
                    assert not saved_path.lower().endswith(ext), (
                        f"위험한 확장자가 실행 가능한 형태로 저장됨!\n" f"saved_path={saved_path}"
                    )

    def test_media_path_traversal_via_url(self, client, schema_test_product):
        """
        미디어 URL을 통한 Path Traversal 시도 테스트

        이미지 URL 접근 시 Path Traversal을 시도하여
        서버가 적절히 방어하는지 확인합니다.

        🔍 검증 포인트:
        - /media/../../../etc/passwd 형태 접근 차단
        - 404 또는 400 반환
        """
        traversal_urls = [
            "/media/../../../etc/passwd",
            "/media/product/../../etc/passwd",
            "/media/product/%2e%2e/%2e%2e/etc/passwd",
            "/static/../../../etc/passwd",
        ]

        for url in traversal_urls:
            response = client.get(url)

            # Assert - 서버 에러 없음
            assert response.status_code < 500, f"미디어 URL Path Traversal로 서버 에러!\n" f"url={url}"

            # 민감한 파일 내용이 노출되면 안 됨
            if response.status_code == status.HTTP_200_OK:
                content = response.content.decode("utf-8", errors="ignore")
                sensitive_patterns = ["root:", "daemon:", "bin/bash", "[extensions]"]
                for pattern in sensitive_patterns:
                    assert pattern not in content, f"민감한 파일 내용 노출!\n" f"url={url}\n" f"pattern={pattern}"

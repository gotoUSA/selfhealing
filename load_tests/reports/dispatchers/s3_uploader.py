"""
Load Test Reports - S3 Dispatcher (Interface Only).

AWS S3에 보고서 업로드.
⚠️ 이 파일은 인터페이스만 정의합니다. 실제 구현은 추후 진행합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from typing import Any, Optional

from .base import DispatcherInterface


class S3Dispatcher(DispatcherInterface):
    """
    AWS S3에 보고서 업로드.
    
    ⚠️ 인터페이스만 정의됨. 실제 구현 필요.
    """
    
    def __init__(
        self,
        bucket: Optional[str] = None,
        prefix: str = "load-test-reports/",
        region: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ):
        """
        S3Dispatcher 초기화.
        
        Args:
            bucket: S3 버킷 이름
            prefix: 객체 키 접두사
            region: AWS 리전
            access_key_id: AWS Access Key ID (선택, 환경변수 사용 권장)
            secret_access_key: AWS Secret Access Key (선택, 환경변수 사용 권장)
        """
        self.bucket = bucket
        self.prefix = prefix
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
    
    def dispatch(self, report: Any, **kwargs) -> bool:
        """
        보고서를 S3에 업로드.
        
        ⚠️ 미구현. NotImplementedError 발생.
        
        Args:
            report: 전송할 보고서 객체
            **kwargs: 추가 옵션
                - upload_json: JSON 파일 업로드 여부 (기본: True)
                - upload_markdown: Markdown 파일 업로드 여부 (기본: True)
            
        Raises:
            NotImplementedError: 아직 구현되지 않음
        """
        raise NotImplementedError(
            "S3Dispatcher is not implemented yet. "
            "Please implement dispatch() method with boto3 S3 integration."
        )
    
    def is_available(self) -> bool:
        """
        S3 버킷이 설정되어 있으면 사용 가능.
        
        Returns:
            bool: bucket이 설정되어 있으면 True
        """
        return self.bucket is not None
    
    def _generate_object_key(self, report: Any, extension: str) -> str:
        """
        S3 객체 키 생성.
        
        Args:
            report: 보고서 객체
            extension: 파일 확장자 (예: "json", "md")
            
        Returns:
            str: S3 객체 키
        """
        from datetime import datetime
        
        timestamp = datetime.now().strftime("%Y/%m/%d")
        stage_id = report.metrics.stage_id
        
        return f"{self.prefix}{timestamp}/{stage_id}.{extension}"
    
    def list_reports(self, date_prefix: Optional[str] = None) -> list:
        """
        S3에 저장된 보고서 목록 조회.
        
        ⚠️ 미구현.
        
        Args:
            date_prefix: 날짜 접두사 필터 (예: "2025/12/28")
            
        Returns:
            list: 보고서 객체 키 목록
        """
        raise NotImplementedError("list_reports() is not implemented yet.")
    
    def download_report(self, object_key: str, local_path: str) -> bool:
        """
        S3에서 보고서 다운로드.
        
        ⚠️ 미구현.
        
        Args:
            object_key: S3 객체 키
            local_path: 로컬 저장 경로
            
        Returns:
            bool: 성공 시 True
        """
        raise NotImplementedError("download_report() is not implemented yet.")

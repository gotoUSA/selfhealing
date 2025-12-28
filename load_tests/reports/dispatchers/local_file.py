"""
Load Test Reports - Local File Dispatcher.

로컬 파일 시스템에 보고서를 저장하는 Dispatcher.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
import os
from datetime import datetime
from typing import Any, Dict, Optional

from .base import DispatcherInterface


class LocalFileDispatcher(DispatcherInterface):
    """
    로컬 파일 시스템에 보고서 저장.
    
    JSON, Markdown, 디버그 로그 등을 로컬 파일로 저장합니다.
    """
    
    def __init__(
        self,
        base_dir: str,
        json_enabled: bool = True,
        markdown_enabled: bool = True,
        console_enabled: bool = True,
    ):
        """
        LocalFileDispatcher 초기화.
        
        Args:
            base_dir: 보고서 저장 기본 디렉토리
            json_enabled: JSON 파일 저장 여부
            markdown_enabled: Markdown 파일 저장 여부
            console_enabled: 콘솔 출력 여부
        """
        self.base_dir = base_dir
        self.json_enabled = json_enabled
        self.markdown_enabled = markdown_enabled
        self.console_enabled = console_enabled
    
    def dispatch(
        self,
        report: Any,
        filename_prefix: Optional[str] = None,
        include_timestamp: bool = True,
        debug_logs: Optional[list] = None,
        **kwargs,
    ) -> bool:
        """
        보고서를 로컬 파일로 저장.
        
        Args:
            report: 전송할 보고서 객체
            filename_prefix: 파일명 접두사 (기본: stage_id)
            include_timestamp: 파일명에 타임스탬프 포함 여부
            debug_logs: 디버그 로그 목록 (저장할 경우)
            **kwargs: 추가 옵션
            
        Returns:
            bool: 성공 시 True
        """
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            
            # 파일명 생성
            prefix = filename_prefix or report.metrics.stage_id
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") if include_timestamp else ""
            base_filename = f"{prefix}_{timestamp}" if timestamp else prefix
            
            saved_files = {}
            
            # 콘솔 출력
            if self.console_enabled:
                report.print_console()
            
            # JSON 저장
            if self.json_enabled:
                json_path = os.path.join(self.base_dir, f"{base_filename}.json")
                report.to_json(json_path)
                saved_files["json"] = json_path
                print(f"💾 JSON Report: {json_path}")
            
            # Markdown 저장
            if self.markdown_enabled:
                md_path = os.path.join(self.base_dir, f"{base_filename}.md")
                report.to_markdown_file(md_path)
                saved_files["markdown"] = md_path
                print(f"💾 Markdown Report: {md_path}")
            
            # 디버그 로그 저장
            if debug_logs:
                debug_path = os.path.join(self.base_dir, f"{base_filename}_debug.log")
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(debug_logs))
                saved_files["debug"] = debug_path
                print(f"💾 Debug Log: {debug_path}")
            
            return True
            
        except Exception as e:
            print(f"❌ LocalFileDispatcher Error: {e}")
            return False
    
    def is_available(self) -> bool:
        """
        항상 사용 가능 (로컬 파일 시스템).
        
        Returns:
            bool: True
        """
        return True
    
    def dispatch_multiple(
        self,
        reports: list,
        **kwargs,
    ) -> Dict[str, bool]:
        """
        여러 보고서를 한 번에 저장.
        
        Args:
            reports: 보고서 객체 목록
            **kwargs: dispatch()에 전달할 옵션
            
        Returns:
            Dict[str, bool]: {stage_id: 성공여부}
        """
        results = {}
        for report in reports:
            stage_id = report.metrics.stage_id
            results[stage_id] = self.dispatch(report, **kwargs)
        return results

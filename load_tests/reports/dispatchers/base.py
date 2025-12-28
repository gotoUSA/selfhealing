"""
Load Test Reports - Dispatcher Base Interface.

보고서 전송 인터페이스 정의.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

# Forward declaration to avoid circular imports
# from ..base_report import BaseReport


class DispatcherInterface(ABC):
    """
    보고서 전송 인터페이스.
    
    모든 Dispatcher는 이 인터페이스를 구현해야 합니다.
    """
    
    @abstractmethod
    def dispatch(self, report: Any, **kwargs) -> bool:
        """
        보고서 전송.
        
        Args:
            report: 전송할 보고서 객체 (BaseReport 또는 하위 클래스)
            **kwargs: 추가 옵션
            
        Returns:
            bool: 성공 시 True, 실패 시 False
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Dispatcher 사용 가능 여부 확인.
        
        Returns:
            bool: 사용 가능하면 True
        """
        pass
    
    def get_name(self) -> str:
        """
        Dispatcher 이름 반환.
        
        Returns:
            str: Dispatcher 이름
        """
        return self.__class__.__name__

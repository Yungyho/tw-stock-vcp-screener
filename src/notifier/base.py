from abc import ABC, abstractmethod
from typing import Any

class BaseNotifier(ABC):
    """
    通知器的基礎類別
    """
    
    @abstractmethod
    def send_text(self, message: str) -> bool:
        """發送純文字訊息"""
        ...
    
    @abstractmethod  
    def send_scan_report(self, results: list[dict], scan_date: str) -> bool:
        """發送掃描報告"""
        ...

"""
Load Test Reports - Dispatchers Package.

보고서 전송 Dispatcher 모음.
"""

from .base import DispatcherInterface
from .local_file import LocalFileDispatcher
from .slack import SlackDispatcher
from .s3_uploader import S3Dispatcher

__all__ = [
    "DispatcherInterface",
    "LocalFileDispatcher",
    "SlackDispatcher",
    "S3Dispatcher",
]

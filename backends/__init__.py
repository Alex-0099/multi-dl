"""
Exposes all backend adapters.
"""

from backends.base import BaseBackend
from backends.ytdlp_backend import YtDlpBackend
from backends.gallerydl_backend import GalleryDlBackend
from backends.telegram_backend import TelegramDlBackend

__all__ = [
    "BaseBackend",
    "YtDlpBackend",
    "GalleryDlBackend",
    "TelegramDlBackend",
]

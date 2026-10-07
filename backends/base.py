"""
Abstract base class defining the contract for all backend downloaders.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from core.models import ArchiveEntry, DownloadProgress, DownloadTask


class BaseBackend(ABC):
    """Abstract interface that every downloader adapter must implement."""

    name: str = "base"
    supported_domains: list[str] = []

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config: Dict[str, Any] = config or {}

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """
        Check if this backend can handle the given URL.
        Must return True if supported, False otherwise.
        """
        pass

    @abstractmethod
    async def extract_info(self, url: str) -> Dict[str, Any]:
        """
        Extract metadata without starting an actual download.
        Useful for inspecting titles, formats, authors, and file size.
        """
        pass

    @abstractmethod
    async def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> ArchiveEntry:
        """
        Execute the download job.
        Periodically invokes progress_callback with normalized DownloadProgress updates.
        Returns a populated ArchiveEntry upon completion.
        """
        pass

"""
Adapter backend wrapping Telegram downloads using Telethon.
Integrated directly from local telegram-dl code into MULTI_DOWNLOADER.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import uuid

from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from backends.base import BaseBackend
from core.exceptions import DownloadFailedError
from core.models import ArchiveEntry, DownloadProgress, DownloadTask, MediaType


class TelegramDlBackend(BaseBackend):
    """
    Telethon-based Telegram downloader backend.
    Handles message links (e.g. t.me/c/123/456, t.me/channel/456).
    """

    name: str = "telegram-dl"
    supported_domains = ["t.me", "telegram.me"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.client: Optional[TelegramClient] = None
        self._init_client()

    def _init_client(self) -> None:
        api_id = self.config.get("api_id")
        api_hash = self.config.get("api_hash")
        session_name = self.config.get("session_name", "telegram_dl_session")

        if api_id and api_hash:
            try:
                self.client = TelegramClient(session_name, int(api_id), str(api_hash))
            except Exception:
                self.client = None

    def can_handle(self, url: str) -> bool:
        """Check if URL points to a Telegram domain."""
        url_lower = url.lower()
        return "t.me/" in url_lower or "telegram.me/" in url_lower

    async def extract_info(self, url: str) -> Dict[str, Any]:
        """Extract message info and media attributes."""
        chat_id, message_id = self._parse_link(url)
        return {
            "chat_id": chat_id,
            "message_id": message_id,
            "url": url,
        }

    async def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> ArchiveEntry:
        """Downloads Telegram media for the specified message link."""
        if not self.client:
            raise DownloadFailedError(
                "TelegramClient not configured. Please supply api_id and api_hash in config.json."
            )

        if not self.client.is_connected():
            await self.client.connect()

        if not await self.client.is_user_authorized():
            raise DownloadFailedError(
                "Telegram account not authorized. Please complete session login first."
            )

        chat_id, message_id = self._parse_link(task.url)
        if not chat_id or not message_id:
            raise DownloadFailedError(f"Could not parse valid chat and message ID from {task.url}")

        out_dir = Path(task.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            entity = await self.client.get_entity(chat_id)
            message = await self.client.get_messages(entity, ids=message_id)
        except Exception as e:
            raise DownloadFailedError(f"Failed to fetch Telegram message {message_id}: {e}")

        if not message or not message.media:
            raise DownloadFailedError(f"Telegram message {message_id} contains no media.")

        # Progress hook for Telethon download_media
        def _telethon_progress(received_bytes: int, total_bytes: int):
            if progress_callback:
                prog = DownloadProgress(
                    downloaded_bytes=received_bytes,
                    total_bytes=total_bytes,
                )
                prog.update_percent()
                progress_callback(prog)

        try:
            downloaded_path = await self.client.download_media(
                message,
                file=str(out_dir),
                progress_callback=_telethon_progress,
            )
        except Exception as e:
            raise DownloadFailedError(f"Error downloading Telegram media: {e}")

        if not downloaded_path:
            raise DownloadFailedError("Telethon returned empty download path.")

        path_obj = Path(downloaded_path)
        media_type = MediaType.UNKNOWN
        if isinstance(message.media, MessageMediaPhoto):
            media_type = MediaType.IMAGE
        elif isinstance(message.media, MessageMediaDocument):
            media_type = MediaType.VIDEO if (message.video or message.document.mime_type.startswith("video/")) else MediaType.DOCUMENT

        return ArchiveEntry(
            id=str(uuid.uuid4()),
            url=task.url,
            file_path=str(path_obj.resolve()),
            file_name=path_obj.name,
            file_hash=None,
            file_size=path_obj.stat().st_size if path_obj.is_file() else None,
            backend=self.name,
            source_site="telegram",
            media_type=media_type,
            metadata={
                "chat_id": str(chat_id),
                "message_id": message_id,
                "date": message.date.isoformat() if message.date else None,
            },
        )

    @staticmethod
    def _parse_link(url: str):
        """Extracts chat identifier and message id from t.me links."""
        import re
        match = re.search(r'(?:https?://)?(?:t|telegram)\.me/(c/)?([a-zA-Z0-9_.-]+)/(\d+)', url)
        if not match:
            return None, None
        
        is_private = bool(match.group(1))
        chat_str = match.group(2)
        message_id = int(match.group(3))

        chat_id: Any = chat_str
        try:
            chat_id = int(chat_str)
            if is_private and chat_id > 0:
                chat_id = int(f"-100{chat_id}")
        except ValueError:
            pass

        return chat_id, message_id

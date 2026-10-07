"""
Adapter backend wrapping gallery-dl via its Python API.
Ensures config isolation by resetting memory config state and loading from modular configs.
"""

import asyncio
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional
import uuid

import gallery_dl
from gallery_dl import config as gdl_config
from gallery_dl import job

from backends.base import BaseBackend
from core.exceptions import DownloadFailedError
from core.models import ArchiveEntry, DownloadProgress, DownloadTask, MediaType


class _GalleryDlOutputTracker:
    """Proxies gallery-dl output writer to track downloaded files and trigger progress callbacks."""

    def __init__(self, original_out: Any, progress_callback: Optional[Callable[[DownloadProgress], None]] = None):
        self._orig = original_out
        self.progress_callback = progress_callback
        self.downloaded_files: List[str] = []

    def success(self, path: str) -> None:
        self.downloaded_files.append(path)
        p = Path(path)
        sys.stdout.write(f"\r\033[K📷 [SAVED] {p.name}\n")
        sys.stdout.flush()

        if self.progress_callback:
            prog = DownloadProgress(
                file_index=len(self.downloaded_files),
                current_file=p.name,
                downloaded_bytes=p.stat().st_size if p.exists() else 0,
            )
            self.progress_callback(prog)

        if self._orig and hasattr(self._orig, "success"):
            try:
                self._orig.success(path)
            except Exception:
                pass

    def skip(self, path: str) -> None:
        p = Path(path)
        sys.stdout.write(f"\r\033[K⏭️  [SKIPPED] {p.name} (already exists / archived)\n")
        sys.stdout.flush()
        if self._orig and hasattr(self._orig, "skip"):
            try:
                self._orig.skip(path)
            except Exception:
                pass

    def error(self, msg: str) -> None:
        sys.stdout.write(f"\r\033[K❌ [GALLERY-DL ERROR] {msg}\n")
        sys.stdout.flush()
        if self._orig and hasattr(self._orig, "error"):
            try:
                self._orig.error(msg)
            except Exception:
                pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)


class GalleryDlBackend(BaseBackend):
    """Integrates gallery-dl via its Python library interfaces with modular config isolation."""

    name: str = "gallery-dl"

    def _configure_job(self, out_dir: Optional[Path] = None, disable_archive: bool = True) -> None:
        """Resets memory state and loads configuration from the project's config file."""
        gdl_config.clear()

        # Load engine-specific config file (e.g. configs/gallery-dl.json)
        config_file = self.config.get("config_file", "configs/gallery-dl.json")
        cfg_path = Path(config_file)
        if not cfg_path.exists():
            example_path = cfg_path.with_name("gallery-dl.example.json")
            if example_path.exists():
                cfg_path = example_path
        if cfg_path.exists():
            gdl_config.load([str(cfg_path.resolve())])

        # Enforce MULTI_DOWNLOADER output directory override
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            gdl_config.set(("extractor",), "base-directory", str(out_dir.resolve()))

        # Pause gallery-dl internal sqlite archives during test mode if requested
        if disable_archive:
            gdl_config.set(("extractor",), "archive", None)

    def can_handle(self, url: str) -> bool:
        """Query gallery-dl's extractor registry for support."""
        try:
            extractor = gallery_dl.extractor.find(url)
            return extractor is not None
        except Exception:
            return False

    async def extract_info(self, url: str) -> Dict[str, Any]:
        """Extract metadata without downloading media."""
        def _extract() -> Dict[str, Any]:
            self._configure_job()
            data_job = job.DataJob(url)
            data_job.run()
            return getattr(data_job, "data", {})

        try:
            return await asyncio.to_thread(_extract)
        except Exception as e:
            raise DownloadFailedError(f"gallery-dl metadata extraction failed: {e}")

    async def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> ArchiveEntry:
        """Download images/galleries using gallery-dl."""
        out_dir = Path(task.output_dir)
        tracker: Optional[_GalleryDlOutputTracker] = None

        def _run_download() -> int:
            nonlocal tracker
            self._configure_job(out_dir=out_dir)

            dl_job = job.DownloadJob(task.url)
            tracker = _GalleryDlOutputTracker(dl_job.out, progress_callback=progress_callback)
            dl_job.out = tracker
            return dl_job.run()

        try:
            status_code = await asyncio.to_thread(_run_download)
            if status_code != 0:
                raise DownloadFailedError(f"gallery-dl returned status code: {status_code}")
        except Exception as e:
            raise DownloadFailedError(f"gallery-dl download failed: {e}")

        downloaded_files = tracker.downloaded_files if tracker else []
        
        # Resolve primary file or folder path
        if downloaded_files:
            primary_path = Path(downloaded_files[0])
            total_size = sum(Path(f).stat().st_size for f in downloaded_files if Path(f).exists())
            file_name = primary_path.name if len(downloaded_files) == 1 else out_dir.name
            file_path = str(primary_path.resolve()) if len(downloaded_files) == 1 else str(out_dir.resolve())
        else:
            # Fallback if extractor downloaded directly or files were already present
            primary_path = out_dir
            total_size = 0
            file_name = out_dir.name
            file_path = str(out_dir.resolve())

        # Determine media type based on extensions
        first_ext = primary_path.suffix.lower() if primary_path.is_file() else ""
        if first_ext in (".mp4", ".webm", ".mkv", ".mov"):
            media_type = MediaType.VIDEO
        elif first_ext in (".mp3", ".m4a", ".aac", ".flac"):
            media_type = MediaType.AUDIO
        elif first_ext in (".zip", ".cbz", ".tar"):
            media_type = MediaType.DOCUMENT
        else:
            media_type = MediaType.IMAGE

        return ArchiveEntry(
            id=str(uuid.uuid4()),
            url=task.url,
            file_path=file_path,
            file_name=file_name,
            file_hash=None,
            file_size=total_size,
            backend=self.name,
            source_site=task.url.split("/")[2] if "://" in task.url else "gallery-dl",
            media_type=media_type,
            metadata={
                "downloaded_files": downloaded_files,
                "file_count": len(downloaded_files),
            },
        )

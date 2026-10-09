"""
Adapter backend wrapping gallery-dl via its Python API.
Ensures config isolation by resetting memory config state and loading from modular configs.
"""

import asyncio
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable, Dict, List, Optional
import uuid

import gallery_dl
from gallery_dl import config as gdl_config
from gallery_dl import job

from backends.base import BaseBackend
from core.exceptions import DownloadFailedError
from core.models import ArchiveEntry, DownloadProgress, DownloadTask, MediaType
from core.terminal import Style, format_bytes


class _GalleryDlLogHandler(logging.Handler):
    """Formats gallery-dl internal logs to match MULTI_DOWNLOADER terminal tags and styling."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            # Suppress benign netrc warning if it ever appears
            if "netrc" in msg.lower() and "no authentication info" in msg.lower():
                return

            if record.levelno >= logging.ERROR:
                sys.stdout.write(f"\r\033[K{Style.tag('❌', 'ERROR', Style.RED)} {Style.error(msg)}\n")
            elif record.levelno >= logging.WARNING:
                sys.stdout.write(f"\r\033[K{Style.tag('⚠️', 'WARNING', Style.YELLOW)} {Style.warning(msg)}\n")
            sys.stdout.flush()
        except Exception:
            pass


class _GalleryDlBatchTracker:
    """
    Renders an in-place dual progress bar for gallery-dl downloads:
    Line 1 (Top): Batch / Gallery progress (e.g. 45/464 images downloaded)
    Line 2 (Bottom): Current file progress (speed, ETA, sizes, filename)
    """

    def __init__(self, progress_callback: Optional[Callable[[DownloadProgress], None]] = None):
        self.progress_callback = progress_callback
        self.total_files: Optional[int] = None
        self.completed_count: int = 0
        self.skipped_count: int = 0
        self.downloaded_files: List[str] = []
        self.skipped_files: List[str] = []
        self._file_bytes: Dict[str, int] = {}

        # Current file metrics
        self.current_filename: str = ""
        self.current_downloaded: int = 0
        self.current_total: Optional[int] = None
        self.current_speed: Optional[float] = None

        self.title_announced: bool = False
        self._lines_printed: int = 0
        self._is_tty: bool = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else True

    @staticmethod
    def _visible_len(s: str) -> int:
        """Returns the visible column width of a string, accounting for ANSI codes and wide characters/emojis."""
        clean = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', s)
        extra = sum(1 for ch in clean if ord(ch) > 0x1F000 or ch in "🖼📥📦🎬📁🚀⚠️❌✅•")
        return len(clean) + extra

    def on_directory(self, kwdict: Dict[str, Any]) -> None:
        """Called when gallery metadata is resolved before downloads begin."""
        album_meta = kwdict.get("album") if isinstance(kwdict.get("album"), dict) else {}
        count = kwdict.get("count") or kwdict.get("total") or album_meta.get("count") or album_meta.get("file_count")
        if count and not self.total_files:
            try:
                self.total_files = int(count)
            except (ValueError, TypeError):
                pass

        title = kwdict.get("title") or album_meta.get("title")
        if title and not self.title_announced:
            self.title_announced = True
            sys.stdout.write(f"\r\033[K{Style.tag('🎬', 'MEDIA', Style.YELLOW)} {Style.white(title)}\n")
            if self.total_files:
                sys.stdout.write(f"\r\033[K{Style.tag('📦', 'BATCH', Style.MAGENTA)} Detected {Style.cyan(str(self.total_files))} files in gallery\n")
            sys.stdout.flush()

    def on_url(self, url: str, kwdict: Dict[str, Any]) -> None:
        """Called for each queued image URL."""
        if not self.total_files:
            album_meta = kwdict.get("album") if isinstance(kwdict.get("album"), dict) else {}
            count = kwdict.get("count") or kwdict.get("total") or album_meta.get("count") or album_meta.get("file_count")
            if count:
                try:
                    self.total_files = int(count)
                except (ValueError, TypeError):
                    pass

        fname = kwdict.get("filename")
        if fname:
            self.current_filename = str(fname)
        self.current_downloaded = 0
        self.current_total = None
        self.current_speed = None

    def start(self, path: str) -> None:
        """Called when download starts for a specific file."""
        self.current_filename = Path(path).name
        self.current_downloaded = 0
        self.current_total = None
        self.current_speed = None
        self._render()

    def progress(self, bytes_total: Optional[int], bytes_downloaded: int, bytes_per_second: int) -> None:
        """Called with live byte stream progress for the current file."""
        self.current_total = bytes_total
        self.current_downloaded = bytes_downloaded
        self.current_speed = float(bytes_per_second) if bytes_per_second else None
        if self.current_filename:
            self._file_bytes[self.current_filename] = bytes_downloaded
        self._render()

    def success(self, path: str) -> None:
        """Called when a file completes downloading successfully."""
        self.downloaded_files.append(path)
        self.completed_count += 1
        p = Path(path)
        self.current_filename = p.name
        if p.exists():
            self.current_total = p.stat().st_size
            self.current_downloaded = self.current_total
            self._file_bytes[p.name] = self.current_total
        self._render()

        if self.progress_callback:
            prog = DownloadProgress(
                file_index=len(self.downloaded_files),
                current_file=p.name,
                downloaded_bytes=self.current_downloaded,
                total_bytes=self.current_total,
            )
            self.progress_callback(prog)

    def skip(self, path: str) -> None:
        """Called when an existing or archived file is skipped."""
        self.skipped_count += 1
        self.completed_count += 1
        p = Path(path)
        self.skipped_files.append(str(p.resolve()))
        self.current_filename = p.name
        if p.exists():
            self._file_bytes[p.name] = p.stat().st_size
        self._render()

    def error(self, msg: str) -> None:
        """Called on gallery-dl error."""
        sys.stdout.write(f"\r\033[K{Style.tag('❌', 'GALLERY-DL ERROR', Style.RED)} {Style.red(msg)}\n")
        self._lines_printed = 0
        sys.stdout.flush()

    def finish(self) -> None:
        """Finalizes the dual progress display when download completes."""
        self._render(done=True)

    def _render(self, done: bool = False) -> None:
        term_width = shutil.get_terminal_size((80, 24)).columns
        max_width = max(40, term_width - 2)

        # Adapt bar width if terminal is narrow
        bar_w = 8 if max_width < 75 else 16

        # --- Line 1: Batch / Gallery Progress ---
        if self.total_files and self.total_files > 0:
            batch_pct = (self.completed_count / self.total_files) * 100.0
            batch_pct_text = f"{batch_pct:5.1f}%"
            batch_bar = Style.progress_bar(batch_pct, width=bar_w)
            batch_count = f"{self.completed_count}/{self.total_files} files"
        else:
            batch_pct = None
            batch_pct_text = "  --% "
            batch_bar = Style.progress_bar(None, width=bar_w)
            batch_count = f"{self.completed_count} files"

        total_batch_bytes = sum(self._file_bytes.values())
        if total_batch_bytes > 0:
            size_color = Style.GREEN if (batch_pct is not None and batch_pct >= 100.0) else Style.WHITE
            size_str = f" {Style.dim('•')} {size_color}{format_bytes(total_batch_bytes)}{Style.RESET}"
        else:
            size_str = ""

        batch_color = Style.GREEN if (batch_pct is not None and batch_pct >= 100.0) else Style.MAGENTA
        tag_batch = Style.tag("🖼️", "BATCH", batch_color)
        pct_color = Style.GREEN if (batch_pct is not None and batch_pct >= 100.0) else Style.CYAN
        skip_notice = f" {Style.dim(f'[{self.skipped_count} skipped]')}" if self.skipped_count else ""

        base_line1 = f"{tag_batch} {batch_bar} {Style.white(batch_count)}"
        pct_part = f" {Style.dim('(')}{pct_color}{batch_pct_text}{Style.RESET}{Style.dim(')')}"

        line1 = f"{base_line1}{size_str}{pct_part}{skip_notice}"
        if self._visible_len(line1) > max_width:
            line1 = f"{base_line1}{size_str}{pct_part}"
            if self._visible_len(line1) > max_width:
                line1 = f"{base_line1}{pct_part}"

        # --- Line 2: Current File Progress ---
        if self.current_total and self.current_total > 0:
            file_pct = (self.current_downloaded / self.current_total) * 100.0
            file_pct_text = f"{file_pct:5.1f}%"
            file_bar = Style.progress_bar(file_pct, width=bar_w)
            file_sizes = f"{format_bytes(self.current_downloaded)} / {format_bytes(self.current_total)}"
            rem_bytes = max(0, self.current_total - self.current_downloaded)
            eta_sec = (rem_bytes / self.current_speed) if (self.current_speed and self.current_speed > 0) else None
        else:
            file_pct = None
            file_pct_text = "  --% "
            file_bar = Style.progress_bar(None, width=bar_w)
            file_sizes = f"{format_bytes(self.current_downloaded)} / ??"
            eta_sec = None

        file_color = Style.GREEN if (file_pct is not None and file_pct >= 100.0) else Style.CYAN
        tag_file = Style.tag("📥", file_pct_text, file_color)
        speed_str = f"{format_bytes(int(self.current_speed))}/s" if self.current_speed else "--/s"
        speed_part = f" {Style.dim('@')} {Style.speed(speed_str)}"

        eta_part = ""
        if eta_sec and eta_sec > 0 and (file_pct is None or file_pct < 100.0):
            eta_m, eta_s = divmod(int(eta_sec), 60)
            eta_part = f" {Style.dim('ETA')} {Style.yellow(f'{eta_m:02d}:{eta_s:02d}')}"

        prefix = f"{tag_file} {file_bar} {Style.white(file_sizes)}{speed_part}"
        prefix_vis = self._visible_len(prefix)

        # Include ETA if it fits comfortably before filename
        if eta_part and (prefix_vis + self._visible_len(eta_part) + 12 <= max_width):
            prefix += eta_part
            prefix_vis = self._visible_len(prefix)

        # Allocate remaining width to filename (leaving 3 cols for ' [' and ']')
        fname = self.current_filename or "..."
        avail_fname = max_width - prefix_vis - 3
        if avail_fname >= 6:
            if len(fname) > avail_fname:
                fname_display = fname[:avail_fname - 3] + "..."
            else:
                fname_display = fname
            fname_part = f" {Style.dim('[')}{Style.white(fname_display)}{Style.dim(']')}"
            line2 = f"{prefix}{fname_part}"
        else:
            line2 = prefix

        if self._is_tty:
            if self._lines_printed == 0:
                sys.stdout.write(f"\r\033[K{line1}\n\r\033[K{line2}")
                self._lines_printed = 2
            else:
                sys.stdout.write(f"\033[A\r\033[K{line1}\n\r\033[K{line2}")

            if done:
                sys.stdout.write("\n")
                self._lines_printed = 0
            sys.stdout.flush()
        else:
            if done:
                sys.stdout.write(f"{line1}\n")
                sys.stdout.flush()

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


class _MultiDlGalleryJob(job.DownloadJob):
    """
    Custom DownloadJob subclass that routes metadata and items to _GalleryDlBatchTracker.
    Fully supports child/sub-jobs spawned by redirect extractors (e.g. Reddit, RedGifs, Twitter).
    """

    def __init__(self, url_or_extr: Any, parent: Any = None, tracker: Optional[_GalleryDlBatchTracker] = None):
        if isinstance(parent, _MultiDlGalleryJob):
            # Child job spawned by gallery-dl: inherit tracker and pass parent to super()
            super().__init__(url_or_extr, parent)
            self.tracker = parent.tracker
        elif isinstance(parent, _GalleryDlBatchTracker):
            # Called with (url, tracker) as positional args
            super().__init__(url_or_extr, None)
            self.tracker = parent
        else:
            # Root job with optional tracker kwarg or parent
            super().__init__(url_or_extr, parent if not isinstance(parent, _GalleryDlBatchTracker) else None)
            self.tracker = tracker or getattr(parent, "tracker", None)

        if self.tracker:
            self.out = self.tracker

    def handle_directory(self, kwdict: Dict[str, Any]):
        if self.tracker and hasattr(self.tracker, "on_directory"):
            self.tracker.on_directory(kwdict)
        return super().handle_directory(kwdict)

    def handle_url(self, url: str, kwdict: Dict[str, Any]):
        if self.tracker and hasattr(self.tracker, "on_url"):
            self.tracker.on_url(url, kwdict)
        return super().handle_url(url, kwdict)

    def on_directory(self, kwdict: Dict[str, Any]):
        """Fallback in case gallery-dl calls on_directory directly on the job."""
        if self.tracker and hasattr(self.tracker, "on_directory"):
            self.tracker.on_directory(kwdict)

    def on_url(self, url: str, kwdict: Dict[str, Any]):
        """Fallback in case gallery-dl calls on_url directly on the job."""
        if self.tracker and hasattr(self.tracker, "on_url"):
            self.tracker.on_url(url, kwdict)


class GalleryDlBackend(BaseBackend):
    """Integrates gallery-dl via its Python library interfaces with modular config isolation."""

    name: str = "gallery-dl"

    def _configure_job(self, out_dir: Optional[Path] = None, disable_archive: bool = True) -> None:
        """Resets memory state and loads configuration from the project's config file."""
        gdl_config.clear()

        # Load engine-specific config file (e.g. configs/gallery-dl.json)
        config_file = self.config.get("config_file", "configs/gallery-dl.json")
        cfg_path = Path(config_file)
        if not cfg_path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent
            cfg_path = project_root / cfg_path
        if not cfg_path.exists():
            example_path = cfg_path.with_name("gallery-dl.example.json")
            if example_path.exists():
                cfg_path = example_path
        if cfg_path.exists():
            gdl_config.load([str(cfg_path.resolve())])

        # Configure fine-grained progress notification for http and ytdl downloaders (100ms interval)
        gdl_config.set(("downloader", "http"), "progress", 0.1)
        gdl_config.set(("downloader", "ytdl"), "progress", 0.1)
        gdl_config.set(("output",), "shorten", False)

        # Enforce MULTI_DOWNLOADER output directory override
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            gdl_config.set(("extractor",), "base-directory", str(out_dir.resolve()))

        # Pause gallery-dl internal sqlite archives during test mode if requested
        if disable_archive:
            gdl_config.set(("extractor",), "archive", None)

        # Disable netrc lookups to prevent unwanted 'No authentication info' warnings
        gdl_config.set((), "netrc", False)

        # Route gallery-dl logger through MULTI_DOWNLOADER terminal handler
        gdl_logger = logging.getLogger("gallery-dl")
        gdl_logger.handlers = [_GalleryDlLogHandler()]
        gdl_logger.propagate = False

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
        tracker: Optional[_GalleryDlBatchTracker] = None

        def _run_download() -> int:
            nonlocal tracker
            self._configure_job(out_dir=out_dir)

            tracker = _GalleryDlBatchTracker(progress_callback=progress_callback)
            dl_job = _MultiDlGalleryJob(task.url, tracker=tracker)
            return dl_job.run()

        try:
            status_code = await asyncio.to_thread(_run_download)
            if tracker:
                tracker.finish()
            if status_code != 0:
                raise DownloadFailedError(f"gallery-dl returned status code: {status_code}")
        except Exception as e:
            if tracker:
                tracker.finish()

            # ── Automatic Failover to cyberdrop-dl ──
            if self.config.get("enable_failover_to_cyberdrop", True):
                try:
                    from backends.cyberdrop_backend import CyberdropDlBackend
                    cdl = CyberdropDlBackend()
                    if cdl.can_handle(task.url):
                        print(f"\n{Style.tag('🔄', 'FAILOVER', Style.YELLOW)} gallery-dl encountered an error ({e}).")
                        print(f"{Style.tag('⚙️', 'FAILOVER', Style.CYAN)} Attempting automatic fallback with {Style.engine_badge('cyberdrop-dl')}...")
                        fallback_task = DownloadTask(
                            url=task.url,
                            backend="cyberdrop-dl",
                            output_dir=task.output_dir,
                            options=task.options,
                        )
                        return await cdl.download(fallback_task, progress_callback=progress_callback)
                except Exception:
                    pass

            raise DownloadFailedError(f"gallery-dl download failed: {e}")

        downloaded_files = tracker.downloaded_files if tracker else []
        skipped_files = tracker.skipped_files if tracker else []
        all_skipped = len(downloaded_files) == 0 and len(skipped_files) > 0
        
        # Resolve primary file or folder path
        files_to_check = downloaded_files if downloaded_files else skipped_files
        if files_to_check:
            primary_path = Path(files_to_check[0])
            total_size = sum(Path(f).stat().st_size for f in files_to_check if Path(f).exists())
            if len(files_to_check) == 1:
                file_name = primary_path.name
                file_path = str(primary_path.resolve())
            else:
                album_dir = primary_path.parent
                file_name = album_dir.name
                file_path = str(album_dir.resolve())
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
                "skipped_files": skipped_files,
                "all_skipped": all_skipped,
                "file_count": len(downloaded_files),
                "skipped_count": len(skipped_files),
            },
        )

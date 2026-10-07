"""
CLI entry point for MULTI_DOWNLOADER.
"""

import argparse
import asyncio
import io
import os
import signal
import sys
from pathlib import Path

# Force standard output to UTF-8 on Windows terminals to support emojis and international titles
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.terminal import Style


def _sigint_handler(signum=None, frame=None):
    """Guarantees instant termination on Ctrl+C on Windows without getting stuck on blocked worker threads."""
    sys.stdout.write(f"\n\n{Style.tag('🛑', 'ABORTED', Style.RED)} {Style.error('Operation cancelled by user.')}\n")
    sys.stdout.flush()
    os._exit(130)


try:
    signal.signal(signal.SIGINT, _sigint_handler)
except (ValueError, AttributeError):
    pass

from backends.gallerydl_backend import GalleryDlBackend
from backends.telegram_backend import TelegramDlBackend
from backends.ytdlp_backend import YtDlpBackend
from core.archive import ArchiveManager
from core.config import ConfigManager
from core.exceptions import MultiDLError
from core.models import DownloadProgress, DownloadTask, TaskStatus
from core.queue_manager import QueueManager
from core.router import URLRouter
from core.updater import EngineUpdater


def format_bytes(size: int) -> str:
    """Formats bytes to human-readable string (KB, MB, GB)."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def print_progress(p: DownloadProgress):
    """Terminal progress renderer with aligned, colored output and visual progress bar."""
    if p.percent is not None:
        percent_text = f"{p.percent:5.1f}%"
        tag_color = Style.GREEN if p.percent >= 100.0 else Style.CYAN
    else:
        percent_text = "  --% "
        tag_color = Style.CYAN

    tag_part = Style.tag("📥", percent_text, tag_color)
    bar_part = Style.progress_bar(p.percent, width=20)

    downloaded_str = format_bytes(p.downloaded_bytes) if p.downloaded_bytes else "0 B"
    total_str = format_bytes(p.total_bytes) if p.total_bytes else "??"
    sizes_part = f"{Style.white(downloaded_str)} / {Style.white(total_str)}"

    speed_val = f"{format_bytes(int(p.speed_bytes_sec))}/s" if p.speed_bytes_sec else "--"
    speed_part = f"{Style.dim('@')} {Style.speed(speed_val)}"

    eta_part = ""
    if p.eta_seconds and p.eta_seconds > 0 and (p.percent is None or p.percent < 100.0):
        eta_m, eta_s = divmod(int(p.eta_seconds), 60)
        eta_part = f" {Style.dim('ETA')} {Style.yellow(f'{eta_m:02d}:{eta_s:02d}')}"

    sys.stdout.write(f"\r\033[K{tag_part} {bar_part} {sizes_part} {speed_part}{eta_part}")
    sys.stdout.flush()


async def download_url(url: str, backend_name: str = None, options: dict = None):
    config = ConfigManager()
    archive = ArchiveManager(config.archive_db_path)
    options = options or {}

    # Initialize Backends
    ytdlp = YtDlpBackend(config.get_backend_config("yt-dlp"))
    gallerydl = GalleryDlBackend(config.get_backend_config("gallery-dl"))
    telegram = TelegramDlBackend(config.get_backend_config("telegram-dl"))

    router = URLRouter()
    router.register_backend(ytdlp)
    router.register_backend(gallerydl)
    router.register_backend(telegram)

    try:
        backend = router.route(url, backend_override=backend_name)
    except MultiDLError as e:
        print(f"\n{Style.tag('❌', 'ROUTING ERROR', Style.RED)} {Style.error(str(e))}")
        return

    print(f"\n{Style.tag('🎯', 'ROUTING', Style.CYAN)} Target: {Style.white(url)}")
    print(f"{Style.tag('⚙️', 'BACKEND', Style.MAGENTA)} Assigned engine: {Style.engine_badge(backend.name)}")

    # Check Archive for existing duplicate (if enabled)
    archive_enabled = config.get("archive", "enabled", True)
    if archive_enabled and config.get("archive", "dedup_by_url", True):
        if archive.is_url_downloaded(url, backend.name):
            print(f"{Style.tag('⚠️', 'SKIPPED', Style.YELLOW)} URL already recorded in archive database.")
            return

    # Organize download directory by downloader -> host/channel
    target_dir = config.download_dir
    if config.get("general", "organize_by_backend", True):
        target_dir = target_dir / backend.name
    if config.get("general", "organize_by_site", True):
        # gallery-dl inherently manages site-level directory naming via {category}
        if backend.name != "gallery-dl":
            host_name = URLRouter.get_host_identifier(url, backend.name)
            target_dir = target_dir / host_name

    task = DownloadTask(
        url=url,
        backend=backend.name,
        output_dir=str(target_dir),
        options=options,
    )

    try:
        print(f"{Style.tag('🚀', 'DOWNLOADING', Style.BLUE)} Starting transfer...")
        progress_cb = None if backend.name == "gallery-dl" else print_progress
        entry = await backend.download(task, progress_callback=progress_cb)
        print(f"\n{Style.tag('✅', 'COMPLETED', Style.GREEN)} {Style.success('Download finished successfully!')}")

        # Format clean path starting with ~downloads\
        raw_path = Path(entry.file_path)
        try:
            rel_path = raw_path.relative_to(config.download_dir.parent)
            clean_display_path = f"~{rel_path}"
        except ValueError:
            clean_display_path = f"~downloads\\{raw_path.name}"

        print(f"{Style.tag('📁', 'SAVED', Style.GREEN)} {Style.path(clean_display_path)}")

        # Compute hash and record in archive (if enabled)
        if archive_enabled:
            entry.file_hash = ArchiveManager.calculate_file_hash(Path(entry.file_path))
            archive.add_entry(entry)
            print(f"{Style.tag('💾', 'ARCHIVE', Style.CYAN)} Logged into database.")

    except MultiDLError as e:
        print(f"\n{Style.tag('❌', 'FAILED', Style.RED)} {Style.error(str(e))}")
    except Exception as e:
        print(f"\n{Style.tag('❌', 'ERROR', Style.RED)} {Style.error(f'Unexpected failure: {e}')}")


def main():
    raw_args = sys.argv[1:]

    # Fast-path for Update command / flag: multi-dl -U / multi-dl --update / multi-dl update [engine]
    if any(arg in ("-U", "--update") for arg in raw_args) or (raw_args and raw_args[0] == "update"):
        remaining = [a for a in raw_args if a not in ("-U", "--update", "update")]
        target = remaining[0].lower() if remaining else "all"
        updater = EngineUpdater()
        if target in ("all", ""):
            updater.update_all()
        elif target in ("yt-dlp", "ytdlp"):
            ok, msg = updater.update_ytdlp()
            print(f"yt-dlp: {msg}")
        elif target in ("gallery-dl", "gallerydl"):
            ok, msg = updater.update_gallerydl()
            print(f"gallery-dl: {msg}")
        elif target in ("curl_cffi", "curlcffi"):
            ok, msg = updater.update_curl_cffi()
            print(f"curl_cffi: {msg}")
        elif target in ("telegram-dl", "telegramdl", "telegram"):
            ok, msg = updater.update_telegramdl()
            print(f"Telegram-dl: {msg}")
        elif target in ("terabox-dl", "teraboxdl", "terabox"):
            ok, msg = updater.update_teraboxdl()
            print(f"Terabox-dl: {msg}")
        elif target in ("cyberdrop-dl", "cyberdropdl", "cyberdrop"):
            ok, msg = updater.update_cyberdropdl()
            print(f"cyberdrop-dl: {msg}")
        elif target in ("multi-dl", "self"):
            ok, msg = updater.update_self()
            print(f"MULTI_DOWNLOADER: {msg}")
        else:
            print(f"{Style.tag('❌', 'UNKNOWN', Style.RED)} Unknown engine '{target}'. Use: yt-dlp, gallery-dl, terabox-dl, telegram-dl, curl_cffi, or all.")
        return

    # Subcommands list
    subcommands = {"archive", "route", "queue", "config", "update"}

    # Direct URL mode: e.g. python multi-dl.py https://... [--backend yt-dlp] [--cookies-from-browser chrome]
    if raw_args and raw_args[0] not in subcommands and not raw_args[0].startswith("-h") and not raw_args[0] == "--help":
        direct_parser = argparse.ArgumentParser(description="MULTI_DOWNLOADER CLI")
        direct_parser.add_argument("url", help="Media URL to download directly")
        direct_parser.add_argument("--backend", "-b", help="Force specific backend (yt-dlp, gallery-dl, telegram-dl)")
        direct_parser.add_argument("--cookies-from-browser", help="Load cookies from browser (e.g. chrome, firefox, edge, brave, opera)")
        direct_parser.add_argument("--cookies", help="Path to cookies.txt file")
        direct_parser.add_argument("--format", "-f", help="Format selection string (e.g. bestvideo*+bestaudio/best)")
        parsed_direct = direct_parser.parse_args()

        opts = {}
        if parsed_direct.cookies_from_browser:
            opts["cookies_from_browser"] = parsed_direct.cookies_from_browser
        if parsed_direct.cookies:
            opts["cookies"] = parsed_direct.cookies
        if parsed_direct.format:
            opts["format"] = parsed_direct.format

        asyncio.run(download_url(parsed_direct.url, parsed_direct.backend, options=opts))
        return

    # Subcommand mode: e.g. python multi-dl.py archive --stats
    parser = argparse.ArgumentParser(
        description="MULTI_DOWNLOADER CLI - Universal Media Downloader",
        epilog="Tip: You can download directly with: python multi-dl.py <URL>"
    )
    parser.add_argument("-U", "--update", action="store_true", help="Update all download engines to their latest versions")
    subparsers = parser.add_subparsers(dest="command")

    # Optional 'download' subcommand (for backward compatibility)
    dl_parser = subparsers.add_parser("download", help="Download a URL (optional, you can just pass the URL directly)")
    dl_parser.add_argument("url", help="Media URL to download")
    dl_parser.add_argument("--backend", "-b", help="Force specific backend (yt-dlp, gallery-dl, telegram-dl)")
    dl_parser.add_argument("--cookies-from-browser", help="Load cookies from browser (e.g. chrome, firefox, edge, brave)")
    dl_parser.add_argument("--cookies", help="Path to cookies.txt file")
    dl_parser.add_argument("--format", "-f", help="Format selection string")

    # 'update' command
    up_parser = subparsers.add_parser("update", help="Update download engines to their latest versions")
    up_parser.add_argument("engine", nargs="?", default="all", help="Specific engine to update (all, yt-dlp, gallery-dl, terabox-dl, telegram-dl)")

    # 'archive' command
    arc_parser = subparsers.add_parser("archive", help="Inspect download archive")
    arc_parser.add_argument("--stats", action="store_true", help="Show summary metrics")
    arc_parser.add_argument("--search", "-s", help="Search history by keyword")

    # 'route' command (test routing)
    route_parser = subparsers.add_parser("route", help="Test URL routing")
    route_parser.add_argument("url", help="URL to test")

    args = parser.parse_args()

    if getattr(args, "update", False) or args.command == "update":
        target = getattr(args, "engine", "all")
        updater = EngineUpdater()
        if target == "all":
            updater.update_all()
        else:
            # Delegate to specific engine
            remaining = [target]
            # Call same logic
            updater.update_all()
    elif args.command == "download":
        sub_opts = {}
        if getattr(args, "cookies_from_browser", None):
            sub_opts["cookies_from_browser"] = args.cookies_from_browser
        if getattr(args, "cookies", None):
            sub_opts["cookies"] = args.cookies
        if getattr(args, "format", None):
            sub_opts["format"] = args.format
        asyncio.run(download_url(args.url, args.backend, options=sub_opts))
    elif args.command == "route":
        config = ConfigManager()
        router = URLRouter({
            "yt-dlp": YtDlpBackend(),
            "gallery-dl": GalleryDlBackend(),
            "telegram-dl": TelegramDlBackend(),
        })
        try:
            b = router.route(args.url)
            print(f"{Style.tag('🎯', 'ROUTING', Style.CYAN)} URL: {Style.white(args.url)}")
            print(f"{Style.tag('⚙️', 'BACKEND', Style.MAGENTA)} Detected Backend: {Style.engine_badge(b.name)}")
        except MultiDLError as e:
            print(f"{Style.tag('❌', 'ROUTING ERROR', Style.RED)} Could not route: {e}")
    elif args.command == "archive":
        config = ConfigManager()
        archive = ArchiveManager(config.archive_db_path)
        if args.stats:
            stats = archive.get_stats()
            print(f"\n{Style.tag('📊', 'STATS', Style.CYAN)} {Style.bold('Download Archive Summary')}")
            print(f"  • Total Downloads: {Style.green(str(stats['total_count']))}")
            print(f"  • Total Size:      {Style.green(format_bytes(stats['total_bytes']))}")
            print(f"  • By Backend:")
            for b, cnt in stats["by_backend"].items():
                print(f"    - {Style.engine_badge(b)}: {Style.white(str(cnt))}")
        elif args.search:
            results = archive.search(args.search)
            print(f"\nFound {Style.cyan(str(len(results)))} matches:")
            for r in results:
                print(f"  {Style.tag('📁', r.backend, Style.engine_color(r.backend))} {Style.white(r.file_name)} {Style.dim(f'({r.url})')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _sigint_handler()

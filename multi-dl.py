"""
CLI entry point for MULTI_DOWNLOADER.
"""

import argparse
import asyncio
import io
import os
import re
import shutil
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Force standard output to UTF-8 on Windows terminals to support emojis and international titles
if sys.platform == "win32" and not os.environ.get("PYTEST_CURRENT_TEST"):
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

__version__ = "4.3.0"

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
from backends.terabox_backend import TeraboxDlBackend
from backends.ytdlp_backend import YtDlpBackend
from backends.cyberdrop_backend import CyberdropDlBackend
from core.archive import ArchiveManager
from core.config import ConfigManager
from core.dispatcher import QueueDispatcher
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

    batch_part = ""
    if p.total_files and p.total_files > 1 and p.file_index:
        batch_part = f" {Style.dim('(')}{Style.cyan(f'{p.file_index}/{p.total_files}')}{Style.dim(')')}"

    sys.stdout.write(f"\r\033[K{tag_part} {bar_part} {sizes_part}{batch_part} {speed_part}{eta_part}")
    sys.stdout.flush()


def resolve_input_urls(inputs: List[str]) -> Tuple[List[str], List[str]]:
    """
    Parses a list of input arguments which can be individual URLs,
    whitespace-separated pasted blocks, or paths to local text files.
    Returns: (extracted_urls, source_files_found)
    """
    urls: List[str] = []
    source_files: List[str] = []

    for item in inputs:
        token = (item or "").strip().strip('"\'')
        if not token:
            continue

        p = Path(token)
        # Check if token is an existing file on disk
        if p.exists() and p.is_file():
            source_files.append(str(p))
            try:
                content = ""
                for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
                    try:
                        content = p.read_text(encoding=enc)
                        break
                    except UnicodeDecodeError:
                        continue
                for line in content.splitlines():
                    cleaned = line.strip().strip('"\'')
                    if cleaned and not cleaned.startswith("#"):
                        # If a line contains multiple space-separated links
                        sub_tokens = cleaned.split()
                        for st in sub_tokens:
                            cleaned_st = st.strip().strip('"\'')
                            if cleaned_st:
                                urls.append(cleaned_st)
            except Exception as e:
                print(f"{Style.tag('⚠️', 'FILE ERROR', Style.YELLOW)} Could not read file {Style.path(str(p))}: {e}")
        elif token.lower().endswith((".txt", ".list", ".urls", ".log")) and not p.exists():
            print(f"{Style.tag('⚠️', 'FILE NOT FOUND', Style.YELLOW)} Text file {Style.path(token)} does not exist.")
        else:
            # Check for multiple URLs in a single string (e.g. multi-pasted string)
            sub_tokens = token.split()
            if len(sub_tokens) > 1:
                for st in sub_tokens:
                    cleaned_st = st.strip().strip('"\'')
                    if cleaned_st:
                        urls.append(cleaned_st)
            else:
                urls.append(token)

    return urls, source_files


def print_queue_list(queue_mgr: QueueManager, status_filter: Optional[str] = None, is_batch: bool = False):
    """Renders a formatted table of all items in the download queue."""
    items = queue_mgr.list_all()
    if status_filter:
        items = [it for it in items if it.status.value == status_filter.lower()]

    q_name = "Active Batch Queue" if is_batch else "Download Queue"
    if not items:
        print(f"\n{Style.tag('📋', 'QUEUE', Style.CYAN)} {q_name} is currently empty.\n")
        return

    term_width = shutil.get_terminal_size((80, 24)).columns
    max_w = min(100, max(60, term_width - 2))

    print(f"\n{Style.tag('📋', 'QUEUE', Style.CYAN)} {Style.bold(f'Current {q_name} ({len(items)} items)')}\n")
    print(f"  {Style.dim('ID')}        {Style.dim('Prio')}  {Style.dim('Engine')}         {Style.dim('Status')}       {Style.dim('URL / Error')}")
    print(f"  {'─' * (max_w - 4)}")

    for item in items:
        status_color = {
            TaskStatus.QUEUED: Style.CYAN,
            TaskStatus.DOWNLOADING: Style.YELLOW,
            TaskStatus.COMPLETED: Style.GREEN,
            TaskStatus.SKIPPED: Style.YELLOW,
            TaskStatus.FAILED: Style.RED,
        }.get(item.status, Style.WHITE)

        eng_badge = Style.engine_badge(item.backend or "auto")
        status_tag = f"{status_color}{item.status.value.upper():<10}{Style.RESET}"

        info = item.error_message if (item.status == TaskStatus.FAILED and item.error_message) else item.url
        avail = max_w - 42
        if len(info) > avail:
            info = info[:avail - 3] + "..."

        print(f"  {Style.dim(item.id):<9} {item.priority:>4}   {eng_badge:<20} {status_tag} {Style.white(info)}")
    print(f"  {'─' * (max_w - 4)}\n")


async def download_url(url: str, backend_name: str = None, options: dict = None):
    config = ConfigManager()
    archive = ArchiveManager(config.archive_db_path)
    options = options or {}

    batch_queue = QueueManager(Path("data/batch_queue.json"))
    batch_queue.clear()
    batch_queue.add_batch([url], backend=backend_name, options=options)

    dispatcher = QueueDispatcher(config=config, queue_manager=batch_queue, archive=archive)
    await dispatcher.run(concurrency=1)


def print_banner(config: ConfigManager, archive: ArchiveManager) -> None:
    term_width = shutil.get_terminal_size((80, 24)).columns
    box_width = min(66, max(52, term_width - 4))

    def _box_line(text: str) -> str:
        clean = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', text)
        pad_total = max(0, box_width - 2 - len(clean))
        left = pad_total // 2
        right = pad_total - left
        return f"{Style.CYAN}║{' ' * left}{text}{' ' * right}║{Style.RESET}"

    bar = "═" * (box_width - 2)
    top_border = f"{Style.CYAN}╔{bar}╗{Style.RESET}"
    bot_border = f"{Style.CYAN}╚{bar}╝{Style.RESET}"

    print(f"\n{top_border}")
    print(_box_line(f"{Style.BOLD}{Style.WHITE}MULTI_DOWNLOADER v{__version__}{Style.RESET}"))
    print(_box_line(f"{Style.WHITE}Universal Modular Media & File Downloader{Style.RESET}"))
    print(_box_line(f"{Style.DIM}yt-dlp • gallery-dl • Terabox-dl • Telegram-dl • cyberdrop-dl{Style.RESET}"))
    print(f"{bot_border}\n")

    # Configuration section
    header_cfg = "─── Configuration "
    sep_len = max(6, box_width - len(header_cfg))
    print(f"{Style.CYAN}{header_cfg}{'─' * sep_len}{Style.RESET}")

    raw_dl = config.download_dir
    try:
        clean_dl = f"~{raw_dl.relative_to(config.download_dir.parent)}"
    except ValueError:
        clean_dl = str(raw_dl)

    print(f"  {Style.dim('Downloads:')} {Style.white(clean_dl)}")

    archive_enabled = config.get("archive", "enabled", True)
    if archive_enabled:
        stats = archive.get_stats()
        tot_cnt = stats.get("total_count", 0)
        print(f"  {Style.dim('Archive:')}   {Style.white(str(config.archive_db_path))} {Style.dim(f'({tot_cnt} recorded)')}")
    else:
        print(f"  {Style.dim('Archive:')}   {Style.yellow('Disabled')}")

    badges = " ".join([
        Style.engine_badge("yt-dlp"),
        Style.engine_badge("gallery-dl"),
        Style.engine_badge("terabox-dl"),
        Style.engine_badge("telegram-dl"),
        Style.engine_badge("cyberdrop-dl"),
    ])
    print(f"  {Style.dim('Engines:')}   {badges}\n")

    # Interactive mode prompt section
    header_mode = "─── Interactive Mode "
    mode_sep_len = max(6, box_width - len(header_mode))
    print(f"{Style.CYAN}{header_mode}{'─' * mode_sep_len}{Style.RESET}")
    print(f"  {Style.dim('Enter a URL or path to a text file with links to begin.')}")
    print(f"  {Style.dim('Right-click to paste. Press ')}{Style.bold('Ctrl+C')}{Style.dim(' or type ')}{Style.bold('exit')}{Style.dim(' to quit.')}\n")


async def interactive_mode():
    config = ConfigManager()
    archive = ArchiveManager(config.archive_db_path)
    print_banner(config, archive)

    while True:
        try:
            url_input = input(f"{Style.bold(Style.cyan('Enter URL: '))}{Style.RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{Style.dim('Goodbye!')}")
            break

        if not url_input or url_input.lower() in ("exit", "quit", "q"):
            print(f"{Style.dim('Goodbye!')}")
            break

        extracted_urls, source_files = resolve_input_urls([url_input])
        if not extracted_urls:
            continue

        if len(extracted_urls) > 1 or source_files:
            if source_files:
                file_names = ", ".join(Path(f).name for f in source_files)
                print(f"\n{Style.tag('📄', 'BATCH FILE', Style.CYAN)} Read {Style.bold(str(len(extracted_urls)))} links from {Style.white(file_names)}\n")
            else:
                print(f"\n{Style.tag('📦', 'BATCH', Style.MAGENTA)} Detected {Style.bold(str(len(extracted_urls)))} links pasted\n")

            batch_queue = QueueManager(Path("data/batch_queue.json"))
            batch_queue.clear()
            batch_queue.add_batch(extracted_urls)

            dispatcher = QueueDispatcher(config=config, queue_manager=batch_queue, archive=archive)
            await dispatcher.run()
        else:
            await download_url(extracted_urls[0])

        term_width = shutil.get_terminal_size((80, 24)).columns
        sep_width = min(66, max(40, term_width - 4))
        print(f"\n{Style.CYAN}{'─' * sep_width}{Style.RESET}\n")


def main():
    raw_args = sys.argv[1:]

    # Interactive mode: running without arguments or with -i / --interactive / interactive
    if not raw_args or raw_args[0] in ("-i", "--interactive", "interactive"):
        asyncio.run(interactive_mode())
        return

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
            print(f"{Style.tag('❌', 'UNKNOWN', Style.RED)} Unknown engine '{target}'. Use: yt-dlp, gallery-dl, terabox-dl, telegram-dl, cyberdrop-dl, curl_cffi, or all.")
        return

    # Version flag check
    if any(arg in sys.argv[1:] for arg in ("-v", "--version")):
        print(f"MULTI_DOWNLOADER v{__version__}")
        return

    # Subcommands list
    subcommands = {"archive", "route", "queue", "config", "update", "auth", "login", "download", "interactive"}

    # Direct URL or batch execution mode: e.g. python multi-dl.py urls.txt / python multi-dl.py https://... [-c 3]
    if raw_args and raw_args[0] not in subcommands and not raw_args[0].startswith("-h") and not raw_args[0] == "--help":
        direct_parser = argparse.ArgumentParser(
            description="MULTI_DOWNLOADER CLI - Direct & Batch Downloader",
            epilog="Tip: You can download directly with: python multi-dl.py <URL or textfile>"
        )
        direct_parser.add_argument("urls", nargs="*", help="Media URLs or text file paths to download")
        direct_parser.add_argument("-f", "--file", "--input", dest="file", help="Path to text file containing URLs")
        direct_parser.add_argument("-c", "--concurrency", type=int, help="Number of concurrent worker streams")
        direct_parser.add_argument("-b", "--backend", help="Force specific backend (yt-dlp, gallery-dl, terabox-dl, telegram-dl, cyberdrop-dl)")
        direct_parser.add_argument("--format", help="Format selection string (e.g. bestvideo*+bestaudio/best)")
        direct_parser.add_argument("--cookies-from-browser", help="Load cookies from browser (e.g. chrome, firefox, edge, brave, opera)")
        direct_parser.add_argument("--cookies", help="Path to cookies.txt file")
        direct_parser.add_argument("--ndus", help="TeraBox ndus session cookie")
        parsed_direct = direct_parser.parse_args(raw_args)

        raw_inputs = list(parsed_direct.urls or [])
        format_val = parsed_direct.format
        if parsed_direct.file:
            f_str = parsed_direct.file
            p_check = Path(f_str.strip('"\''))
            if (p_check.exists() and p_check.is_file()) or f_str.lower().endswith((".txt", ".list", ".urls")):
                raw_inputs.append(f_str)
            elif not format_val:
                format_val = f_str
            else:
                raw_inputs.append(f_str)

        extracted_urls, source_files = resolve_input_urls(raw_inputs)
        if not extracted_urls:
            if not raw_inputs:
                direct_parser.print_help()
            else:
                print(f"{Style.tag('⚠️', 'DOWNLOAD', Style.YELLOW)} No valid URLs found to download.")
            return

        opts = {}
        if parsed_direct.cookies_from_browser:
            opts["cookies_from_browser"] = parsed_direct.cookies_from_browser
        if parsed_direct.cookies:
            opts["cookies"] = parsed_direct.cookies
        if format_val:
            opts["format"] = format_val
        if parsed_direct.ndus:
            opts["ndus"] = parsed_direct.ndus

        # Single direct URL mode
        if len(extracted_urls) == 1 and not source_files and not parsed_direct.concurrency:
            asyncio.run(download_url(extracted_urls[0], parsed_direct.backend, options=opts))
            return

        # Immediate concurrent batch mode
        if source_files:
            file_names = ", ".join(Path(f).name for f in source_files)
            print(f"\n{Style.tag('📄', 'BATCH FILE', Style.CYAN)} Read {Style.bold(str(len(extracted_urls)))} links from {Style.white(file_names)}")
        else:
            print(f"\n{Style.tag('📦', 'BATCH', Style.MAGENTA)} Ingested {Style.bold(str(len(extracted_urls)))} links for immediate concurrent download...")

        config = ConfigManager()
        archive = ArchiveManager(config.archive_db_path)
        batch_queue = QueueManager(Path("data/batch_queue.json"))
        batch_queue.clear()
        batch_queue.add_batch(extracted_urls, backend=parsed_direct.backend, options=opts)

        dispatcher = QueueDispatcher(config=config, queue_manager=batch_queue, archive=archive)
        asyncio.run(dispatcher.run(concurrency=parsed_direct.concurrency))
        return

    # Subcommand mode: e.g. python multi-dl.py archive --stats
    parser = argparse.ArgumentParser(
        description="MULTI_DOWNLOADER CLI - Universal Media Downloader",
        epilog="Tip: You can download directly with: python multi-dl.py <URL or textfile>"
    )
    parser.add_argument("-v", "--version", action="version", version=f"MULTI_DOWNLOADER v{__version__}")
    parser.add_argument("-U", "--update", action="store_true", help="Update all download engines to their latest versions")
    parser.add_argument("-i", "--interactive", action="store_true", help="Launch interactive link pasting session")
    parser.add_argument("-f", "--file", "--input", dest="file", help="Process a text file of URLs with concurrent workers")
    parser.add_argument("-c", "--concurrency", type=int, help="Number of concurrent worker streams")
    subparsers = parser.add_subparsers(dest="command")

    # 'interactive' command
    subparsers.add_parser("interactive", help="Start interactive link pasting session")

    # 'auth' / 'login' command
    auth_parser = subparsers.add_parser("auth", help="Configure engine authentication (telegram, terabox, all)")
    auth_parser.add_argument("engine", nargs="?", default="all", help="Engine to authenticate (telegram, terabox, all)")
    subparsers.add_parser("login", help="Alias for 'auth'")

    # Optional 'download' subcommand (for backward compatibility)
    dl_parser = subparsers.add_parser("download", help="Download URLs or text files directly")
    dl_parser.add_argument("urls", nargs="*", help="Media URLs or text file paths to download")
    dl_parser.add_argument("-f", "--file", "--input", dest="file", help="Path to text file containing URLs")
    dl_parser.add_argument("-c", "--concurrency", type=int, help="Number of concurrent worker streams")
    dl_parser.add_argument("-b", "--backend", help="Force specific backend (yt-dlp, gallery-dl, terabox-dl, telegram-dl, cyberdrop-dl)")
    dl_parser.add_argument("--format", help="Format selection string")
    dl_parser.add_argument("--cookies-from-browser", help="Load cookies from browser (e.g. chrome, firefox, edge, brave)")
    dl_parser.add_argument("--cookies", help="Path to cookies.txt file")
    dl_parser.add_argument("--ndus", help="TeraBox ndus session cookie")

    # 'update' command
    up_parser = subparsers.add_parser("update", help="Update download engines to their latest versions")
    up_parser.add_argument("engine", nargs="?", default="all", help="Specific engine to update (all, yt-dlp, gallery-dl, terabox-dl, telegram-dl, cyberdrop-dl)")

    # 'archive' command
    arc_parser = subparsers.add_parser("archive", help="Inspect download archive")
    arc_parser.add_argument("--stats", action="store_true", help="Show summary metrics")
    arc_parser.add_argument("--search", "-s", help="Search history by keyword")

    # 'route' command (test routing)
    route_parser = subparsers.add_parser("route", help="Test URL routing")
    route_parser.add_argument("url", help="URL to test")

    # 'queue' command
    q_parser = subparsers.add_parser("queue", help="Manage download queue and run concurrent worker pool")
    q_subparsers = q_parser.add_subparsers(dest="queue_action")

    # queue add
    q_add = q_subparsers.add_parser("add", help="Add URLs to queue (with automatic pre-routing)")
    q_add.add_argument("urls", nargs="*", help="Media URLs or text file paths to queue")
    q_add.add_argument("-f", "--file", "--input", dest="file", help="Path to text file containing URLs")
    q_add.add_argument("-p", "--priority", type=int, default=0, help="Priority (higher processed first)")
    q_add.add_argument("-b", "--backend", help="Force specific backend engine")
    q_add.add_argument("--batch", action="store_true", help="Add to batch_queue.json instead of persistent queue.json")

    # queue list
    q_list = q_subparsers.add_parser("list", help="Display all queued downloads")
    q_list.add_argument("--status", help="Filter by status (queued, downloading, completed, failed, skipped)")
    q_list.add_argument("--batch", action="store_true", help="View active batch queue instead of persistent queue")

    # queue start
    q_start = q_subparsers.add_parser("start", help="Process download queue with concurrent workers")
    q_start.add_argument("-c", "--concurrency", type=int, help="Number of concurrent worker streams")
    q_start.add_argument("--batch", action="store_true", help="Process active batch queue instead of persistent queue")

    # queue retry
    q_retry = q_subparsers.add_parser("retry", help="Reset all failed downloads back to queued status")
    q_retry.add_argument("--batch", action="store_true", help="Retry failed items in batch queue")

    # queue clear
    q_clear = q_subparsers.add_parser("clear", help="Clear downloads from queue")
    q_clear.add_argument("--status", help="Clear only specific status (completed, failed, queued, or all)")
    q_clear.add_argument("--batch", action="store_true", help="Clear batch queue instead of persistent queue")

    args = parser.parse_args()

    if getattr(args, "interactive", False) or args.command == "interactive":
        asyncio.run(interactive_mode())
    elif getattr(args, "update", False) or args.command == "update":
        target = getattr(args, "engine", "all")
        updater = EngineUpdater()
        if target == "all":
            updater.update_all()
        else:
            updater.update_all()
    elif args.command == "download":
        raw_inputs = list(args.urls or [])
        if getattr(args, "file", None):
            raw_inputs.append(args.file)
        extracted_urls, source_files = resolve_input_urls(raw_inputs)
        if not extracted_urls:
            print(f"{Style.tag('⚠️', 'DOWNLOAD', Style.YELLOW)} No valid URLs found to download.")
            return

        opts = {}
        if getattr(args, "cookies_from_browser", None):
            opts["cookies_from_browser"] = args.cookies_from_browser
        if getattr(args, "cookies", None):
            opts["cookies"] = args.cookies
        if getattr(args, "format", None):
            opts["format"] = args.format
        if getattr(args, "ndus", None):
            opts["ndus"] = args.ndus

        if len(extracted_urls) == 1 and not source_files and not getattr(args, "concurrency", None):
            asyncio.run(download_url(extracted_urls[0], getattr(args, "backend", None), options=opts))
            return

        if source_files:
            file_names = ", ".join(Path(f).name for f in source_files)
            print(f"\n{Style.tag('📄', 'BATCH FILE', Style.CYAN)} Read {Style.bold(str(len(extracted_urls)))} links from {Style.white(file_names)}")
        else:
            print(f"\n{Style.tag('📦', 'BATCH', Style.MAGENTA)} Ingested {Style.bold(str(len(extracted_urls)))} links for immediate concurrent download...")

        config = ConfigManager()
        archive = ArchiveManager(config.archive_db_path)
        batch_queue = QueueManager(Path("data/batch_queue.json"))
        batch_queue.clear()
        batch_queue.add_batch(extracted_urls, backend=getattr(args, "backend", None), options=opts)

        dispatcher = QueueDispatcher(config=config, queue_manager=batch_queue, archive=archive)
        asyncio.run(dispatcher.run(concurrency=getattr(args, "concurrency", None)))
        return
    elif args.command == "route":
        config = ConfigManager()
        router = URLRouter.create_default(config=config)
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
    elif args.command in ("auth", "login"):
        target = (getattr(args, "engine", "all") or "all").lower()
        config = ConfigManager()

        if target in ("telegram", "telegram-dl", "all"):
            print(f"\n{Style.tag('🔑', 'AUTH', Style.BLUE)} Initializing Telegram authorization...")
            tg_backend = TelegramDlBackend(config.get_backend_config("telegram-dl"))
            try:
                asyncio.run(tg_backend._ensure_client())
                print(f"{Style.tag('✅', 'READY', Style.GREEN)} Telegram account is authorized and ready for downloads.")
            except Exception as e:
                print(f"{Style.tag('❌', 'AUTH ERROR', Style.RED)} {Style.error(str(e))}")

        if target in ("terabox", "terabox-dl", "all"):
            print(f"\n{Style.tag('🔑', 'AUTH', Style.CYAN)} Configuring TeraBox credentials...")
            tb_backend = TeraboxDlBackend(config.get_backend_config("terabox-dl"))
            try:
                tb_backend._prompt_ndus_cookie()
                print(f"{Style.tag('✅', 'READY', Style.GREEN)} TeraBox credentials configured.")
            except Exception as e:
                print(f"{Style.tag('❌', 'AUTH ERROR', Style.RED)} {Style.error(str(e))}")
    elif args.command == "queue":
        q_action = getattr(args, "queue_action", None)
        queue_file = Path("data/batch_queue.json") if getattr(args, "batch", False) else Path("data/queue.json")
        target_queue = QueueManager(queue_file)

        if q_action == "add":
            raw_inputs = list(args.urls or [])
            if getattr(args, "file", None):
                raw_inputs.append(args.file)

            extracted_urls, source_files = resolve_input_urls(raw_inputs)
            if not extracted_urls:
                print(f"{Style.tag('⚠️', 'QUEUE', Style.YELLOW)} No URLs provided or found to add.")
                return

            if source_files:
                file_names = ", ".join(Path(f).name for f in source_files)
                print(f"\n{Style.tag('📄', 'BATCH FILE', Style.CYAN)} Read {Style.bold(str(len(extracted_urls)))} links from {Style.white(file_names)}")

            added = target_queue.add_batch(extracted_urls, priority=args.priority, backend=args.backend)
            q_label = "batch queue" if getattr(args, "batch", False) else "download queue"
            print(f"\n{Style.tag('✅', 'QUEUE', Style.GREEN)} Added {Style.bold(str(len(added)))} item(s) to {q_label}:")
            for it in added[:8]:
                print(f"  • {Style.engine_badge(it.backend or 'auto')} {Style.white(it.url)}")
            if len(added) > 8:
                print(f"  ... and {len(added) - 8} more.")
            print(f"\n{Style.dim('Tip: Run')} {Style.bold('python multi-dl.py queue start')} {Style.dim('to begin downloading.')}\n")

        elif q_action == "list" or q_action is None:
            print_queue_list(target_queue, status_filter=getattr(args, "status", None), is_batch=getattr(args, "batch", False))

        elif q_action == "start":
            config = ConfigManager()
            archive = ArchiveManager(config.archive_db_path)
            stats = target_queue.stats()
            queued_cnt = stats["by_status"].get("queued", 0) + stats["by_status"].get("pending", 0)
            if queued_cnt == 0:
                q_label = "batch queue" if getattr(args, "batch", False) else "download queue"
                print(f"\n{Style.tag('📋', 'QUEUE', Style.CYAN)} No pending items in {q_label} to download.")
                print(f"  {Style.dim('Add items first with:')} {Style.bold('python multi-dl.py queue add <url/file>')}\n")
                return

            dispatcher = QueueDispatcher(config=config, queue_manager=target_queue, archive=archive)
            asyncio.run(dispatcher.run(concurrency=getattr(args, "concurrency", None)))

        elif q_action == "retry":
            count = target_queue.retry_failed()
            q_label = "batch queue" if getattr(args, "batch", False) else "download queue"
            if count > 0:
                print(f"\n{Style.tag('🔄', 'QUEUE RETRY', Style.CYAN)} Re-queued {Style.bold(str(count))} failed item(s) in {q_label} back to QUEUED.\n")
            else:
                print(f"\n{Style.tag('ℹ️', 'QUEUE', Style.CYAN)} No failed items found in {q_label} to retry.\n")

        elif q_action == "clear":
            status_f = getattr(args, "status", None)
            sf = TaskStatus(status_f.lower()) if status_f and status_f.lower() in [s.value for s in TaskStatus] else None
            count = target_queue.clear(status_filter=sf)
            filter_text = f" ({status_f})" if status_f else ""
            q_label = "batch queue" if getattr(args, "batch", False) else "download queue"
            print(f"\n{Style.tag('🗑️', 'QUEUE CLEAR', Style.YELLOW)} Removed {Style.bold(str(count))} item(s){filter_text} from {q_label}.\n")

    elif getattr(args, "file", None):
        extracted_urls, source_files = resolve_input_urls([args.file])
        if not extracted_urls:
            print(f"{Style.tag('⚠️', 'DOWNLOAD', Style.YELLOW)} No URLs found in {args.file}")
            return
        file_names = ", ".join(Path(f).name for f in source_files)
        print(f"\n{Style.tag('📄', 'BATCH FILE', Style.CYAN)} Read {Style.bold(str(len(extracted_urls)))} links from {Style.white(file_names)}")

        config = ConfigManager()
        archive = ArchiveManager(config.archive_db_path)
        batch_queue = QueueManager(Path("data/batch_queue.json"))
        batch_queue.clear()
        batch_queue.add_batch(extracted_urls)

        dispatcher = QueueDispatcher(config=config, queue_manager=batch_queue, archive=archive)
        asyncio.run(dispatcher.run(concurrency=getattr(args, "concurrency", None)))
        return
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _sigint_handler()

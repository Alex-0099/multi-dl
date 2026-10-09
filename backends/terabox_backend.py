"""
Adapter backend wrapping Terabox-dl for MULTI_DOWNLOADER.
Supports single files, multi-file shares, and directory folders with resume, 
chunked streaming, and in-place dual progress bars.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import uuid

import requests
from dotenv import load_dotenv

from backends.base import BaseBackend
from core.exceptions import AuthenticationError, DownloadFailedError, EngineNotFoundError
from core.models import ArchiveEntry, DownloadProgress, DownloadTask, MediaType
from core.terminal import Style, format_bytes

APP_ID = "250528"
BASE_URL = "https://www.terabox.com"

TERABOX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.terabox.com/",
    "X-Requested-With": "XMLHttpRequest",
}

ERROR_MESSAGES = {
    -1: "Server error or rate limited. Try again later.",
    -3: "Invalid or missing parameters in request.",
    -6: "Share link has expired or been removed.",
    -7: "File or share requires a password.",
    -9: "File does not exist or has been deleted.",
    -12: "Insufficient storage space on your account.",
    -20: "Session expired. Please refresh your ndus cookie.",
    -21: "Share link has been banned/restricted.",
    -32: "Exceeded download frequency limit. Wait and retry.",
    -33: "File too large for free-tier download.",
    2: "Download link expired. Re-fetching...",
    4: "Request too frequent. Please wait.",
    12: "Access denied — cookie may be invalid or expired.",
    31: "Sign/token verification failed. Cookie may need refresh.",
    105: "Invalid share link format.",
    112: "Session expired or cookie invalid. Please re-authenticate.",
    118: "Download quota exceeded for this file.",
    400210: "jsToken missing or invalid. Auto-refreshing...",
    4000023: "jsToken expired. Auto-refreshing...",
}


def _get_error_message(code: int) -> str:
    return ERROR_MESSAGES.get(code, f"TeraBox API error (code {code})")


def _get_safe_filename(name: Optional[str]) -> str:
    if not name:
        return "unnamed_file"
    # Remove invalid Windows filesystem characters: < > : " / \ | ? *
    clean = re.sub(r'[<>:"/\\|?*]', '_', name).strip()
    return clean or "unnamed_file"


def _detect_media_type(filename: str) -> MediaType:
    ext = Path(filename).suffix.lower()
    if ext in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v"):
        return MediaType.VIDEO
    if ext in (".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"):
        return MediaType.AUDIO
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        return MediaType.IMAGE
    if ext in (".pdf", ".zip", ".rar", ".7z", ".tar", ".gz", ".txt", ".epub"):
        return MediaType.DOCUMENT
    return MediaType.UNKNOWN


class _TeraboxBatchTracker:
    """
    Renders an in-place dual progress bar for multi-file TeraBox shares:
    Line 1 (Top): Batch progress (e.g. 2/5 files • 85.30 MB ( 40.0%))
    Line 2 (Bottom): Active file progress (speed, ETA, sizes, filename)
    Strictly budgets visible columns to prevent terminal line-wrapping cascades.
    """

    def __init__(self, total_files: int, total_batch_bytes: int = 0):
        self.total_files: int = total_files
        self.completed_count: int = 0
        self.skipped_count: int = 0
        self.total_batch_bytes: int = total_batch_bytes
        self._file_bytes: Dict[str, int] = {}

        self.current_filename: str = ""
        self.current_downloaded: int = 0
        self.current_total: Optional[int] = None
        self.current_speed: Optional[float] = None
        self.current_eta: Optional[int] = None

        self._lines_printed: int = 0
        self._is_tty: bool = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else True

    @staticmethod
    def _visible_len(s: str) -> int:
        clean = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', s)
        extra = sum(1 for ch in clean if ord(ch) > 0x1F000 or ch in "🖼📥📦🎬📁🚀⚠️❌✅•")
        return len(clean) + extra

    def update_file(
        self,
        filename: str,
        downloaded: int,
        total: Optional[int],
        speed: Optional[float],
        eta: Optional[int],
    ) -> None:
        self.current_filename = filename
        self.current_downloaded = downloaded
        self.current_total = total
        self.current_speed = speed
        self.current_eta = eta
        self._file_bytes[filename] = downloaded
        self._render()

    def finish_file(self, filename: str, final_size: int, skipped: bool = False) -> None:
        self.completed_count += 1
        if skipped:
            self.skipped_count += 1
        self._file_bytes[filename] = final_size
        self._render()

    def finish(self) -> None:
        self._render(done=True)

    def _render(self, done: bool = False) -> None:
        term_width = shutil.get_terminal_size((80, 24)).columns
        max_width = max(40, term_width - 2)
        bar_w = 8 if max_width < 75 else 16

        # --- Line 1: Batch Progress ---
        if self.total_files > 0:
            batch_pct = (self.completed_count / self.total_files) * 100.0
            batch_pct_text = f"{batch_pct:5.1f}%"
            batch_bar = Style.progress_bar(batch_pct, width=bar_w)
            batch_count = f"{self.completed_count}/{self.total_files} files"
        else:
            batch_pct = None
            batch_pct_text = "  --% "
            batch_bar = Style.progress_bar(None, width=bar_w)
            batch_count = f"{self.completed_count} files"

        current_total_bytes = sum(self._file_bytes.values())
        if current_total_bytes > 0:
            size_color = Style.GREEN if (batch_pct is not None and batch_pct >= 100.0) else Style.WHITE
            size_str = f" {Style.dim('•')} {size_color}{format_bytes(current_total_bytes)}{Style.RESET}"
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

        # --- Line 2: Active File Progress ---
        if self.current_total and self.current_total > 0:
            file_pct = (self.current_downloaded / self.current_total) * 100.0
            file_pct_text = f"{file_pct:5.1f}%"
            file_bar = Style.progress_bar(file_pct, width=bar_w)
            file_sizes = f"{format_bytes(self.current_downloaded)} / {format_bytes(self.current_total)}"
        else:
            file_pct = None
            file_pct_text = "  --% "
            file_bar = Style.progress_bar(None, width=bar_w)
            file_sizes = f"{format_bytes(self.current_downloaded)} / ??"

        file_color = Style.GREEN if (file_pct is not None and file_pct >= 100.0) else Style.CYAN
        tag_file = Style.tag("📥", file_pct_text, file_color)
        speed_str = f"{format_bytes(int(self.current_speed))}/s" if self.current_speed else "--/s"
        speed_part = f" {Style.dim('@')} {Style.speed(speed_str)}"

        eta_part = ""
        if self.current_eta and self.current_eta > 0 and (file_pct is None or file_pct < 100.0):
            eta_m, eta_s = divmod(int(self.current_eta), 60)
            eta_part = f" {Style.dim('ETA')} {Style.yellow(f'{eta_m:02d}:{eta_s:02d}')}"

        prefix = f"{tag_file} {file_bar} {Style.white(file_sizes)}{speed_part}"
        prefix_vis = self._visible_len(prefix)

        if eta_part and (prefix_vis + self._visible_len(eta_part) + 12 <= max_width):
            prefix += eta_part
            prefix_vis = self._visible_len(prefix)

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


class TeraboxDlBackend(BaseBackend):
    """
    Adapter backend wrapping Terabox-dl for MULTI_DOWNLOADER.
    Features:
    - Auto-engine discovery (local user repo, project engines/, or native implementation)
    - Authentication via ndus cookie (modular config, environment variable, or .env)
    - jsToken dynamic resolution & session caching
    - Direct CDN link retrieval via /api/sharedownload
    - Multi-file share & folder hierarchy recursion
    - HTTP Range resume support
    - In-place dual batch progress reporting
    """

    name: str = "terabox-dl"
    supported_domains: List[str] = [
        "terabox.com",
        "www.terabox.com",
        "1024tera.com",
        "www.1024tera.com",
        "teraboxlink.com",
        "www.teraboxlink.com",
        "freeterabox.com",
        "www.freeterabox.com",
        "mirrobox.com",
        "www.mirrobox.com",
        "nephobox.com",
        "www.nephobox.com",
        "4funbox.com",
        "www.4funbox.com",
        "teraboxapp.com",
        "www.teraboxapp.com",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._cached_js_token: Optional[str] = None
        self._load_modular_config()

    def _load_modular_config(self) -> None:
        """Loads and merges options from configs/terabox-dl.json if present."""
        cfg_path_str = self.config.get("config_file", "configs/terabox-dl.json")
        cfg_path = Path(cfg_path_str)
        if not cfg_path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent
            cfg_path = project_root / cfg_path

        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if not k.startswith("//") and k not in self.config:
                            self.config[k] = v
            except Exception:
                pass

    def can_handle(self, url: str) -> bool:
        """Returns True if the URL targets any supported TeraBox domain or contains surl=."""
        url_lower = url.lower()
        if "surl=" in url_lower:
            return True
        try:
            domain = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
            return any(domain == d or domain.endswith("." + d) for d in self.supported_domains)
        except Exception:
            return False

    @staticmethod
    def extract_short_key(url: str) -> str:
        """
        Normalizes share keys from various TeraBox link representations:
        - https://www.terabox.com/s/1ABCxyz -> 1ABCxyz
        - https://1024tera.com/sharing/link?surl=ABCxyz -> 1ABCxyz
        - 1ABCxyz -> 1ABCxyz
        """
        raw = url.strip().rstrip('/')
        if '/s/' in raw:
            return raw.split('/s/')[-1].split('?')[0]
        if 'surl=' in raw:
            parsed = urllib.parse.urlparse(raw)
            params = urllib.parse.parse_qs(parsed.query)
            surl = params.get('surl', [None])[0]
            if surl:
                return '1' + surl if not surl.startswith('1') else surl
        if '/' not in raw and ':' not in raw:
            return raw
        raise DownloadFailedError(f"Could not extract TeraBox share key from URL: {url}")

    def _resolve_ndus_cookie(self, task_options: Optional[Dict[str, Any]] = None) -> str:
        """
        Resolves the ndus cookie from:
        1. Task CLI option override (--ndus ...)
        2. Config ndus_cookie
        3. Environment variable TERABOX_NDUS
        4. .env file in user repo (~/Terabox-dl/.env) or project root
        """
        if task_options and task_options.get("ndus"):
            return str(task_options["ndus"]).strip()

        if self.config.get("ndus_cookie"):
            return str(self.config["ndus_cookie"]).strip()

        env_val = os.getenv("TERABOX_NDUS")
        if env_val:
            return env_val.strip()

        custom_env = self.config.get("env_file")
        candidate_envs = [
            Path(custom_env) if custom_env else None,
            Path.home() / "Terabox-dl" / ".env",
            Path.home() / "terabox-dl" / ".env",
            Path(__file__).resolve().parent.parent / ".env",
        ]
        for p in candidate_envs:
            if p and p.exists():
                load_dotenv(p)
                val = os.getenv("TERABOX_NDUS")
                if val:
                    return val.strip()

        return ""

    def _save_modular_config(self) -> None:
        """Persists updated configuration back to configs/terabox-dl.json."""
        cfg_path_str = self.config.get("config_file", "configs/terabox-dl.json")
        cfg_path = Path(cfg_path_str)
        if not cfg_path.is_absolute():
            project_root = Path(__file__).resolve().parent.parent
            cfg_path = project_root / cfg_path

        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            current_data: Dict[str, Any] = {}
            if cfg_path.exists():
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        current_data = json.load(f)
                except Exception:
                    pass
            current_data["ndus_cookie"] = self.config.get("ndus_cookie", "")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(current_data, f, indent=2)
        except Exception:
            pass

    def _prompt_ndus_cookie(self, reason: str = "") -> str:
        """Interactively prompts the user for their TeraBox NDUS cookie."""
        print(f"\n{Style.tag('🍪', 'TERABOX AUTH', Style.CYAN)} {Style.bold('TeraBox account cookie (ndus) required.')}")
        if reason:
            print(f"{Style.dim(f'   Notice: {reason}')}")
        print(f"{Style.dim('   How to get your ndus cookie:')}")
        print(f"{Style.dim('   1. Log into https://www.terabox.com (or 1024tera.com) in your web browser.')}")
        print(f"{Style.dim('   2. Open Developer Tools (F12) -> Application / Storage -> Cookies -> terabox.com.')}")
        print(f"{Style.dim('   3. Locate the cookie named \"ndus\" and copy its Value.')}\n")

        val = input(f"{Style.cyan('Enter TeraBox ndus cookie: ')}").strip()
        if not val:
            raise AuthenticationError("TeraBox download cancelled: No ndus cookie provided.")

        self.config["ndus_cookie"] = val
        self._save_modular_config()
        print(f"\n{Style.tag('💾', 'SAVED', Style.GREEN)} Cookie saved to {Style.path('configs/terabox-dl.json')}\n")
        return val

    def _create_session(self, ndus_cookie: str) -> requests.Session:
        """Initializes a configured requests Session with cookies and headers."""
        session = requests.Session()
        session.headers.update(TERABOX_HEADERS)
        custom_ua = self.config.get("user_agent")
        if custom_ua:
            session.headers["User-Agent"] = custom_ua

        if ndus_cookie:
            for domain in [".terabox.com", ".1024tera.com", ".teraboxlink.com", ".freeterabox.com"]:
                session.cookies.set("ndus", ndus_cookie, domain=domain)
        return session

    def _fetch_js_token(self, session: requests.Session, target_url: str) -> Optional[str]:
        """Scrapes the dynamic jsToken required for TeraBox signature requests."""
        if self._cached_js_token:
            return self._cached_js_token

        urls_to_try = [target_url, BASE_URL]
        timeout = int(self.config.get("timeout", 30))

        for u in urls_to_try:
            try:
                resp = session.get(u, timeout=timeout)
                html = resp.text

                # Primary pattern (encoded backticks)
                start_marker = '`function%20fn%28a%29%7Bwindow.jsToken%20%3D%20a%7D%3Bfn%28%22'
                end_marker = '%22%29`'
                if start_marker in html:
                    s_idx = html.index(start_marker) + len(start_marker)
                    e_idx = html.index(end_marker, s_idx)
                    token = html[s_idx:e_idx]
                    if token:
                        self._cached_js_token = token
                        return token

                # Alternative markers
                start_alt = 'function%20fn%28a%29%7Bwindow.jsToken%20%3D%20a%7D%3Bfn%28%22'
                if start_alt in html:
                    s_idx = html.index(start_alt) + len(start_alt)
                    e_idx = html.index('%22%29', s_idx)
                    token = html[s_idx:e_idx]
                    if token:
                        self._cached_js_token = token
                        return token

                # Regex patterns
                m = re.search(r'window\.jsToken\s*=\s*["\']([a-zA-Z0-9_.-]+)["\']', html)
                if m:
                    self._cached_js_token = m.group(1)
                    return self._cached_js_token

                m = re.search(r'["\']jsToken["\']\s*:\s*["\']([a-zA-Z0-9_.-]+)["\']', html)
                if m:
                    self._cached_js_token = m.group(1)
                    return self._cached_js_token

                m = re.search(r'fn\(\s*["\']([a-zA-Z0-9_.-]{16,})["\']\s*\)', html)
                if m:
                    self._cached_js_token = m.group(1)
                    return self._cached_js_token
            except Exception:
                pass

        return None

    def _get_share_info(self, session: requests.Session, short_key: str, target_url: str) -> Dict[str, Any]:
        """Resolves share metadata via /api/shorturlinfo."""
        app_id = self.config.get("app_id", APP_ID)
        timeout = int(self.config.get("timeout", 30))
        params = {
            "shorturl": short_key,
            "root": "1",
            "app_id": app_id,
            "web": "1",
            "channel": "dubox",
            "clienttype": "0",
        }

        js_token = self._fetch_js_token(session, target_url)
        if js_token:
            params["jsToken"] = js_token

        url = f"{BASE_URL}/api/shorturlinfo"

        for attempt in range(1, 4):
            try:
                resp = session.get(url, params=params, timeout=timeout)
                data = resp.json()

                if data.get("errno") in (400210, 4000023):
                    # Refresh token and retry
                    self._cached_js_token = None
                    js_token = self._fetch_js_token(session, target_url)
                    if js_token:
                        params["jsToken"] = js_token
                    resp = session.get(url, params=params, timeout=timeout)
                    data = resp.json()

                errno = data.get("errno", 0)
                if errno != 0:
                    err_msg = _get_error_message(errno)
                    if errno in (-20, 12, 112):
                        if Style.is_interactive():
                            new_cookie = self._prompt_ndus_cookie(reason=err_msg)
                            for domain in [".terabox.com", ".1024tera.com", ".teraboxlink.com", ".freeterabox.com"]:
                                session.cookies.set("ndus", new_cookie, domain=domain)
                            self._cached_js_token = None
                            js_token = self._fetch_js_token(session, target_url)
                            if js_token:
                                params["jsToken"] = js_token
                            continue
                        raise AuthenticationError(f"{err_msg} (Provide a valid ndus cookie)")
                    raise DownloadFailedError(err_msg)

                return {
                    "share_id": data.get("shareid"),
                    "uk": data.get("uk"),
                    "sign": data.get("sign"),
                    "timestamp": data.get("timestamp"),
                    "randsk": data.get("randsk"),
                    "title": data.get("title", "Shared Files"),
                    "file_list": data.get("list", []),
                }
            except (AuthenticationError, DownloadFailedError):
                raise
            except Exception as e:
                if attempt == 3:
                    raise DownloadFailedError(f"Failed to resolve TeraBox share info: {e}")
                time.sleep(1)

        raise DownloadFailedError("Failed to resolve TeraBox share info after retries.")

    def _get_folder_contents(
        self,
        session: requests.Session,
        share_id: Any,
        uk: Any,
        sign: Any,
        timestamp: Any,
        directory: str,
        target_url: str,
    ) -> List[Dict[str, Any]]:
        """Recursively lists all files in a folder directory."""
        app_id = self.config.get("app_id", APP_ID)
        timeout = int(self.config.get("timeout", 30))
        all_items: List[Dict[str, Any]] = []
        page = 1
        limit = 100

        while True:
            params = {
                "shareid": share_id,
                "uk": uk,
                "sign": sign,
                "timestamp": timestamp,
                "dir": directory,
                "root": "1" if directory == "/" else "0",
                "app_id": app_id,
                "web": "1",
                "channel": "dubox",
                "clienttype": "0",
                "page": page,
                "num": limit,
                "order": "name",
            }
            if self._cached_js_token:
                params["jsToken"] = self._cached_js_token

            url = f"{BASE_URL}/share/list"
            try:
                resp = session.get(url, params=params, timeout=timeout)
                data = resp.json()

                if data.get("errno") in (400210, 4000023):
                    self._cached_js_token = None
                    self._fetch_js_token(session, target_url)
                    if self._cached_js_token:
                        params["jsToken"] = self._cached_js_token
                    resp = session.get(url, params=params, timeout=timeout)
                    data = resp.json()

                if data.get("errno", 0) != 0:
                    break

                items = data.get("list", [])
                if items:
                    all_items.extend(items)
                    page += 1
                    if len(items) < limit:
                        break
                else:
                    break
            except Exception:
                break

        return all_items

    def _get_download_link(
        self,
        session: requests.Session,
        fs_id: Any,
        share_id: Any,
        uk: Any,
        sign: Any,
        timestamp: Any,
        randsk: Any,
    ) -> Optional[str]:
        """Retrieves direct CDN download URL via /api/sharedownload."""
        app_id = self.config.get("app_id", APP_ID)
        timeout = int(self.config.get("timeout", 30))

        decoded_randsk = urllib.parse.unquote(randsk) if '%' in str(randsk) else str(randsk)
        extra = f'{{"sekey":"{decoded_randsk}"}}'
        extra_escaped = urllib.parse.quote(extra)
        data = f"encrypt=0&extra={extra_escaped}&fid_list=[{fs_id}]&primaryid={share_id}&uk={uk}&product=share&type=nolimit"
        uri = f"{BASE_URL}/api/sharedownload?app_id={app_id}&channel=chunlei&clienttype=12&sign={sign}&timestamp={timestamp}&web=1"

        post_headers = session.headers.copy()
        post_headers["Content-Type"] = "application/x-www-form-urlencoded"

        for _ in range(3):
            try:
                resp = session.post(uri, headers=post_headers, data=data, timeout=timeout)
                resp_data = resp.json()
                if resp_data.get("errno") == 0 and resp_data.get("list"):
                    return resp_data["list"][0].get("dlink")
                if resp_data.get("errno") in (-20, 12, 112):
                    if Style.is_interactive():
                        err_msg = _get_error_message(resp_data.get("errno"))
                        new_cookie = self._prompt_ndus_cookie(reason=err_msg)
                        for domain in [".terabox.com", ".1024tera.com", ".teraboxlink.com", ".freeterabox.com"]:
                            session.cookies.set("ndus", new_cookie, domain=domain)
                        continue
                time.sleep(1)
            except Exception:
                time.sleep(1)

        return None

    async def extract_info(self, url: str) -> Dict[str, Any]:
        """Extracts metadata from a TeraBox share without downloading."""
        def _extract() -> Dict[str, Any]:
            short_key = self.extract_short_key(url)
            ndus_cookie = self._resolve_ndus_cookie()
            session = self._create_session(ndus_cookie)
            return self._get_share_info(session, short_key, url)

        try:
            return await asyncio.to_thread(_extract)
        except Exception as e:
            raise DownloadFailedError(f"TeraBox metadata extraction failed: {e}")

    async def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> ArchiveEntry:
        """Executes the TeraBox download pipeline."""
        out_dir = Path(task.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        short_key = self.extract_short_key(task.url)
        ndus_cookie = self._resolve_ndus_cookie(task.options)
        if not ndus_cookie:
            print(f"\n{Style.tag('⚠️', 'AUTH', Style.YELLOW)} {Style.yellow('No ndus cookie provided.')}")
            print(f"  {Style.dim('1. Add ndus_cookie to configs/terabox-dl.json')}")
            print(f"  {Style.dim('2. Or set TERABOX_NDUS in your .env file')}")
            print(f"  {Style.dim('3. Or pass --ndus <cookie> on CLI')}\n")

        session = self._create_session(ndus_cookie)

        def _resolve_pipeline() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
            info = self._get_share_info(session, short_key, task.url)
            title = info.get("title", "Shared Files")
            file_list = info.get("file_list", [])

            # Flatten directory trees
            queue: List[Dict[str, Any]] = []

            def _walk(items: List[Dict[str, Any]], rel_dir: str):
                for item in items:
                    is_dir = item.get("isdir") == 1 or str(item.get("isdir")).strip() == "1" or item.get("isdir") is True
                    if is_dir:
                        sub_dir_name = _get_safe_filename(item.get("server_filename"))
                        sub_path = f"{rel_dir}/{sub_dir_name}" if rel_dir else sub_dir_name
                        folder_path = item.get("path") or f"/{sub_dir_name}"
                        sub_items = self._get_folder_contents(
                            session,
                            info["share_id"],
                            info["uk"],
                            info["sign"],
                            info["timestamp"],
                            folder_path,
                            task.url,
                        )
                        _walk(sub_items, sub_path)
                    else:
                        queue.append({
                            "item": item,
                            "rel_dir": rel_dir,
                            "share_id": info["share_id"],
                            "uk": info["uk"],
                            "sign": info["sign"],
                            "timestamp": info["timestamp"],
                            "randsk": info["randsk"],
                        })

            _walk(file_list, "")
            return info, queue

        info, queue = await asyncio.to_thread(_resolve_pipeline)
        if not queue:
            raise DownloadFailedError("No downloadable files found in this TeraBox share.")

        share_title = info.get("title", "Shared Files")
        total_files = len(queue)
        total_share_bytes = sum(int(q["item"].get("size", 0)) for q in queue)

        print(f"{Style.tag('🎬', 'MEDIA', Style.YELLOW)} {Style.white(share_title)}")
        if total_files > 1:
            print(f"{Style.tag('📦', 'BATCH', Style.MAGENTA)} Detected {Style.cyan(str(total_files))} files in share ({format_bytes(total_share_bytes)})")

        # Multi-file batch tracker or single file progress
        is_batch = total_files > 1
        batch_tracker = _TeraboxBatchTracker(total_files=total_files, total_batch_bytes=total_share_bytes) if is_batch else None

        downloaded_paths: List[Path] = []
        max_retries = int(self.config.get("max_retries", 10))
        chunk_size = int(self.config.get("chunk_size_kb", 64)) * 1024
        resume_enabled = bool(self.config.get("resume", True))
        timeout = int(self.config.get("timeout", 30))

        def _download_all() -> None:
            for idx, q_entry in enumerate(queue, 1):
                item = q_entry["item"]
                rel_dir = q_entry["rel_dir"]
                raw_filename = item.get("server_filename") or f"file_{idx}"
                filename = _get_safe_filename(raw_filename)
                file_size = int(item.get("size", 0))
                fs_id = item.get("fs_id")

                target_folder = out_dir / rel_dir if rel_dir else out_dir
                target_folder.mkdir(parents=True, exist_ok=True)
                dest_path = target_folder / filename

                # 1. Resolve CDN link
                dlink = item.get("dlink")
                if not dlink:
                    dlink = self._get_download_link(
                        session,
                        fs_id,
                        q_entry["share_id"],
                        q_entry["uk"],
                        q_entry["sign"],
                        q_entry["timestamp"],
                        q_entry["randsk"],
                    )

                if not dlink:
                    if batch_tracker:
                        batch_tracker.finish_file(filename, 0, skipped=True)
                    continue

                # 2. Check existing / collision
                if dest_path.exists():
                    exist_sz = dest_path.stat().st_size
                    if exist_sz == file_size and file_size > 0:
                        downloaded_paths.append(dest_path)
                        if batch_tracker:
                            batch_tracker.finish_file(filename, file_size, skipped=True)
                        continue

                # 3. Stream download with retry
                success = False
                for attempt in range(1, max_retries + 1):
                    try:
                        resume_pos = 0
                        if resume_enabled and dest_path.exists():
                            resume_pos = dest_path.stat().st_size
                            if resume_pos >= file_size and file_size > 0:
                                downloaded_paths.append(dest_path)
                                if batch_tracker:
                                    batch_tracker.finish_file(filename, file_size, skipped=True)
                                success = True
                                break

                        req_headers = session.headers.copy()
                        if resume_pos > 0:
                            req_headers["Range"] = f"bytes={resume_pos}-"

                        start_time = time.time()
                        last_tick = start_time
                        last_bytes = resume_pos
                        current_speed = 0.0

                        with session.get(dlink, headers=req_headers, stream=True, timeout=timeout) as resp:
                            if resp.status_code not in (200, 206):
                                resp.raise_for_status()

                            is_append = resp.status_code == 206
                            mode = "ab" if is_append else "wb"
                            downloaded_now = resume_pos if is_append else 0

                            with open(dest_path, mode) as f:
                                for chunk in resp.iter_content(chunk_size=chunk_size):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    downloaded_now += len(chunk)
                                    now = time.time()

                                    # Update speed and ETA every 0.2s
                                    if now - last_tick >= 0.2:
                                        delta_t = now - last_tick
                                        delta_b = downloaded_now - last_bytes
                                        current_speed = delta_b / delta_t if delta_t > 0 else 0.0
                                        last_tick = now
                                        last_bytes = downloaded_now

                                        rem_bytes = max(0, file_size - downloaded_now) if file_size > 0 else 0
                                        eta_sec = int(rem_bytes / current_speed) if current_speed > 0 else None

                                        if batch_tracker:
                                            batch_tracker.update_file(
                                                filename=filename,
                                                downloaded=downloaded_now,
                                                total=file_size,
                                                speed=current_speed,
                                                eta=eta_sec,
                                            )
                                        elif progress_callback:
                                            prog = DownloadProgress(
                                                downloaded_bytes=downloaded_now,
                                                total_bytes=file_size,
                                                speed_bytes_sec=current_speed,
                                                eta_seconds=eta_sec,
                                                current_file=filename,
                                                file_index=idx,
                                                total_files=total_files,
                                            )
                                            prog.update_percent()
                                            progress_callback(prog)

                        downloaded_paths.append(dest_path)
                        if batch_tracker:
                            batch_tracker.finish_file(filename, file_size)
                        success = True
                        break

                    except Exception:
                        if attempt == max_retries:
                            if batch_tracker:
                                batch_tracker.finish_file(filename, 0, skipped=True)
                        else:
                            time.sleep(1)

        await asyncio.to_thread(_download_all)

        if batch_tracker:
            batch_tracker.finish()

        if not downloaded_paths:
            raise DownloadFailedError("Failed to download any files from TeraBox.")

        # Determine primary file or folder path
        primary_path = downloaded_paths[0] if len(downloaded_paths) == 1 else out_dir
        final_filename = primary_path.name if len(downloaded_paths) == 1 else share_title
        total_size = sum(p.stat().st_size for p in downloaded_paths if p.exists())
        media_type = _detect_media_type(primary_path.name) if len(downloaded_paths) == 1 else MediaType.UNKNOWN

        # Calculate SHA256 of primary file (or first downloaded file)
        file_hash = None
        first_file = downloaded_paths[0]
        if first_file.exists():
            try:
                hasher = hashlib.sha256()
                with open(first_file, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)
                file_hash = hasher.hexdigest()
            except Exception:
                pass

        return ArchiveEntry(
            id=str(uuid.uuid4()),
            url=task.url,
            file_path=str(primary_path.resolve()),
            file_name=final_filename,
            file_hash=file_hash,
            file_size=total_size,
            backend=self.name,
            source_site="terabox",
            media_type=media_type,
            metadata={
                "share_title": share_title,
                "total_files": len(downloaded_paths),
                "share_id": info.get("share_id"),
            },
        )

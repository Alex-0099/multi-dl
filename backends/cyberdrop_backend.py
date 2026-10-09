"""
Adapter backend wrapping cyberdrop-dl for MULTI_DOWNLOADER.
Features:
- Subprocess execution with asynchronous stdout streaming
- Comprehensive support for file lockers (Bunkr, Gofile, Pixeldrain, Catbox, Coomer, Kemono, Saint, etc.)
- Forum thread crawler support (SimpCity, F95zone, ViperGirls)
- Config isolation via configs/cyberdrop-dl.json
- Domain conflict management and automatic failover to gallery-dl on anti-bot/unsupported locker errors
- Integration with unified terminal styling and SQLite archive
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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import urllib.parse
import uuid

from backends.base import BaseBackend
from core.exceptions import DownloadFailedError, EngineNotFoundError
from core.models import ArchiveEntry, DownloadProgress, DownloadTask, MediaType
from core.terminal import Style, format_bytes


def _detect_media_type(filename: str) -> MediaType:
    """Infers MediaType enum from filename extension."""
    ext = Path(filename).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff"):
        return MediaType.IMAGE
    if ext in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v"):
        return MediaType.VIDEO
    if ext in (".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus"):
        return MediaType.AUDIO
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".pdf", ".txt", ".cbz", ".cbr"):
        return MediaType.DOCUMENT
    return MediaType.UNKNOWN


def _probe_file_size(url: str) -> Tuple[Optional[int], Optional[int]]:
    """Lightweight direct metadata probe for supported file lockers. Returns (total_bytes, total_files)."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = (parsed.netloc or "").lower()
        parts = parsed.path.strip("/").split("/")

        # Pixeldrain single file
        if "pixeldrain.com" in domain and len(parts) >= 2 and parts[0] in ("u", "file"):
            file_id = parts[1]
            api_url = f"https://pixeldrain.com/api/file/{file_id}/info"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("success") and data.get("size"):
                        return int(data["size"]), 1

        # Pixeldrain album / list
        elif "pixeldrain.com" in domain and len(parts) >= 2 and parts[0] == "l":
            list_id = parts[1]
            api_url = f"https://pixeldrain.com/api/list/{list_id}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    files = data.get("files") or []
                    if files:
                        tot_size = sum(f.get("size", 0) for f in files)
                        return tot_size, len(files)

        # Catbox direct links
        elif "catbox.moe" in domain and parsed.path.strip("/"):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                cl = resp.headers.get("Content-Length")
                if cl:
                    return int(cl), 1

        # Direct media files
        elif Path(parsed.path).suffix.lower() in (
            ".mp4", ".mkv", ".webm", ".avi", ".mov", ".zip", ".rar", ".7z", ".tar", ".gz",
            ".jpg", ".png", ".gif", ".webp", ".mp3", ".flac"
        ):
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                cl = resp.headers.get("Content-Length")
                if cl:
                    return int(cl), 1

    except Exception:
        pass
    return None, None


def _query_cdl_db_size(filename: Optional[str] = None, url: Optional[str] = None) -> Tuple[Optional[int], Optional[int]]:
    """
    Attempts to look up expected total size and file count from cyberdrop.db.
    Supports both single items and multi-file albums/threads.
    Returns (total_bytes, total_files).
    """
    try:
        db_path = Path(os.path.expandvars(r"%APPDATA%\cyberdrop-dl\cyberdrop.db"))
        if not db_path.exists():
            return None, None
        import sqlite3
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
            cur = conn.cursor()

            # 1. Look up by source URL, slug, or album_id in media table
            if url:
                parsed = urllib.parse.urlparse(url)
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
                slug_parts = [p for p in parsed.path.strip("/").split("/") if p]
                slug = slug_parts[-1] if slug_parts else ""
                cur.execute(
                    """
                    SELECT COUNT(*), SUM(file_size) FROM media 
                    WHERE (album_id = ? OR referer LIKE ? OR referer LIKE ? OR url_path LIKE ?) 
                      AND file_size > 0
                    """,
                    (slug, f"%{clean_url}%", f"%{slug}%", f"%{slug}%")
                )
                row = cur.fetchone()
                if row and row[0] and row[1]:
                    return int(row[1]), int(row[0])

            # 2. Look up by downloaded filename, original filename, or CDN url_path
            if filename:
                cur.execute(
                    """
                    SELECT file_size FROM media 
                    WHERE (download_filename LIKE ? OR original_filename LIKE ? OR url_path LIKE ?) 
                      AND file_size > 0 
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (f"%{filename}%", f"%{filename}%", f"%{filename}%")
                )
                row = cur.fetchone()
                if row and row[0]:
                    return int(row[0]), 1

    except Exception:
        pass
    return None, None





class CyberdropDlBackend(BaseBackend):
    """
    Subprocess-based cyberdrop-dl adapter for MULTI_DOWNLOADER.
    Handles bulk file lockers, media hosts, and recursive forum threads.
    """

    name: str = "cyberdrop-dl"
    supported_domains: List[str] = [
        "cyberdrop.me", "bunkr.is", "bunkr.si", "bunkr.cr", "bunkr.black", "bunkr.site", "bunkr.ws", "bunkr.ac",
        "gofile.io", "pixeldrain.com", "catbox.moe", "files.catbox.moe",
        "coomer.su", "coomer.party", "kemono.su", "kemono.party",
        "saint.to", "sendvid.com", "streamtape.com", "jpg.church", "puter.com",
        "simpcity.su", "simpcity.to", "f95zone.to", "vipergirls.to", "mega.nz",
        "scrolller.com", "erome.com", "fapello.com", "postimg.cc", "pixl.is", "redgifs.com",
        "imageban.ru", "imgbox.com", "ibb.co", "xbunker.cc"
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._load_modular_config()

    def _load_modular_config(self) -> None:
        """Loads and merges options from configs/cyberdrop-dl.json if present."""
        cfg_path_str = self.config.get("config_file", "configs/cyberdrop-dl.json")
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

    def _resolve_executable(self) -> List[str]:
        """
        Locates the cyberdrop-dl binary or python module invocation.
        Checks:
        1. Configured custom executable_path
        2. System PATH via shutil.which('cyberdrop-dl')
        3. ~/.local/bin/cyberdrop-dl.exe
        4. Current Python environment Scripts/
        5. Python -c module fallback
        """
        custom = self.config.get("executable_path")
        if custom and Path(custom).exists():
            return [str(custom)]

        # 1. System PATH
        which_path = shutil.which("cyberdrop-dl") or shutil.which("cyberdrop-dl.exe")
        if which_path:
            return [which_path]

        # 2. User local bin (~/.local/bin)
        user_local = Path.home() / ".local" / "bin"
        for candidate in [user_local / "cyberdrop-dl.exe", user_local / "cyberdrop-dl"]:
            if candidate.exists():
                return [str(candidate)]

        # 3. Virtual environment Scripts directory
        scripts_dir = Path(sys.executable).parent
        for candidate in [scripts_dir / "cyberdrop-dl.exe", scripts_dir / "Scripts" / "cyberdrop-dl.exe"]:
            if candidate.exists():
                return [str(candidate)]

        # 4. Direct Python fallback
        return [sys.executable, "-c", "import sys, cyberdrop_dl.main; sys.exit(cyberdrop_dl.main.main())"]

    def can_handle(self, url: str) -> bool:
        """Returns True if the URL targets any supported domain or forum thread."""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = (parsed.netloc or "").lower().split(":")[0]
            if not domain:
                return False
            for d in self.supported_domains:
                if domain == d or domain.endswith("." + d):
                    return True
            return False
        except Exception:
            return False

    def _scan_files(self, directory: Path) -> Set[Path]:
        """Returns a set of all existing file paths under a directory."""
        if not directory.exists():
            return set()
        return {p for p in directory.rglob("*") if p.is_file()}

    async def extract_info(self, url: str) -> Dict[str, Any]:
        """Extracts basic URL metadata without running full download."""
        parsed = urllib.parse.urlparse(url)
        domain = (parsed.netloc or "").lower().split(":")[0]
        return {
            "url": url,
            "domain": domain,
            "backend": self.name,
            "can_handle": self.can_handle(url),
        }

    def _flatten_loose_files(self, directory: Path) -> None:
        """
        Flattens any 'Loose Files (*)' directory created by cyberdrop-dl
        directly into the target site directory, matching gallery-dl's clean structure.
        """
        try:
            for item in list(directory.glob("Loose Files*")):
                if item.is_dir():
                    for child in list(item.iterdir()):
                        dest = directory / child.name
                        if not dest.exists():
                            shutil.move(str(child), str(dest))
                        elif dest.resolve() != child.resolve():
                            if child.is_dir() and dest.is_dir():
                                for sub in list(child.iterdir()):
                                    sub_dest = dest / sub.name
                                    if not sub_dest.exists():
                                        shutil.move(str(sub), str(sub_dest))
                                shutil.rmtree(str(child), ignore_errors=True)
                            else:
                                stem, suffix = child.stem, child.suffix
                                counter = 1
                                while (directory / f"{stem}_{counter}{suffix}").exists():
                                    counter += 1
                                shutil.move(str(child), str(directory / f"{stem}_{counter}{suffix}"))
                    try:
                        shutil.rmtree(str(item), ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass

    def _deduplicate_domain_folder(self, directory: Path) -> None:
        """
        If a nested subfolder with the same name as directory exists (e.g. gofile/gofile),
        moves its contents up into directory and removes the duplicate subfolder.
        """
        try:
            dup = directory / directory.name
            if dup.is_dir() and dup.resolve() != directory.resolve():
                for child in list(dup.iterdir()):
                    dest = directory / child.name
                    if not dest.exists():
                        shutil.move(str(child), str(dest))
                    elif dest.resolve() != child.resolve():
                        if child.is_dir() and dest.is_dir():
                            for inner in list(child.iterdir()):
                                inner_dest = dest / inner.name
                                if not inner_dest.exists():
                                    shutil.move(str(inner), str(inner_dest))
                            shutil.rmtree(str(child), ignore_errors=True)
                        else:
                            stem, suffix = child.stem, child.suffix
                            counter = 1
                            while (directory / f"{stem}_{counter}{suffix}").exists():
                                counter += 1
                            shutil.move(str(child), str(directory / f"{stem}_{counter}{suffix}"))
                try:
                    shutil.rmtree(str(dup), ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass

    async def download(
        self,
        task: DownloadTask,
        progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
    ) -> ArchiveEntry:
        """
        Executes cyberdrop-dl download via async subprocess.
        Streams stdout, formats terminal output, tracks newly downloaded files,
        reports real-time byte progress, and provides automatic failover to gallery-dl.
        """
        out_dir = Path(task.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        exe_cmd = self._resolve_executable()
        cmd = list(exe_cmd) + [
            "download",
            task.url,
            "--ui", "disabled",
            "--no-stats",
            "--no-print-traffic",
            "--download-folder", str(out_dir),
        ]

        # Directory placement and subfolder controls matching gallery-dl
        if self.config.get("subfolders", True):
            cmd.append("--subfolders")
            if not self.config.get("include_domain_in_subfolders", False):
                cmd.append("--subfolders.include.no-domain")
        else:
            cmd.append("--no-subfolders")

        if self.config.get("separate_posts", False):
            cmd.append("--separate-posts")
        else:
            cmd.append("--no-separate-posts")

        if self.config.get("delete_empty_folders", True):
            cmd.append("--delete-empty-folders")

        rate_limit = self.config.get("rate_limit")
        if rate_limit:
            cmd.extend(["--rate-limit", str(rate_limit)])

        if self.config.get("ignore_history", True):
            cmd.append("--ignore-history")
        if self.config.get("ignore_hashes", True):
            cmd.append("--ignore-hashes")

        # Apply cookies if provided
        cookies = task.options.get("cookies") or self.config.get("cookies_file")
        if cookies and Path(str(cookies)).exists():
            cmd.extend(["--cookies", str(cookies)])

        # Apply proxy if provided
        proxy = task.options.get("proxy") or self.config.get("proxy")
        if proxy:
            cmd.extend(["--proxy", str(proxy)])

        # Apply FlareSolverr if configured
        flaresolverr = self.config.get("flaresolverr")
        if flaresolverr:
            cmd.extend(["--flaresolverr", str(flaresolverr)])

        # Take snapshot of files before download to isolate new transfers
        before_files = self._scan_files(out_dir)

        print(f"{Style.tag('🚀', 'CYBERDROP', Style.MAGENTA)} Launching {Style.engine_badge('cyberdrop-dl')} engine...")

        stdout_lines: List[str] = []
        failover_detected = False
        unsupported_detected = False

        # Setup UTF-8 subprocess environment
        sub_env = dict(os.environ)
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        # Probe expected size from locker API, HEAD headers, or cyberdrop.db before transfer
        probed_bytes, probed_files = _probe_file_size(task.url)
        if not probed_bytes:
            probed_bytes, probed_files = _query_cdl_db_size(url=task.url)

        stop_monitor = asyncio.Event()
        progress_active: Dict[str, Any] = {
            "last_file": None,
            "total_bytes": probed_bytes,
            "total_files": probed_files,
        }

        async def _track_progress():
            last_bytes = 0
            last_time = time.time()
            speed_samples: List[float] = []

            while not stop_monitor.is_set():
                try:
                    part_files = [f for f in out_dir.rglob("*.part") if f.is_file()]
                    completed_files = [
                        f for f in out_dir.rglob("*")
                        if f.is_file() and not f.name.endswith(".part") and f not in before_files
                    ]

                    cur_bytes = sum(f.stat().st_size for f in completed_files)
                    active_name = None

                    if part_files:
                        latest_part = max(part_files, key=lambda f: f.stat().st_mtime)
                        part_size = latest_part.stat().st_size
                        cur_bytes += part_size
                        clean_name = latest_part.name[:-5]
                        active_name = clean_name
                        if clean_name != progress_active["last_file"]:
                            progress_active["last_file"] = clean_name
                            if not progress_active.get("total_bytes"):
                                b, count = _query_cdl_db_size(clean_name, task.url)
                                if b:
                                    progress_active["total_bytes"] = b
                                    progress_active["total_files"] = count

                        # Continuously retry if total size is not yet resolved
                        if progress_active.get("total_bytes") is None:
                            b, count = _query_cdl_db_size(clean_name, task.url)
                            if b:
                                progress_active["total_bytes"] = b
                                progress_active["total_files"] = count
                    elif completed_files:
                        active_name = max(completed_files, key=lambda f: f.stat().st_mtime).name

                    now = time.time()
                    dt = now - last_time
                    if dt >= 0.25:
                        inst_speed = max(0.0, (cur_bytes - last_bytes) / dt) if dt > 0 else 0.0
                        speed_samples.append(inst_speed)
                        if len(speed_samples) > 6:
                            speed_samples.pop(0)
                        avg_speed = sum(speed_samples) / len(speed_samples) if speed_samples else 0.0
                        last_bytes = cur_bytes
                        last_time = now

                        total_b = progress_active.get("total_bytes")
                        total_cnt = progress_active.get("total_files")
                        current_file_index = len(completed_files) + (1 if part_files else 0)

                        pct = None
                        eta = None
                        if total_b and total_b > 0:
                            pct = min(100.0, (cur_bytes / total_b) * 100.0)
                            if avg_speed > 0 and cur_bytes < total_b:
                                eta = int((total_b - cur_bytes) / avg_speed)

                        if progress_callback and (cur_bytes > 0 or part_files):
                            prog = DownloadProgress(
                                downloaded_bytes=cur_bytes,
                                total_bytes=total_b,
                                speed_bytes_sec=avg_speed,
                                eta_seconds=eta,
                                percent=pct,
                                current_file=active_name,
                                total_files=total_cnt,
                                file_index=current_file_index,
                            )
                            progress_callback(prog)
                except Exception:
                    pass

                await asyncio.sleep(0.3)

        monitor_task = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=sub_env,
            )

            monitor_task = asyncio.create_task(_track_progress())

            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                stdout_lines.append(line)

                # Filter and style key log events cleanly
                lower_line = line.lower()
                if "scraping" in lower_line and ("http" in lower_line or "[" in lower_line):
                    target_info = line.split("Scraping", 1)[-1].strip() if "Scraping" in line else line
                    print(f"{Style.tag('🔍', 'SCRAPING', Style.CYAN)} {Style.dim(target_info)}")
                elif "skipping" in lower_line and ("hash" in lower_line or "already" in lower_line):
                    print(f"{Style.tag('⚠️', 'SKIPPED', Style.YELLOW)} {Style.dim('File already downloaded in database')}")
                elif "unsupported url" in lower_line:
                    unsupported_detected = True
                    print(f"{Style.tag('⚠️', 'UNSUPPORTED', Style.YELLOW)} {Style.warning(line)}")
                elif (
                    line.startswith("ERROR")
                    or line.startswith("CRITICAL")
                    or "403 forbidden" in lower_line
                    or "anti-bot" in lower_line
                    or "challenge failed" in lower_line
                ):
                    failover_detected = True
                    print(f"{Style.tag('⚠️', 'NOTICE', Style.YELLOW)} {Style.dim(line)}")

            await proc.wait()

        except Exception as e:
            raise DownloadFailedError(f"Failed to execute cyberdrop-dl subprocess: {e}")
        finally:
            stop_monitor.set()
            if monitor_task:
                await monitor_task

        # Post-download folder normalization matching gallery-dl structure:
        # 1. Flatten "Loose Files (*)" into out_dir so single files sit cleanly in the site directory
        if self.config.get("flatten_loose_files", True):
            self._flatten_loose_files(out_dir)

        # 2. Prevent/repair double-nested domain folder (e.g. out_dir / out_dir.name)
        self._deduplicate_domain_folder(out_dir)

        # Scan for newly created files
        after_files = self._scan_files(out_dir)
        new_files = sorted(list(after_files - before_files), key=lambda p: p.stat().st_mtime)

        # ── Automatic Failover Check ──
        if not new_files and (proc.returncode != 0 or failover_detected or unsupported_detected):
            if self.config.get("enable_failover_to_gallerydl", True):
                from backends.gallerydl_backend import GalleryDlBackend
                from core.router import URLRouter
                gdl = GalleryDlBackend()
                if gdl.can_handle(task.url):
                    print(f"\n{Style.tag('🔄', 'FAILOVER', Style.YELLOW)} cyberdrop-dl locker error encountered.")
                    print(f"{Style.tag('⚙️', 'FAILOVER', Style.CYAN)} Attempting automatic fallback with {Style.engine_badge('gallery-dl')}...")
                    host_id = URLRouter.get_host_identifier(task.url, self.name)
                    failover_dir = str(out_dir.parent) if out_dir.name.lower() == host_id.lower() else str(out_dir)
                    failover_task = DownloadTask(
                        url=task.url,
                        backend="gallery-dl",
                        output_dir=failover_dir,
                        options=task.options,
                    )
                    return await gdl.download(failover_task, progress_callback=progress_callback)

            err_details = "\n".join(stdout_lines[-5:]) if stdout_lines else "Process exited with code 1"
            raise DownloadFailedError(f"cyberdrop-dl download failed:\n{err_details}")

        # Partial failure recovery: some files transferred but download errors occurred on others
        if new_files and failover_detected and self.config.get("enable_failover_to_gallerydl", True):
            from backends.gallerydl_backend import GalleryDlBackend
            from core.router import URLRouter
            gdl = GalleryDlBackend()
            if gdl.can_handle(task.url):
                print(f"\n{Style.tag('⚠️', 'PARTIAL ERROR', Style.YELLOW)} cyberdrop-dl dropped files during batch/album download.")
                print(f"{Style.tag('⚙️', 'FAILOVER', Style.CYAN)} Passing to {Style.engine_badge('gallery-dl')} to ensure all files are completely retrieved...")
                host_id = URLRouter.get_host_identifier(task.url, self.name)
                failover_dir = str(out_dir.parent) if out_dir.name.lower() == host_id.lower() else str(out_dir)
                failover_task = DownloadTask(
                    url=task.url,
                    backend="gallery-dl",
                    output_dir=failover_dir,
                    options=task.options,
                )
                return await gdl.download(failover_task, progress_callback=progress_callback)

        if not new_files:
            # Check if files already existed or were scanned without modification
            if after_files:
                new_files = sorted(list(after_files), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
            else:
                raise DownloadFailedError("cyberdrop-dl completed but no downloaded files were found.")

        primary_file = new_files[0]
        total_size = sum(f.stat().st_size for f in new_files if f.exists())
        file_count = len(new_files)

        # Final progress update on completion
        if progress_callback and total_size > 0:
            prog = DownloadProgress(
                downloaded_bytes=total_size,
                total_bytes=total_size,
                speed_bytes_sec=0.0,
                eta_seconds=0,
                percent=100.0,
                current_file=primary_file.name,
                total_files=file_count,
                file_index=file_count,
            )
            progress_callback(prog)
            print()  # Finalize the carriage return line

        # Calculate file hash for primary file
        file_hash = None
        if primary_file.exists():
            try:
                hasher = hashlib.sha256()
                with open(primary_file, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)
                file_hash = hasher.hexdigest()
            except Exception:
                pass

        final_name = primary_file.name if file_count == 1 else f"{primary_file.parent.name} ({file_count} files)"

        return ArchiveEntry(
            id=str(uuid.uuid4()),
            url=task.url,
            file_path=str(primary_file.resolve()),
            file_name=final_name,
            file_hash=file_hash,
            file_size=total_size,
            backend=self.name,
            source_site="cyberdrop",
            media_type=_detect_media_type(primary_file.name),
            metadata={
                "total_downloaded": file_count,
                "command": cmd,
            },
        )

"""
Terminal formatting and color utilities for MULTI_DOWNLOADER.
Provides unified, consistent ANSI styling and aligned status tags across all CLI outputs.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

import unicodedata

# Enable ANSI virtual terminal processing and ensure UTF-8 encoding on Windows cmd/powershell
if sys.platform == "win32":
    os.system("")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_terminal_write(text: str) -> None:
    """Safely writes text to sys.stdout, preventing UnicodeEncodeError crashes on non-UTF-8 terminals."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        clean = text.encode(enc, errors="replace").decode(enc, errors="replace")
        try:
            sys.stdout.write(clean)
        except Exception:
            pass
    except Exception:
        pass


def char_width(ch: str) -> int:
    """Returns terminal column width (2 for wide/fullwidth CJK and emojis, 1 otherwise)."""
    w = unicodedata.east_asian_width(ch)
    return 2 if w in ("W", "F") else 1


def str_width(s: str) -> int:
    """Returns clean column length ignoring ANSI escapes and accounting for wide characters."""
    clean = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', s)
    return sum(char_width(ch) for ch in clean)


def truncate_to_width(s: str, max_cols: int) -> str:
    """Truncates string to maximum column width, appending '...' if truncated."""
    if str_width(s) <= max_cols:
        return s
    target = max(0, max_cols - 3)
    cur_cols = 0
    res = []
    for ch in s:
        w = char_width(ch)
        if cur_cols + w > target:
            break
        res.append(ch)
        cur_cols += w
    return "".join(res) + "..."


def format_bytes(size: Optional[float]) -> str:
    """Formats bytes into human-readable string (B, KB, MB, GB)."""
    if size is None:
        return "??"
    s = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if s < 1024.0:
            return f"{s:.2f} {unit}"
        s /= 1024.0
    return f"{s:.2f} PB"


def format_dual_bytes(downloaded: int, total: Optional[int]) -> str:
    """
    Formats downloaded and total bytes as a concise single-unit pair.
    Removes redundant unit from downloaded when units match and keeps no spaces around '/':
    Examples:
      26.0/52.0 MB
      100.0/500.0 KB
      1.2/2.4 GB
      15.0 MB/?? (when total is unknown)
    """
    if total is None or total <= 0:
        if downloaded <= 0:
            return "0.0 B/??"
        d_val = float(downloaded)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if d_val < 1024.0 or unit == "TB":
                return f"{d_val:.1f} {unit}/??"
            d_val /= 1024.0
        return f"{d_val:.1f} PB/??"

    t_val = float(total)
    d_val = float(downloaded)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if t_val < 1024.0 or unit == "TB":
            return f"{d_val:.1f}/{t_val:.1f} {unit}"
        t_val /= 1024.0
        d_val /= 1024.0
    return f"{d_val:.1f}/{t_val:.1f} PB"


def render_line_bar(percent: Optional[float], width: int = 14, color: str = "\033[96m") -> str:
    """
    Renders sleek line progress bar matching terabox-dl and Drawing-1.sketchpad.png:
    E.g. '[━━━━━━━───────]'
    """
    cyan = "\033[96m"
    gray = "\033[90m"
    green = "\033[92m"
    reset = "\033[0m"

    if percent is None or percent < 0:
        return f"{cyan}[{gray}{'─' * width}{cyan}]{reset}"

    clamped = min(max(percent, 0.0), 100.0)
    filled_len = int(round((clamped / 100.0) * width))
    empty_len = width - filled_len

    if clamped >= 100.0:
        return f"{green}[{green}{'━' * width}{green}]{reset}"

    filled_part = f"{color}{'━' * filled_len}{reset}"
    empty_part = f"{gray}{'─' * empty_len}{reset}"
    return f"{cyan}[{filled_part}{empty_part}{cyan}]{reset}"


class Style:
    """ANSI color and styling helper for CLI rendering."""

    format_bytes = staticmethod(format_bytes)

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    # Bright foreground palette
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    ENGINE_COLORS = {
        "yt-dlp": "\033[91m",       # YouTube Red
        "gallery-dl": "\033[93m",    # Gallery Amber / Yellow
        "telegram-dl": "\033[94m",   # Telegram Blue
        "terabox-dl": "\033[96m",    # TeraBox Cyan
        "cyberdrop-dl": "\033[95m",  # Cyberdrop Magenta
    }

    @classmethod
    def engine_color(cls, engine_name: str) -> str:
        """Returns the distinct ANSI color code for a download engine."""
        return cls.ENGINE_COLORS.get(engine_name.lower(), cls.CYAN)

    @classmethod
    def engine_badge(cls, engine_name: str) -> str:
        """Formats an engine name in its signature color, e.g. [yt-dlp] in red."""
        color = cls.engine_color(engine_name)
        return f"{color}{cls.BOLD}[{engine_name}]{cls.RESET}"

    @classmethod
    def progress_bar(cls, percent: Optional[float], width: int = 20) -> str:
        """
        Renders a solid Unicode progress bar of given width.
        E.g. '[███████████████░░░░░]'
        When percent is 100%, returns a fully green completed bar.
        When percent is None, returns an indeterminate dim bar.
        """
        if percent is None or percent < 0:
            return f"{cls.CYAN}[{cls.GRAY}{'░' * width}{cls.CYAN}]{cls.RESET}"

        clamped = min(max(percent, 0.0), 100.0)
        filled_len = int(round((clamped / 100.0) * width))
        empty_len = width - filled_len

        if clamped >= 100.0:
            return f"{cls.GREEN}[{cls.GREEN}{'█' * width}{cls.GREEN}]{cls.RESET}"

        filled_part = f"{cls.GREEN}{'█' * filled_len}{cls.RESET}"
        empty_part = f"{cls.GRAY}{'░' * empty_len}{cls.RESET}"
        return f"{cls.CYAN}[{filled_part}{empty_part}{cls.CYAN}]{cls.RESET}"

    @classmethod
    def tag(cls, emoji: str, text: str, color: str = CYAN) -> str:
        """
        Formats a standard status tag with strict single-space alignment:
        Example: '🎯 [ROUTING]' or '⚙️ [BACKEND]'
        Always strips accidental whitespace from emoji to enforce exact alignment.
        """
        clean_emoji = emoji.strip()
        return f"{clean_emoji} {color}{cls.BOLD}[{text}]{cls.RESET}"

    @classmethod
    def highlight(cls, text: str, color: str = WHITE) -> str:
        return f"{color}{text}{cls.RESET}"

    @classmethod
    def bold(cls, text: str) -> str:
        return f"{cls.BOLD}{text}{cls.RESET}"

    @classmethod
    def dim(cls, text: str) -> str:
        return f"{cls.DIM}{text}{cls.RESET}"

    @classmethod
    def muted(cls, text: str) -> str:
        return f"{cls.GRAY}{text}{cls.RESET}"

    @classmethod
    def cyan(cls, text: str) -> str:
        return f"{cls.CYAN}{text}{cls.RESET}"

    @classmethod
    def magenta(cls, text: str) -> str:
        return f"{cls.MAGENTA}{text}{cls.RESET}"

    @classmethod
    def green(cls, text: str) -> str:
        return f"{cls.GREEN}{text}{cls.RESET}"

    @classmethod
    def yellow(cls, text: str) -> str:
        return f"{cls.YELLOW}{text}{cls.RESET}"

    @classmethod
    def red(cls, text: str) -> str:
        return f"{cls.RED}{text}{cls.RESET}"

    @classmethod
    def blue(cls, text: str) -> str:
        return f"{cls.BLUE}{text}{cls.RESET}"

    @classmethod
    def white(cls, text: str) -> str:
        return f"{cls.WHITE}{text}{cls.RESET}"

    @classmethod
    def speed(cls, text: str) -> str:
        return f"{cls.GREEN}{cls.BOLD}{text}{cls.RESET}"

    @classmethod
    def path(cls, text: str) -> str:
        return f"{cls.WHITE}{cls.BOLD}{text}{cls.RESET}"

    @classmethod
    def error(cls, text: str) -> str:
        return f"{cls.RED}{cls.BOLD}{text}{cls.RESET}"

    @classmethod
    def warning(cls, text: str) -> str:
        return f"{cls.YELLOW}{text}{cls.RESET}"

    @classmethod
    def success(cls, text: str) -> str:
        return f"{cls.GREEN}{cls.BOLD}{text}{cls.RESET}"

    @classmethod
    def is_interactive(cls) -> bool:
        """Checks if standard input is attached to an interactive terminal."""
        try:
            return sys.stdin is not None and sys.stdin.isatty()
        except Exception:
            return False


@dataclass
class MultiBarSlot:
    """Represents an active visual slot in the download canvas."""
    slot_id: int
    item_id: Optional[str] = None
    backend: str = "idle"
    url_or_title: str = ""
    percent: Optional[float] = None
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed_bytes_sec: Optional[float] = None
    file_index: Optional[int] = None
    total_files: Optional[int] = None
    status: str = "idle"  # idle, active, completed, failed, skipped
    status_message: Optional[str] = None  # Live phase or verbose event


class MultiBarManager:
    """
    Thread-safe, non-colliding multi-stream progress bar renderer.
    Renders an in-place canvas matching Drawing-1.sketchpad.png:
      Header: MULTI_DOWNLOADER ─ Active Downloads (X/Y Workers)
      Row 1:  [Downloader]: [engine] • [title]
      Row 2:  [━━━━━━━───────] 50.0% • 14/28 • 26.0/52.0 MB • @ 4.2 MB/s • [verbose / current file]
      Footer: Queue: X remaining • Y completed • Z failed
    """

    def __init__(self, max_slots: int = 3, is_tty: Optional[bool] = None):
        self.max_slots = max(1, max_slots)
        self.is_tty = is_tty if is_tty is not None else (sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else True)
        self._lock = threading.Lock()
        self._slots: Dict[int, MultiBarSlot] = {
            i: MultiBarSlot(slot_id=i) for i in range(1, self.max_slots + 1)
        }
        self._remaining: int = 0
        self._completed: int = 0
        self._failed: int = 0
        self._lines_printed: int = 0
        self._last_render_time: float = 0.0
        self._throttle_interval: float = 0.05

    @staticmethod
    def _format_engine_badge(engine_name: str) -> str:
        """Formats engine badge with engine color for backward compatibility."""
        return Style.engine_badge(engine_name)

    @staticmethod
    def _visible_len(s: str) -> int:
        """Returns clean column length ignoring ANSI escapes and accounting for wide characters."""
        return str_width(s)

    def set_queue_counts(self, remaining: int, completed: int, failed: int) -> None:
        """Updates overall queue counter values displayed in footer."""
        with self._lock:
            self._remaining = max(0, remaining)
            self._completed = max(0, completed)
            self._failed = max(0, failed)
        self.render()

    def assign_slot(self, item_id: str, backend: str, url_or_title: str) -> int:
        """Assigns an idle slot for a new concurrent worker. Returns 1-based slot_id."""
        with self._lock:
            for slot_id, slot in self._slots.items():
                if slot.status in ("idle", "completed", "failed", "skipped"):
                    slot.item_id = item_id
                    slot.backend = backend
                    slot.url_or_title = url_or_title
                    slot.percent = None
                    slot.downloaded_bytes = 0
                    slot.total_bytes = None
                    slot.speed_bytes_sec = None
                    slot.file_index = None
                    slot.total_files = None
                    slot.status = "active"
                    slot.status_message = None
                    return slot_id
            # Fallback to slot 1 if all occupied
            s = self._slots[1]
            s.item_id = item_id
            s.backend = backend
            s.url_or_title = url_or_title
            s.status = "active"
            s.status_message = None
            return 1

    def update_slot(
        self,
        slot_id: int,
        percent: Optional[float] = None,
        downloaded: int = 0,
        total: Optional[int] = None,
        speed: Optional[float] = None,
        title: Optional[str] = None,
        file_index: Optional[int] = None,
        total_files: Optional[int] = None,
        status_message: Optional[str] = None,
    ) -> None:
        """Updates live metrics for a specific worker slot."""
        with self._lock:
            if slot_id in self._slots:
                slot = self._slots[slot_id]
                slot.percent = percent
                slot.downloaded_bytes = downloaded
                if total is not None:
                    slot.total_bytes = total
                if speed is not None:
                    slot.speed_bytes_sec = speed
                if title:
                    slot.url_or_title = title
                if file_index is not None:
                    slot.file_index = file_index
                if total_files is not None:
                    slot.total_files = total_files
                if status_message is not None:
                    slot.status_message = status_message
                slot.status = "active"
        self.render()

    def finish_slot(
        self,
        slot_id: int,
        display_path: str = "",
        status_type: str = "completed",
        error_msg: Optional[str] = None,
    ) -> None:
        """
        Marks a slot idle upon completion or failure without disrupting active canvas.
        """
        with self._lock:
            if slot_id in self._slots:
                slot = self._slots[slot_id]
                slot.status = "idle"
                slot.backend = "idle"
                slot.url_or_title = ""
                slot.percent = None
                slot.downloaded_bytes = 0
                slot.total_bytes = None
                slot.speed_bytes_sec = None
                slot.file_index = None
                slot.total_files = None
                slot.status_message = None
        self.render(force=True)

    def render(self, force: bool = False) -> None:
        """Atomically renders header, unified 2-line cards (Drawing-1.sketchpad.png), and footer."""
        now = time.time()
        if not force and (now - self._last_render_time) < self._throttle_interval:
            return
        self._last_render_time = now

        term_size = shutil.get_terminal_size((80, 24))
        term_width = term_size.columns
        term_height = term_size.lines
        max_width = max(40, min(term_width - 2, 105))
        bar_w = 12 if max_width < 80 else 14

        bullet = f"{Style.RED}•{Style.RESET}"
        sep = f" {bullet} "

        with self._lock:
            active_count = sum(1 for s in self._slots.values() if s.status == "active")

            # 1. Header Card (matching visual theme)
            if self.max_slots == 1:
                header_str = (
                    f"\033[48;5;236m\033[97m\033[1m MULTI_DOWNLOADER \033[0m"
                    f"\033[48;5;236m\033[90m─\033[0m"
                    f"\033[48;5;236m\033[97m Active Download \033[96m(1 Worker) \033[0m"
                )
            else:
                header_str = (
                    f"\033[48;5;236m\033[97m\033[1m MULTI_DOWNLOADER \033[0m"
                    f"\033[48;5;236m\033[90m─\033[0m"
                    f"\033[48;5;236m\033[97m Active Downloads \033[96m({active_count}/{self.max_slots} Workers) \033[0m"
                )

            lines_to_print = [header_str, ""]

            # Decide whether to include inter-slot spacing based on available terminal rows
            include_slot_spacing = term_height >= (self.max_slots * 3 + 4)

            # 2. Worker Slots (2 lines per slot per Drawing-1.sketchpad.png)
            for slot_id in range(1, self.max_slots + 1):
                slot = self._slots[slot_id]
                eng_name = slot.backend if slot.status == "active" else "idle"
                eng_badge = Style.engine_badge(eng_name)

                # Line 1: [Downloader]: [gallery-dl] • [title / gallery name]
                line1_prefix = f"{Style.CYAN}{Style.BOLD}[Downloader]:{Style.RESET} {eng_badge}{sep}"
                prefix1_len = str_width(line1_prefix)
                avail_title = max_width - prefix1_len

                if slot.status == "active" and slot.url_or_title:
                    title_text = slot.url_or_title
                    if avail_title > 6:
                        trunc_title = truncate_to_width(title_text, avail_title)
                        line1 = f"{line1_prefix}{Style.white(trunc_title)}"
                    else:
                        line1 = line1_prefix
                elif slot.status == "active":
                    line1 = f"{line1_prefix}{Style.dim('downloading...')}"
                else:
                    line1 = f"{line1_prefix}{Style.dim('idle')}"

                # Line 2: [━━━━━━━━━━━━━━] 50.0% • 14/28 • 26.0/52.0 MB • @ 4.2 MB/s • [verbose / current file]
                eng_color = Style.engine_color(slot.backend) if slot.status == "active" else Style.CYAN
                bar = render_line_bar(slot.percent if slot.status == "active" else None, width=bar_w, color=eng_color)

                if slot.status == "active" and slot.percent is not None:
                    pct_str = f"{slot.percent:.1f}%"
                    pct_color = Style.GREEN if slot.percent >= 100.0 else Style.CYAN
                    pct_colored = f"{pct_color}{pct_str}{Style.RESET}"
                else:
                    pct_colored = f"{Style.dim('--%')}"

                if slot.status == "active":
                    if slot.total_files and slot.total_files > 1:
                        cur_f = slot.file_index or 0
                        tot_f = slot.total_files
                        files_str = f"{cur_f}/{tot_f}"
                    else:
                        files_str = "1/1"
                    bytes_str = format_dual_bytes(slot.downloaded_bytes, slot.total_bytes)
                    if slot.speed_bytes_sec:
                        sp_b = format_bytes(int(slot.speed_bytes_sec)) + "/s"
                        sp_val = re.sub(r'(\.[0-9])0\s', r'\1 ', sp_b)
                        speed_str = f"{Style.dim('@')} {Style.speed(sp_val)}"
                    else:
                        speed_str = f"{Style.dim('@ --/s')}"

                    # Verbose message: sub-event status or active filename
                    if slot.status_message:
                        v_msg = slot.status_message
                    elif slot.file_index and slot.total_files and slot.total_files > 1 and slot.url_or_title:
                        v_msg = Path(slot.url_or_title).name
                    elif slot.url_or_title:
                        v_msg = Path(slot.url_or_title).name
                    else:
                        v_msg = "downloading..."
                    verbose_raw = f"[{v_msg}]"
                else:
                    files_str = "--/--"
                    bytes_str = "--/-- MB"
                    speed_str = f"{Style.dim('@ --/s')}"
                    verbose_raw = "[idle]"

                base_line2 = (
                    f"{bar} {pct_colored}{sep}"
                    f"{Style.white(files_str)}{sep}"
                    f"{Style.white(bytes_str)}{sep}"
                    f"{speed_str}{sep}"
                )
                base2_len = str_width(base_line2)
                avail_v = max_width - base2_len

                if avail_v > 6:
                    trunc_v = truncate_to_width(verbose_raw, avail_v)
                    line2 = f"{base_line2}{Style.dim(Style.cyan(trunc_v))}"
                else:
                    line2 = base_line2.rstrip()

                if str_width(line2) > max_width:
                    line2 = truncate_to_width(line2, max_width)

                lines_to_print.append(line1)
                lines_to_print.append(line2)

                if include_slot_spacing and slot_id < self.max_slots:
                    lines_to_print.append("")

            # 3. Spacing & Footer Line
            if self.max_slots > 1 or self._remaining > 0 or self._completed > 1:
                lines_to_print.append("")
                failed_color = Style.RED if self._failed > 0 else Style.DIM
                footer_line = (
                    f"{Style.BOLD}Queue:{Style.RESET} "
                    f"{Style.CYAN}{self._remaining} remaining{Style.RESET} {bullet} "
                    f"{Style.GREEN}{self._completed} completed{Style.RESET} {bullet} "
                    f"{failed_color}{self._failed} failed{Style.RESET}"
                )
                lines_to_print.append(footer_line)

        if not self.is_tty:
            return

        # Atomic terminal refresh without scrolling drift
        if self._lines_printed > 0:
            safe_terminal_write(f"\033[{self._lines_printed}A")

        printed_count = 0
        try:
            for line in lines_to_print:
                safe_terminal_write(f"\r\033[K{line}\n")
                printed_count += 1
            try:
                sys.stdout.flush()
            except Exception:
                pass
        finally:
            self._lines_printed = printed_count

    def close(self) -> None:
        """Cleans up visual canvas at the end of the batch run."""
        with self._lock:
            if self.is_tty and self._lines_printed > 0:
                safe_terminal_write(f"\033[{self._lines_printed}A")
                for _ in range(self._lines_printed):
                    safe_terminal_write("\r\033[K\n")
                safe_terminal_write(f"\033[{self._lines_printed}A")
                try:
                    sys.stdout.flush()
                except Exception:
                    pass
                self._lines_printed = 0


def print_batch_summary(
    total_items: int,
    succeeded_count: int,
    skipped_count: int,
    failed_count: int,
    total_bytes: int,
    elapsed_seconds: float,
    failed_items: Optional[List[Tuple[str, str]]] = None,
) -> None:
    """Renders a polished, high-visibility summary card after completing a download batch."""
    term_width = shutil.get_terminal_size((80, 24)).columns
    box_width = min(66, max(52, term_width - 4))

    # Calculate readable elapsed time
    mins, secs = divmod(int(elapsed_seconds), 60)
    time_str = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"

    # Average transfer speed
    if elapsed_seconds > 0 and total_bytes > 0:
        avg_speed_sec = total_bytes / elapsed_seconds
        speed_str = f" (avg {format_bytes(avg_speed_sec)}/s)"
    else:
        speed_str = ""

    def _box_line(left_text: str, right_text: str = "") -> str:
        clean_left = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', left_text)
        clean_right = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', right_text)
        extra_left = sum(1 for ch in clean_left if ord(ch) > 0x1F000 or ch in "🖼📥📦🎬📁🚀⚠️❌✅⏩•")
        extra_right = sum(1 for ch in clean_right if ord(ch) > 0x1F000 or ch in "🖼📥📦🎬📁🚀⚠️❌✅⏩•")
        vis_total = len(clean_left) + extra_left + len(clean_right) + extra_right
        pad = max(0, box_width - 4 - vis_total)
        return f"{Style.CYAN}║ {left_text}{' ' * pad}{right_text} ║{Style.RESET}"

    bar = "═" * (box_width - 2)
    div = "─" * (box_width - 2)

    print(f"\n{Style.CYAN}╔{bar}╗{Style.RESET}")
    title_text = f"{Style.BOLD}{Style.WHITE}BATCH DOWNLOAD SUMMARY{Style.RESET}"
    clean_title = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', title_text)
    pad_title = max(0, box_width - 2 - len(clean_title))
    pl = pad_title // 2
    pr = pad_title - pl
    print(f"{Style.CYAN}║{' ' * pl}{title_text}{' ' * pr}║{Style.RESET}")
    print(f"{Style.CYAN}╠{div}╣{Style.RESET}")

    print(_box_line(f"• Total Processed:", f"{Style.BOLD}{Style.WHITE}{total_items}{Style.RESET}"))
    print(_box_line(f"• Succeeded:", f"{Style.BOLD}{Style.GREEN}{succeeded_count} completed{Style.RESET}"))
    if skipped_count > 0:
        print(_box_line(f"• Skipped:", f"{Style.BOLD}{Style.YELLOW}{skipped_count} (already on disk){Style.RESET}"))
    if failed_count > 0:
        print(_box_line(f"• Failed / Errors:", f"{Style.BOLD}{Style.RED}{failed_count}{Style.RESET}"))
    print(_box_line(f"• Total Transferred:", f"{Style.BOLD}{Style.CYAN}{format_bytes(total_bytes)}{Style.RESET}"))
    print(_box_line(f"• Elapsed Time:", f"{Style.BOLD}{Style.WHITE}{time_str}{Style.dim(speed_str)}{Style.RESET}"))
    print(f"{Style.CYAN}╚{bar}╝{Style.RESET}\n")

    if failed_items:
        print(f"{Style.tag('❌', 'FAILED ITEMS', Style.RED)} The following links could not be downloaded:")
        for idx, (url, err) in enumerate(failed_items, 1):
            print(f"  {Style.dim(f'[{idx}]')} {Style.white(url)}")
            print(f"      {Style.red(f'Error: {err}')}")
        print(f"\n{Style.tag('💡', 'TIP', Style.CYAN)} You can retry all failed items anytime with: {Style.bold('python multi-dl.py queue retry')}\n")



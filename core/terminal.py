"""
Terminal formatting and color utilities for MULTI_DOWNLOADER.
Provides unified, consistent ANSI styling and aligned status tags across all CLI outputs.
"""

import os
import sys
from typing import Optional

# Enable ANSI virtual terminal processing on Windows cmd/powershell
if sys.platform == "win32":
    os.system("")


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


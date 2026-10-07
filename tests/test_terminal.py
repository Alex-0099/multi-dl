"""
Unit tests for terminal styling, coloring, and bracket alignment.
"""

from core.terminal import Style


def test_tag_alignment_single_space():
    # Emojis used across the CLI and backends
    tags = [
        ("🎯", "ROUTING", Style.CYAN),
        ("⚙️", "BACKEND", Style.MAGENTA),
        ("🚀", "DOWNLOADING", Style.BLUE),
        ("🌐", "METADATA", Style.CYAN),
        ("🔍", "EXTRACTOR", Style.CYAN),
        ("📡", "STREAMS", Style.BLUE),
        ("⚙️", "PO-TOKEN", Style.MAGENTA),
        ("🎯", "TARGET", Style.YELLOW),
        ("🎬", "MEDIA", Style.YELLOW),
        ("🎞️", "FORMAT", Style.CYAN),
        ("📥", "99.5%", Style.CYAN),
        ("✅", "COMPLETED", Style.GREEN),
        ("📁", "SAVED", Style.GREEN),
        ("💾", "ARCHIVE", Style.CYAN),
        ("⚠️", "WARNING", Style.YELLOW),
        ("❌", "ERROR", Style.RED),
    ]

    for emoji, text, color in tags:
        rendered = Style.tag(emoji, text, color)
        # Ensure single space after emoji and before opening bracket ANSI escape sequence
        # Emoji is stripped of trailing spaces, then followed by exactly 1 space and color code
        clean_emoji = emoji.strip()
        assert f"{clean_emoji} {color}" in rendered
        assert f"[{text}]" in rendered


def test_tag_strips_accidental_spaces():
    # Even if someone calls Style.tag with accidental spaces in emoji
    rendered1 = Style.tag("⚙️", "BACKEND", Style.MAGENTA)
    rendered2 = Style.tag("⚙️  ", "BACKEND", Style.MAGENTA)
    assert rendered1 == rendered2


def test_color_helpers():
    assert "\033[96m" in Style.cyan("test")
    assert "\033[95m" in Style.magenta("test")
    assert "\033[92m" in Style.green("test")
    assert "\033[93m" in Style.yellow("test")
    assert "\033[91m" in Style.red("test")
    assert "\033[0m" in Style.white("test")


def test_engine_badges_and_colors():
    assert Style.engine_color("yt-dlp") == Style.RED
    assert Style.engine_color("gallery-dl") == Style.YELLOW
    assert Style.engine_color("telegram-dl") == Style.BLUE
    assert Style.engine_color("cyberdrop-dl") == Style.MAGENTA
    assert Style.engine_color("unknown-engine") == Style.CYAN

    ytdlp_badge = Style.engine_badge("yt-dlp")
    assert Style.RED in ytdlp_badge
    assert "[yt-dlp]" in ytdlp_badge

    gdl_badge = Style.engine_badge("gallery-dl")
    assert Style.YELLOW in gdl_badge
    assert "[gallery-dl]" in gdl_badge


def test_progress_bar_stages():
    # 0%: all empty track
    bar_0 = Style.progress_bar(0.0, width=20)
    assert "░" * 20 in bar_0

    # 50%: 10 filled, 10 empty
    bar_50 = Style.progress_bar(50.0, width=20)
    assert "█" * 10 in bar_50
    assert "░" * 10 in bar_50

    # 100%: fully filled with green
    bar_100 = Style.progress_bar(100.0, width=20)
    assert "█" * 20 in bar_100
    assert Style.GREEN in bar_100

    # None: indeterminate empty track
    bar_none = Style.progress_bar(None, width=20)
    assert "░" * 20 in bar_none


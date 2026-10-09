# MULTI_DOWNLOADER

> A modern, unified multi-platform downloader orchestrating `yt-dlp`, `gallery-dl`, `terabox-dl`, `telegram-dl`, and `cyberdrop-dl` with isolated configurations, shared SQLite deduplication archive, persistent priority queue, two-way automatic failover, and interactive terminal interface.

---

## 📌 Versioning Scheme

* **Current Version:** `4.2.3`
* **Version Format:** `MAJOR.MINOR.PATCH`
  * `+1.0.0` : Full Downloader Implementation / New Backend Architecture
  * `+0.1.0` : Significant Feature Addition within an implementation
  * `+0.0.1` : Bug fix, performance polish, or patch

### Detailed Changelog:

#### ⚡ Version 4.x — `cyberdrop-dl` Engine Implementation
* **v4.2.3 (Bug Fix - Existing File Skip Detection & Reporting):**
  * Fixed misleading `[COMPLETED]` notifications when all files in a job were already present on disk and skipped by `gallery-dl`.
  * Added distinct `[SKIPPED]` / `[EXISTING]` status feedback and path resolution for existing files to eliminate confusion.
* **v4.2.2 (Bug Fix - CyberDrop Partial-Failure Recovery):**
  * Added validation ensuring partial album downloads or dropped network connections (e.g., GoFile / Bunkr drops) are never falsely committed to the SQLite archive database as successful completions.
* **v4.2.1 (Bug Fix - CyberDrop Directory Normalization):**
  * Implemented directory normalization (`_flatten_loose_files` and `_deduplicate_domain_folder`) matching `gallery-dl`'s single-level site hierarchy (`downloads/cyberdrop-dl/<domain>/<files>`).
  * Eliminated redundant domain-in-domain folder nesting (e.g. `gofile_io/gofile.io/...`).
* **v4.2.0 (Feature - Two-Way Failover & Locker Priority Routing):**
  * Configured default priority routing for common lockers (`bunkr`, `gofile`, `pixeldrain`, `catbox`, `coomer`, `kemono`, `erome`, `fapello`, `simpcity`) to `gallery-dl` for optimal single-file reliability.
  * Added bidirectional automatic failover: automatically falls back to `cyberdrop-dl` if `gallery-dl` encounters unsupported forum/locker variations, and vice versa.
* **v4.1.0 (Feature - Live Real-Time Byte & File Progress Tracker):**
  * Implemented live SQLite monitoring on `cyberdrop.db` and fallback HTTP `Content-Length` header probing to resolve file sizes and render dynamic progress bars, speeds, and ETA even on lockers hiding content lengths (`??`).
* **v4.0.0 (New Engine - `cyberdrop-dl` Integration):**
  * Added `CyberdropDlBackend` (`backends/cyberdrop_backend.py`) wrapping the `cyberdrop-dl` CLI in an isolated subprocess.
  * Dynamic runtime config sandbox (`configs/cyberdrop-dl.json`), dedicated lockfile isolation, and downloads redirection.
  * Extends platform coverage across hundreds of file lockers, forums, image hosts, and video hosts.

#### ✈️ Version 3.x — `telegram-dl` Engine Implementation
* **v3.2.1 (Bug Fix - Telethon Event Loop & FloodWait):**
  * Fixed Telethon async loop lifecycle handling and added graceful recovery for Telegram API `FloodWait` penalties.
* **v3.2.0 (Feature - Engine Auto-Updater):**
  * Integrated unified `EngineUpdater` (`core/updater.py`), accessible via `multi-dl -U` or `multi-dl update [engine]`, managing pip packages and git repositories.
* **v3.1.0 (Feature - Interactive Authentication Wizard):**
  * Added `multi-dl auth` / `multi-dl login` command providing guided CLI onboarding for Telegram API ID, API Hash, phone verification code, and 2FA password.
* **v3.0.0 (New Engine - `telegram-dl` Integration):**
  * Embedded native Telethon client (`backends/telegram_backend.py`) supporting downloading public and private channels, supergroups, direct messages, and chat media queues.
  * Session file persistence (`telegram_dl_session.session`) and selective media filtering.

#### 📦 Version 2.x — `terabox-dl` Engine Implementation
* **v2.1.1 (Bug Fix - TeraBox Session Cookie & Chunk Streams):**
  * Added `ndus` session cookie injection and stream connection retry handling.
* **v2.1.0 (Feature - Interactive Mode TUI):**
  * Added `multi-dl -i` / `multi-dl interactive` live loop mode for rapid multi-link pasting and queueing.
* **v2.0.0 (New Engine - `terabox-dl` Integration):**
  * Ported native TeraBox downloader (`backends/terabox_backend.py`) featuring JavaScript token scraping, dynamic direct link resolution, multi-threaded chunk downloading, and HTTP `Range` request resuming.

#### 🖼️ Version 1.x — `gallery-dl` Engine Implementation
* **v1.1.1 (Bug Fix - Path Resolution & Sandboxed Folders):**
  * Fixed relative path resolution and output directory normalization across galleries.
* **v1.1.0 (Feature - Dual-Line Album Progress Tracker):**
  * Implemented dual-line progress reporting with live file counter, album progress, and batch completion indicators.
* **v1.0.0 (New Engine - `gallery-dl` Integration):**
  * Embedded official `gallery-dl` Python backend (`backends/gallerydl_backend.py`) with in-memory configuration isolation (`config.clear()`).
  * Support for 300+ image hosting platforms, manga readers, and social galleries.

#### 🎬 Version 0.x — Core Architecture & `yt-dlp` Engine
* **v0.2.0 (Feature - Proof-of-Origin Token Sidecar):**
  * Implemented `core/pot_manager.py` with automatic lifecycle management for local Proof-of-Origin token provider sidecar (`127.0.0.1:4416`), enabling 1080p/4K YouTube downloads without account verification.
* **v0.1.6 (Bug Fix - Format Control & DASH/HLS High-Res Discovery):**
  * Added `--format` / `-f` CLI option and configured `extractor_args` with `missing_pot` to discover complete stream formats.
* **v0.1.5 (Bug Fix - JavaScript Challenge Solver):**
  * Integrated `remote_components: ["ejs:github"]` into `YtDlpBackend` for modern YouTube player signature decoding.
* **v0.1.4 (Feature - Browser Cookie Extraction):**
  * Added `--cookies-from-browser <browser>` and `--cookies <file>` flags to bypass age gates and access subscriber-only media.
* **v0.1.3 (Bug Fix / Polish):**
  * Clean newline separation, suppressed stdout progress noise with `noprogress: True`, and optimized extractor matching.
* **v0.1.2 (UX Polish):**
  * Enabled direct URL downloads without needing explicit `download` subcommand.
* **v0.1.1 (Bug Fix / Polish):**
  * Fixed merged final filename reporting (`.mp4`), relative path display, and tag formatting.
* **v0.1.0 (Feature):**
  * Added structured folder sorting hierarchy (`downloads/<downloader>/<host>/`).
* **v0.0.2 (Bug Fix):**
  * Upgraded `yt-dlp` to fix plugin logger mismatch; resolved Windows console Unicode encoding.
* **v0.0.1 (Baseline Architecture):**
  * Initial architecture: unified models (`DownloadTask`, `QueueItem`, `ArchiveEntry`), isolated `ConfigManager`, SQLite archive database (`core/archive.py`), persistent queue (`core/queue_manager.py`), 3-tier URL router (`core/router.py`), and `yt-dlp` Python backend.

---

## 🚀 Supported Engines

| Engine | Primary Media & Domains | Integration Method | Key Features |
| :--- | :--- | :--- | :--- |
| **`yt-dlp`** | YouTube, Twitch, TikTok, Twitter/X, Reddit, Vimeo, SoundCloud, 1000+ streaming sites | Embedded Python API | PO Token sidecar, EJS challenge solver, browser cookies, custom formats (`-f`), auto ffmpeg |
| **`gallery-dl`** | Image boards, manga, galleries, social media, common lockers (Imgur, Pixiv, Danbooru, Reddit, Bunkr, GoFile, Pixeldrain, Catbox, Coomer, Kemono, Erome) | Embedded Python Library | In-memory sandbox (`config.clear()`), batch counter, dual-line progress display |
| **`terabox-dl`** | TeraBox cloud links (`terabox.com`, `1024tera.com`, `teraboxapp.com`, etc.) | Native Python Port | JS token scraper, direct link extractor, multi-threaded chunk streaming, HTTP `Range` resume, `ndus` cookie |
| **`telegram-dl`** | Public/private Telegram channels, groups, direct chats (`t.me/...`) | Native Telethon Library | Interactive auth setup wizard (`multi-dl auth`), session persistence, media type filtering |
| **`cyberdrop-dl`** | File lockers, forums, album hosts, dump sites (Cyberdrop, Bunkr, GoFile, Pixeldrain, forums, etc.) | Subprocess CLI Wrapper | Live SQLite progress bar & HTTP size probing, dynamic config sandbox, locker crawler |

---

## 🔄 Smart Routing & Automatic Failover

`MULTI_DOWNLOADER` uses a **3-tier intelligent routing pipeline**:

```
                       ┌─────────────────────────┐
                       │       Incoming URL      │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │  Tier 1: Domain Mapping │
                       └────────────┬────────────┘
                                    │
                ┌───────────────────┴───────────────────┐
                ▼                                       ▼
    Common File Lockers                         Streaming / Specialized
  (Bunkr, GoFile, Pixeldrain,                  (YouTube, TikTok, TeraBox,
   Catbox, Coomer, Kemono...)                         Telegram...)
                │                                       │
                ▼                                       ▼
    ┌───────────────────────┐               ┌───────────────────────┐
    │  Primary: gallery-dl  │               │ Assigned Engine Direct│
    └───────────┬───────────┘               └───────────────────────┘
                │
                ├───────► Success! Download Complete.
                │
          Scraper Failed?
                │
                ▼
    ┌───────────────────────┐
    │ Failover: cyberdrop-dl│
    └───────────────────────┘
```

1. **Tier 1 — Domain Matching:** Fast Regex map routes URLs to their optimal engine.
2. **Tier 2 — Locker Prioritization:** Common file lockers default to `gallery-dl` to prevent skipped files and guarantee clean single-file placement.
3. **Tier 3 — Bidirectional Automatic Failover:** If `gallery-dl` fails (e.g. forum thread or unsupported album type), the task seamlessly falls back to `cyberdrop-dl`. If `cyberdrop-dl` fails, it falls back to `gallery-dl`.

---

## 💻 CLI Usage

### 1. Direct Downloads
Simply pass the URL directly:
```powershell
python multi-dl.py "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
python multi-dl.py "https://pixeldrain.com/u/kbAnWg3d"
python multi-dl.py "https://terabox.com/s/1abcdef..."
python multi-dl.py "https://t.me/channel_name/123"
```

### 2. Interactive Mode (TUI)
Launch an interactive session to rapidly paste multiple links:
```powershell
python multi-dl.py -i
# or
python multi-dl.py interactive
```

### 3. Engine Authentication Wizard
Configure credentials for Telegram (API ID, Hash, 2FA) or TeraBox (`ndus` cookie):
```powershell
python multi-dl.py auth
# or for a specific engine:
python multi-dl.py auth telegram
python multi-dl.py auth terabox
```

### 4. Engine Auto-Updater
Check for and apply updates across all download engines:
```powershell
# Update everything:
python multi-dl.py -U
# or
python multi-dl.py update

# Update a specific engine:
python multi-dl.py update yt-dlp
python multi-dl.py update gallery-dl
python multi-dl.py update cyberdrop-dl
```

### 5. Force a Specific Backend
Override automatic routing and force an engine:
```powershell
python multi-dl.py "https://example.com/media" --backend gallery-dl
python multi-dl.py "https://example.com/media" --backend cyberdrop-dl
```

### 6. Authentication & Cookies (Age-Gated / Member Media)
```powershell
# Extract session cookies directly from your web browser:
python multi-dl.py "https://www.youtube.com/watch?v=..." --cookies-from-browser chrome
python multi-dl.py "https://www.youtube.com/watch?v=..." --cookies-from-browser firefox

# Or pass a Netscape cookies.txt file:
python multi-dl.py "https://..." --cookies "cookies.txt"

# Provide TeraBox session cookie:
python multi-dl.py "https://terabox.com/s/..." --ndus "YOUR_NDUS_COOKIE"
```

### 7. Custom Formats & Quality
```powershell
python multi-dl.py "https://www.youtube.com/watch?v=..." -f "bestvideo*+bestaudio/best"
```

### 8. Inspect SQLite Download Archive
```powershell
# View archive metrics and statistics:
python multi-dl.py archive --stats

# Search download history by keyword:
python multi-dl.py archive --search "search_term"
```

### 9. Test URL Routing
```powershell
python multi-dl.py route "https://bunkr.cr/f/mgUxSR79ZiNOD"
```

### 10. Check Version
```powershell
python multi-dl.py -v
# or
python multi-dl.py --version
```

---

## ⚙️ Configuration Reference (`config.json`)

All engines and core systems are configured centrally via `config.json` without modifying global system configurations:

```json
{
    "version": "4.2.2",
    "general": {
        "download_dir": "downloads",
        "concurrent_downloads": 3,
        "organize_by_backend": true,
        "organize_by_site": true,
        "auto_detect_backend": true,
        "retry_failed_attempts": 3
    },
    "archive": {
        "enabled": true,
        "db_path": "data/archive.db",
        "dedup_by_url": true,
        "dedup_by_hash": false
    },
    "yt_dlp": {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "embed_metadata": true,
        "embed_thumbnail": false,
        "write_subtitles": false,
        "ffmpeg_location": null,
        "cookies_from_browser": null,
        "fallback_browser_cookies": "firefox",
        "auto_start_po_provider": true,
        "po_provider_host": "127.0.0.1",
        "po_provider_port": 4416,
        "extractor_args": {
            "youtube": {
                "formats": ["missing_pot"]
            }
        }
    },
    "gallery_dl": {
        "config_file": "configs/gallery-dl.json"
    },
    "cyberdrop_dl": {
        "max_simultaneous": 5,
        "use_domain_subfolders": true,
        "config_file": "configs/cyberdrop-dl.json",
        "executable_path": "cyberdrop-dl"
    },
    "telegram_dl": {
        "api_id": "",
        "api_hash": "",
        "session_name": "telegram_dl_session",
        "media_types": ["photo", "video", "document"],
        "min_file_size_mb": 0,
        "max_file_size_mb": 0,
        "organize_by_chat": true,
        "organize_by_type": true
    },
    "terabox_dl": {
        "ndus_cookie": "",
        "chunk_size_mb": 4,
        "max_threads": 4,
        "auto_unpack": false
    }
}
```

---

## 📁 Project Architecture

```
MULTI_DOWNLOADER/
├── backends/
│   ├── base.py                   # Base backend abstract interface
│   ├── cyberdrop_backend.py      # cyberdrop-dl subprocess runner & live SQLite hook
│   ├── gallerydl_backend.py      # gallery-dl Python library integration
│   ├── telegram_backend.py       # Telethon client & channel/chat scraper
│   ├── terabox_backend.py        # TeraBox token scraper & chunk downloader
│   └── ytdlp_backend.py          # yt-dlp Python API adapter
├── core/
│   ├── archive.py                # Unified SQLite deduplication database
│   ├── config.py                 # Isolated configuration manager
│   ├── exceptions.py             # Domain-specific error hierarchies
│   ├── models.py                 # Dataclasses (DownloadTask, ArchiveEntry, etc.)
│   ├── pot_manager.py            # YouTube PO Token server sidecar manager
│   ├── queue_manager.py          # Persistent task queue
│   ├── router.py                 # 3-tier URL router & failover coordinator
│   ├── terminal.py               # Styled ANSI terminal output & progress bars
│   └── updater.py                # Multi-engine pip & git updater
├── configs/                      # Sandboxed backend configuration files
├── data/                         # SQLite archive and queue files
├── downloads/                    # Output root: downloads/<backend>/<site>/<file>
├── tests/                        # Comprehensive test suite (58 tests)
├── multi-dl.py                   # Main CLI entry point
├── config.json                   # Master configuration
└── requirements.txt              # Python dependencies
```

---

## 🧪 Testing & Verification

Run the automated test suite with pytest:

```powershell
.\.venv\Scripts\pytest -v
```

All 58 test cases covering routing, all 5 engine adapters, configuration isolation, updater, and terminal UI pass.

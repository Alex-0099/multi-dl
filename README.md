# MULTI_DOWNLOADER

> A modern, unified multi-platform downloader orchestrating `yt-dlp`, `gallery-dl`, `telegram-dl`, and `cyberdrop-dl` with a single config, shared SQLite archive, queue system, and cross-platform GUI.

---

## 📌 Versioning Scheme

* **Current Version:** `0.2.0`
* **Version Format:** `MAJOR.MINOR.PATCH`
  * `+1.0.0` : Full Downloader Implementation / New Backend Architecture
  * `+0.1.0` : Significant Feature Addition within an implementation
  * `+0.0.1` : Bug fix, performance polish, or patch

### Changelog:
* **v0.2.0 (New Feature - Auto-Managed PO Token Sidecar):**
  * Implemented `core/pot_manager.py` with seamless automatic sidecar lifecycle management (`POTManager`).
  * When a YouTube URL is processed, `MULTI_DOWNLOADER` automatically detects whether the local Proof-of-Origin token server on `127.0.0.1:4416` is active, and silently spawns it as a background sidecar if not running.
  * Allows downloading age-restricted YouTube videos in full resolution (1080p, 1440p, 4K) without uploading government ID, credit card, or face scans to Google.
  * Added configuration options: `auto_start_po_provider`, `po_provider_host`, and `po_provider_port`.
* **v0.1.6 (Bug Fix - Format Control & High-Res/PO Token Handling):**
  * Added `--format` / `-f` CLI option to allow custom format selection (e.g. `python multi-dl.py <URL> -f "bestvideo*+bestaudio/best"`).
  * Configured `extractor_args` with `"youtube": {"formats": ["missing_pot"]}` in `config.json` and backend to ensure high-resolution DASH/HLS formats are discovered.
  * Explained YouTube's account age-gate restriction: YouTube serves 360p (itag 18) fallback on web sessions unless account age is verified with ID or a GVS Proof of Origin (PO) token is provided.
* **v0.1.5 (Bug Fix - Age Verification & JS Challenges):**
  * Integrated `"remote_components": ["ejs:github"]` into `YtDlpBackend` to automatically download and run YouTube's JavaScript challenge solver (EJS) for modern signature decoding.
  * Verified successful download of strict age-restricted videos using browser session cookies (`firefox`).
* **v0.1.4 (Feature - Authentication / Age-Gated Media):**
  * Added `--cookies-from-browser <browser>` flag (Chrome, Firefox, Edge, Brave, Opera, Vivaldi) to bypass age-restrictions and sign-in gates.
  * Added `--cookies <path/to/cookies.txt>` flag to pass exported Netscape cookie files.
  * Added permanent `cookies_from_browser` and `cookies_file` settings in `config.json`.
* **v0.1.3 (Bug Fix / Polish):**
  * Renamed CLI entry file to `multi-dl.py`.
  * Added clean newline separation before `[ROUTING]`.
  * Added `noprogress: True` to yt-dlp backend settings to suppress internal stdout progress fragments.
  * Optimized extractor matching using `gen_extractor_classes()`.
* **v0.1.2 (UX Polish):** Enabled direct URL downloads without needing `download` argument (`python multi-dl.py <URL>`).
* **v0.1.1 (Bug Fix / Polish):** Fixed final merged filename reporting (`.mp4`), relative path display, and `[TAG]` feedback.
* **v0.1.0 (New Feature):** Added nested folder sorting hierarchy (`downloads/<downloader>/<host>/`).
* **v0.0.2 (Bug Fix):** Upgraded `yt-dlp` to fix plugin logger mismatch; resolved Windows console unicode encoding.
* **v0.0.1 (Baseline):** Initial architecture with `yt-dlp` Python backend, SQLite archive, queue, and URL router.

---

## 💻 CLI Usage

### 1. Download Media Directly
```powershell
python multi-dl.py "https://www.youtube.com/shorts/9nTBHwY1C1I"
```

### 2. Download Age-Restricted / Member Videos (Cookies)
```powershell
# Extract session directly from your browser:
python multi-dl.py "https://www.youtube.com/watch?v=YaFBI-4ol20" --cookies-from-browser chrome
# Or using Firefox, Edge, Brave, etc.:
python multi-dl.py "https://www.youtube.com/watch?v=YaFBI-4ol20" --cookies-from-browser firefox
```

### 3. Using a Cookies File
```powershell
python multi-dl.py "https://..." --cookies "cookies.txt"
```

### 3. Test URL Routing
```powershell
python multi-dl.py route "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### 4. Inspect Download Archive
```powershell
python multi-dl.py archive --stats
python multi-dl.py archive --search "keyword"
```

---

## 🚀 Implemented Components (v0.0.1)

### 1. Engine Core
* **Data Models (`core/models.py`)**: `DownloadTask`, `QueueItem`, `ArchiveEntry`, and `DownloadProgress`.
* **Isolated Configuration (`core/config.py` & `config.json`)**: Config manager providing typed access with zero bleed into global system configs.
* **Unified SQLite Archive (`core/archive.py`)**: Tracks downloads across all engines with URL and SHA-256 hash deduplication.
* **Queue System (`core/queue_manager.py`)**: Persistent priority queue (`data/queue.json`).
* **3-Tier URL Router (`core/router.py`)**: Fast domain lookup with fallback deep extraction probes.

### 2. Backend Adapters
* **`yt-dlp` Backend (`backends/ytdlp_backend.py`)**:
  * Embedded via official Python API.
  * Isolated configuration using `ignoreconfig=True` (does not read `%APPDATA%/yt-dlp/config`).
  * Auto-locates `ffmpeg.exe` (detects `~/yt-dlp/ffmpeg.exe` or system PATH).
  * Real-time progress callback hooks.
  * Metadata embedding and thumbnail handling.
* **`gallery-dl` Backend (`backends/gallerydl_backend.py`)**:
  * Embedded via Python library.
  * In-memory config sandbox using `config.clear()`.
* **`telegram-dl` Backend (`backends/telegram_backend.py`)**:
  * Native Telethon implementation ported directly into the codebase.

---

## 🛠️ Installation & Setup

1. **Clone or navigate to project directory:**
   ```powershell
   git clone <repo-url>
   cd MULTI_DOWNLOADER
   ```

2. **Activate the virtual environment:**
   ```powershell
   .\.venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

---

## 💻 CLI Usage

### 1. Direct Download
```powershell
python multi-dl.py "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
```

### 2. Force Specific Backend
```powershell
python multi-dl.py "https://example.com/media" --backend yt-dlp
```

---

## ⚙️ Configuration (`config.json`)

Key configuration sections available:

```json
{
    "version": "1.0.0",
    "general": {
        "download_dir": "downloads",
        "concurrent_downloads": 3,
        "organize_by_backend": true
    },
    "yt_dlp": {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "embed_metadata": true,
        "ffmpeg_location": null
    }
}
```

---

## 🧪 Testing

Run automated tests using pytest:
```powershell
.\.venv\Scripts\pytest -v tests/test_ytdlp.py
```

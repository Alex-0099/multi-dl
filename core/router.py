"""
URL Router for MULTI_DOWNLOADER.
Directs incoming URLs to the appropriate backend using a 3-tier matching engine.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backends.base import BaseBackend
from core.exceptions import UnsupportedURLError


class URLRouter:
    """
    3-Tier URL routing engine:
    1. Fast domain dictionary match (covers 90%+ cases)
    2. Deep backend can_handle() check
    3. Fallback priority chain
    """

    # Tier 1 lookup table mapping domains to backend names
    DOMAIN_MAP: Dict[str, str] = {
        # yt-dlp
        "youtube.com": "yt-dlp",
        "www.youtube.com": "yt-dlp",
        "m.youtube.com": "yt-dlp",
        "youtu.be": "yt-dlp",
        "twitch.tv": "yt-dlp",
        "www.twitch.tv": "yt-dlp",
        "tiktok.com": "yt-dlp",
        "www.tiktok.com": "yt-dlp",
        "vimeo.com": "yt-dlp",
        "soundcloud.com": "yt-dlp",
        "dailymotion.com": "yt-dlp",

        # gallery-dl
        "twitter.com": "gallery-dl",
        "www.twitter.com": "gallery-dl",
        "x.com": "gallery-dl",
        "www.x.com": "gallery-dl",
        "reddit.com": "gallery-dl",
        "www.reddit.com": "gallery-dl",
        "pixiv.net": "gallery-dl",
        "www.pixiv.net": "gallery-dl",
        "instagram.com": "gallery-dl",
        "www.instagram.com": "gallery-dl",
        "imgur.com": "gallery-dl",
        "danbooru.donmai.us": "gallery-dl",
        "deviantart.com": "gallery-dl",
        "nhentai.net": "gallery-dl",
        "www.nhentai.net": "gallery-dl",

        # Common lockers where gallery-dl extractors are faster, more reliable, and do not drop files
        "bunkr.si": "gallery-dl",
        "bunkr.is": "gallery-dl",
        "bunkr.cr": "gallery-dl",
        "bunkr.black": "gallery-dl",
        "bunkr.site": "gallery-dl",
        "bunkr.ws": "gallery-dl",
        "bunkr.ac": "gallery-dl",
        "gofile.io": "gallery-dl",
        "pixeldrain.com": "gallery-dl",
        "coomer.su": "gallery-dl",
        "coomer.party": "gallery-dl",
        "kemono.su": "gallery-dl",
        "kemono.party": "gallery-dl",
        "catbox.moe": "gallery-dl",
        "files.catbox.moe": "gallery-dl",
        "erome.com": "gallery-dl",
        "fapello.com": "gallery-dl",
        "cyberdrop.me": "gallery-dl",
        "simpcity.su": "gallery-dl",
        "simpcity.to": "gallery-dl",

        # cyberdrop-dl (Primary for deep forum threads and hosts without gallery-dl support)
        "saint.to": "cyberdrop-dl",
        "f95zone.to": "cyberdrop-dl",
        "vipergirls.to": "cyberdrop-dl",
        "mega.nz": "cyberdrop-dl",
        "sendvid.com": "cyberdrop-dl",
        "streamtape.com": "cyberdrop-dl",
        "puter.com": "cyberdrop-dl",
        "jpg.church": "cyberdrop-dl",

        # telegram-dl
        "t.me": "telegram-dl",
        "telegram.me": "telegram-dl",

        # terabox-dl
        "terabox.com": "terabox-dl",
        "www.terabox.com": "terabox-dl",
        "1024tera.com": "terabox-dl",
        "www.1024tera.com": "terabox-dl",
        "teraboxlink.com": "terabox-dl",
        "www.teraboxlink.com": "terabox-dl",
        "freeterabox.com": "terabox-dl",
        "www.freeterabox.com": "terabox-dl",
        "mirrobox.com": "terabox-dl",
        "www.mirrobox.com": "terabox-dl",
        "nephobox.com": "terabox-dl",
        "www.nephobox.com": "terabox-dl",
        "4funbox.com": "terabox-dl",
        "www.4funbox.com": "terabox-dl",
        "teraboxapp.com": "terabox-dl",
        "www.teraboxapp.com": "terabox-dl",
    }

    def __init__(self, backends: Optional[Dict[str, BaseBackend]] = None):
        self.backends: Dict[str, BaseBackend] = backends or {}

    def register_backend(self, backend: BaseBackend) -> None:
        """Register a backend instance with the router."""
        self.backends[backend.name] = backend

    def route(self, url: str, backend_override: Optional[str] = None) -> BaseBackend:
        """
        Identify and return the proper backend instance for the provided URL.
        """
        # Manual user override
        if backend_override:
            clean_name = backend_override.lower().replace("_", "-")
            if clean_name in self.backends:
                return self.backends[clean_name]
            raise UnsupportedURLError(f"Specified backend '{backend_override}' is not registered.")

        # Tier 1: Fast domain lookup
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().split(":")[0]  # strip port if present
        
        target_name = self.DOMAIN_MAP.get(domain)
        if target_name and target_name in self.backends:
            return self.backends[target_name]

        # Tier 2: Query each registered backend's can_handle()
        for name, backend in self.backends.items():
            try:
                if backend.can_handle(url):
                    return backend
            except Exception:
                continue

        # Tier 3: Fallback priority (gallery-dl then yt-dlp)
        for fallback_name in ["gallery-dl", "yt-dlp"]:
            if fallback_name in self.backends:
                return self.backends[fallback_name]

        raise UnsupportedURLError(f"No backend available to handle URL: {url}")

    @classmethod
    def detect_backend_name(cls, url: str, backend_override: Optional[str] = None) -> str:
        """
        Lightweight, fast detection of backend engine name without instantiating heavy backends.
        Uses Tier 1 DOMAIN_MAP and fast substring patterns.
        """
        if backend_override:
            return backend_override.lower().replace("_", "-")

        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().split(":")[0]

        # Tier 1 direct match
        if domain in cls.DOMAIN_MAP:
            return cls.DOMAIN_MAP[domain]

        # Domain substring matching
        for d, b in cls.DOMAIN_MAP.items():
            if d in domain:
                return b

        # Telegram detection
        if "t.me" in domain or "telegram.me" in domain or url.startswith("t.me/"):
            return "telegram-dl"

        # TeraBox mirror detection
        if any(t in domain for t in ("terabox", "1024tera", "teraboxlink", "freeterabox", "mirrobox", "nephobox", "4funbox")):
            return "terabox-dl"

        # Image/gallery heuristic vs video/audio
        if any(img in domain for img in ("image", "photo", "pic", "gallery", "album", "danbooru", "gelbooru")):
            return "gallery-dl"

        return "yt-dlp"

    @classmethod
    def create_default(cls, config: Optional[Any] = None) -> "URLRouter":
        """Instantiates all 5 production backends with configured settings and returns a fully initialized router."""
        from backends.cyberdrop_backend import CyberdropDlBackend
        from backends.gallerydl_backend import GalleryDlBackend
        from backends.telegram_backend import TelegramDlBackend
        from backends.terabox_backend import TeraboxDlBackend
        from backends.ytdlp_backend import YtDlpBackend

        if config is None:
            from core.config import ConfigManager
            config = ConfigManager()

        return cls({
            "yt-dlp": YtDlpBackend(config.get_backend_config("yt-dlp")),
            "gallery-dl": GalleryDlBackend(config.get_backend_config("gallery-dl")),
            "telegram-dl": TelegramDlBackend(config.get_backend_config("telegram-dl")),
            "terabox-dl": TeraboxDlBackend(config.get_backend_config("terabox-dl")),
            "cyberdrop-dl": CyberdropDlBackend(config.get_backend_config("cyberdrop-dl")),
        })

    @staticmethod
    def get_host_identifier(url: str, backend_name: str) -> str:
        """
        Derives a clean host/site or channel folder name for organizing downloads.
        Examples:
          - youtube.com/shorts/... -> 'youtube'
          - twitter.com/... -> 'twitter'
          - t.me/channel_name/123 -> 'channel_name'
          - t.me/c/12345/678 -> 'chat_12345'
        """
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().split(":")[0]

        # Telegram specific extraction (channel/chat name)
        if "telegram" in backend_name or "t.me" in domain:
            import re
            m = re.search(r'(?:https?://)?(?:t|telegram)\.me/(c/)?([a-zA-Z0-9_.-]+)', url)
            if m:
                is_private = bool(m.group(1))
                name = m.group(2)
                return f"chat_{name}" if is_private else name
            return "telegram"

        # YouTube specific normalization (youtube.com, youtu.be -> youtube)
        if "youtube" in domain or "youtu.be" in domain:
            return "youtube"

        # TeraBox specific normalization
        if any(d in domain for d in ("terabox", "1024tera", "mirrobox", "nephobox", "4funbox")):
            return "terabox"

        # Locker & community host normalization
        if "bunkr" in domain:
            return "bunkr"
        if "catbox" in domain:
            return "catbox"
        if "coomer" in domain:
            return "coomer"
        if "kemono" in domain:
            return "kemono"


        # General domain simplification (e.g. www.twitter.com -> twitter)
        parts = domain.split(".")
        if len(parts) >= 2:
            # Drop www / m subdomains
            if parts[0] in ("www", "m"):
                parts = parts[1:]
            # Return primary domain name (e.g. twitter, instagram, pixiv)
            return parts[0] if parts else domain

        return domain or "misc"

"""
Configuration manager for MULTI_DOWNLOADER.
Handles loading, defaults fallback, deep-merge migrations, saving,
dot-notation access, type coercion, and per-backend isolation.
"""

import copy
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Union

from core.exceptions import ConfigError


def coerce_value(val: Any) -> Any:
    """
    Intelligently coerces string representation of settings into typed Python values.
    Supports booleans, integers, floats, nulls, JSON arrays/objects, or clean strings.
    """
    if not isinstance(val, str):
        return val

    cleaned = val.strip()
    lower = cleaned.lower()

    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    if lower in ("null", "none", "nil"):
        return None

    # Try integer
    try:
        return int(cleaned)
    except ValueError:
        pass

    # Try float
    try:
        return float(cleaned)
    except ValueError:
        pass

    # Try JSON (for arrays or dicts like ["photo", "video"])
    if (cleaned.startswith("[") and cleaned.endswith("]")) or (cleaned.startswith("{") and cleaned.endswith("}")):
        try:
            return json.loads(cleaned)
        except Exception:
            pass

    # Return string without surrounding quotes if any
    if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
        return cleaned[1:-1]

    return cleaned


def _deep_merge(target: Dict[str, Any], default: Dict[str, Any]) -> bool:
    """
    Recursively merges missing keys from default into target dictionary.
    Returns True if target was modified, False otherwise.
    Never overwrites existing values in target.
    """
    modified = False
    for k, v in default.items():
        if k not in target:
            target[k] = copy.deepcopy(v)
            modified = True
        elif isinstance(v, dict) and isinstance(target.get(k), dict):
            if _deep_merge(target[k], v):
                modified = True
    return modified


class ConfigManager:
    """Manages application-wide configuration with schema validation and per-backend isolation."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

    DEFAULT_SCHEMA: Dict[str, Any] = {
        "version": "4.6.0",
        "general": {
            "download_dir": "downloads",
            "concurrent_downloads": 3,
            "organize_by_backend": True,
            "organize_by_site": True,
            "auto_detect_backend": True,
            "retry_failed_attempts": 3,
            "notifications": True,
            "play_sound": True,
        },
        "network": {
            "proxy": None,
            "rate_limit": None,
            "timeout": 30,
            "user_agent": None,
        },
        "engine_limits": {
            "yt-dlp": 2,
            "telegram-dl": 1,
            "gallery-dl": 4,
            "terabox-dl": 2,
            "cyberdrop-dl": 2,
        },
        "domain_limits": {
            "max_per_domain": 1,
            "enabled": True,
        },
        "archive": {
            "enabled": True,
            "db_path": "data/archive.db",
            "dedup_by_url": True,
            "dedup_by_hash": False,
            "verify_file_exists": True,
        },
        "yt_dlp": {
            "format": "bestvideo*+bestaudio/best",
            "preferred_resolution": "best",
            "merge_output_format": "mp4",
            "impersonate": None,
            "cookies_from_browser": None,
            "fallback_browser_cookies": "firefox",
            "rate_limit_bytes": None,
            "concurrent_fragments": 1,
            "embed_metadata": True,
            "embed_thumbnail": False,
            "write_subtitles": False,
            "subtitles_langs": ["en"],
            "sponsorblock": False,
            "auto_start_po_provider": True,
            "po_provider_host": "127.0.0.1",
            "po_provider_port": 4416,
            "config_file": "configs/yt-dlp.conf",
        },
        "gallery_dl": {
            "cookies_from_browser": None,
            "fallback_browser_cookies": "firefox",
            "sleep_request": 0.0,
            "retries": 3,
            "config_file": "configs/gallery-dl.json",
        },
        "telegram_dl": {
            "api_id": "",
            "api_hash": "",
            "session_name": "telegram_dl_session",
            "github_repo": "https://github.com/Alex-0099/Telegram-dl.git",
            "media_types": ["photo", "video", "document", "audio"],
            "min_file_size_mb": 0,
            "max_file_size_mb": 0,
            "organize_by_chat": True,
            "organize_by_type": True,
            "flood_sleep_threshold": 60,
            "config_file": "configs/telegram-dl.json",
        },
        "terabox_dl": {
            "github_repo": "https://github.com/Alex-0099/Terabox-DL.git",
            "ndus_cookie": "",
            "js_token": "",
            "chunk_size_mb": 4,
            "max_threads": 4,
            "auto_unpack": False,
            "config_file": "configs/terabox-dl.json",
        },
        "cyberdrop_dl": {
            "executable_path": "cyberdrop-dl",
            "config_file": "configs/cyberdrop-dl.json",
            "max_simultaneous": 5,
            "use_domain_subfolders": True,
            "include_audio": True,
            "include_video": True,
            "include_images": True,
        },
    }

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._load_env()
        self._init_example_configs()
        self.load()

    def _load_env(self) -> None:
        """Loads environment variables from project root .env if present."""
        env_file = self.PROJECT_ROOT / ".env"
        if env_file.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file)
            except Exception:
                pass

    def _init_example_configs(self) -> None:
        """Copies configs/*.example.json to configs/*.json on fresh installs if missing."""
        configs_dir = self.PROJECT_ROOT / "configs"
        if not configs_dir.exists():
            return
        for example_file in configs_dir.glob("*.example.json"):
            target_name = example_file.name.replace(".example.json", ".json")
            target_path = configs_dir / target_name
            if not target_path.exists():
                try:
                    shutil.copy2(example_file, target_path)
                except Exception:
                    pass

    def load(self) -> None:
        """
        Load configuration from JSON file or initialize with defaults.
        Performs deep-merge migration so existing user configurations
        receive new schema keys automatically without losing customizations.
        """
        if not self.config_path.exists():
            self._data = copy.deepcopy(self.DEFAULT_SCHEMA)
            self.save()
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if not isinstance(loaded, dict):
                    loaded = {}
                self._data = loaded
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in config file: {e}")

        # Deep-merge defaults for any missing keys
        schema_defaults = copy.deepcopy(self.DEFAULT_SCHEMA)
        modified = _deep_merge(self._data, schema_defaults)

        # Update version if schema is newer
        if self._data.get("version") != self.DEFAULT_SCHEMA["version"]:
            self._data["version"] = self.DEFAULT_SCHEMA["version"]
            modified = True

        if modified:
            self.save()

    def save(self) -> None:
        """Persist current configuration to disk."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4)
        except OSError as e:
            raise ConfigError(f"Failed to write config file: {e}")

    def reset_defaults(self) -> None:
        """Resets configuration back to pristine factory defaults."""
        self._data = copy.deepcopy(self.DEFAULT_SCHEMA)
        self.save()
        self._init_example_configs()

    def get(self, section: str, key: Optional[str] = None, default: Any = None) -> Any:
        """Retrieve a specific section or key."""
        if section in self._data and not isinstance(self._data[section], dict):
            return self._data[section]
        sec_data = self._data.get(section, {})
        if not isinstance(sec_data, dict):
            return default if key is not None else sec_data
        if key is None:
            return sec_data
        return sec_data.get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a configuration option in memory and persist it."""
        if section not in self._data or not isinstance(self._data[section], dict):
            self._data[section] = {}
        self._data[section][key] = value
        self.save()

    FLAT_KEY_ALIASES: Dict[str, str] = {
        "max_per_domain": "domain_limits.max_per_domain",
        "max-per-domain": "domain_limits.max_per_domain",
        "domain_guard": "domain_limits.enabled",
        "domain-guard": "domain_limits.enabled",
        "concurrent_downloads": "general.concurrent_downloads",
        "concurrent-downloads": "general.concurrent_downloads",
        "download_dir": "general.download_dir",
        "download-dir": "general.download_dir",
        "proxy": "network.proxy",
        "rate_limit": "network.rate_limit",
        "rate-limit": "network.rate_limit",
        "timeout": "network.timeout",
        "user_agent": "network.user_agent",
        "user-agent": "network.user_agent",
        "notifications": "general.notifications",
        "play_sound": "general.play_sound",
        "play-sound": "general.play_sound",
        "retry_failed_attempts": "general.retry_failed_attempts",
        "retry-failed-attempts": "general.retry_failed_attempts",
        "organize_by_backend": "general.organize_by_backend",
        "organize-by-backend": "general.organize_by_backend",
        "organize_by_site": "general.organize_by_site",
        "organize-by-site": "general.organize_by_site",
        "auto_detect_backend": "general.auto_detect_backend",
        "auto-detect-backend": "general.auto_detect_backend",
        "yt-dlp": "engine_limits.yt-dlp",
        "yt_dlp": "engine_limits.yt-dlp",
        "gallery-dl": "engine_limits.gallery-dl",
        "gallery_dl": "engine_limits.gallery-dl",
        "telegram-dl": "engine_limits.telegram-dl",
        "telegram_dl": "engine_limits.telegram-dl",
        "terabox-dl": "engine_limits.terabox-dl",
        "terabox_dl": "engine_limits.terabox-dl",
        "cyberdrop-dl": "engine_limits.cyberdrop-dl",
        "cyberdrop_dl": "engine_limits.cyberdrop-dl",
        "archive_enabled": "archive.enabled",
        "archive-enabled": "archive.enabled",
        "db_path": "archive.db_path",
        "db-path": "archive.db_path",
        "dedup_by_url": "archive.dedup_by_url",
        "dedup-by-url": "archive.dedup_by_url",
        "dedup_by_hash": "archive.dedup_by_hash",
        "dedup-by-hash": "archive.dedup_by_hash",
        "verify_file_exists": "archive.verify_file_exists",
        "verify-file-exists": "archive.verify_file_exists",
    }

    def resolve_key_path(self, key_or_path: str) -> Optional[str]:
        """
        Resolves a user-provided setting key (either flat alias or dot-path)
        to its canonical dot-notation path in the configuration schema.
        Returns None if key cannot be resolved.
        """
        cleaned = key_or_path.strip()
        if not cleaned:
            return None

        # 1. Direct alias match
        norm_key = cleaned.lower()
        if norm_key in self.FLAT_KEY_ALIASES:
            return self.FLAT_KEY_ALIASES[norm_key]
        alt_norm = norm_key.replace("-", "_")
        if alt_norm in self.FLAT_KEY_ALIASES:
            return self.FLAT_KEY_ALIASES[alt_norm]

        # 2. Dot-notation path normalization
        if "." in cleaned:
            parts = cleaned.split(".", 1)
            sec, key = parts[0], parts[1]
            sec_norm = sec.lower().replace("-", "_")
            sec_hyphen = sec.lower().replace("_", "-")

            matched_sec = None
            for s in self.DEFAULT_SCHEMA.keys():
                if s.lower() in (sec_norm, sec_hyphen):
                    matched_sec = s
                    break

            if matched_sec:
                sec_dict = self.DEFAULT_SCHEMA.get(matched_sec, {})
                if isinstance(sec_dict, dict):
                    k_norm = key.lower().replace("-", "_")
                    k_hyphen = key.lower().replace("_", "-")
                    for k in sec_dict.keys():
                        if k.lower() in (k_norm, k_hyphen, key.lower()):
                            return f"{matched_sec}.{k}"
                return f"{matched_sec}.{key}"
            return cleaned

        # 3. Dynamic search across known sections for flat key
        target_norm = cleaned.lower().replace("-", "_")
        if cleaned in self._data and not isinstance(self._data[cleaned], dict):
            return cleaned

        matches = []
        for sec_name, sec_val in self.DEFAULT_SCHEMA.items():
            if isinstance(sec_val, dict):
                for k in sec_val.keys():
                    if k.lower() == cleaned.lower() or k.lower().replace("-", "_") == target_norm:
                        matches.append(f"{sec_name}.{k}")

        if len(matches) >= 1:
            return matches[0]

        return None

    ENV_CREDENTIAL_MAP: Dict[str, str] = {
        "telegram_dl.api_id": "TELEGRAM_API_ID",
        "telegram_dl.api_hash": "TELEGRAM_API_HASH",
        "terabox_dl.ndus_cookie": "TERABOX_NDUS_COOKIE",
        "terabox_dl.js_token": "TERABOX_JS_TOKEN",
        "network.proxy": "GLOBAL_PROXY",
    }

    def get_path(self, path: str, default: Any = None) -> Any:
        """
        Retrieves a setting using dot-notation or flat key alias.
        Automatically normalizes between hyphens and underscores for engine and section names.
        Falls back to environment variables for sensitive credentials if unset.
        """
        sec_under = path.replace("-", "_")
        sec_hyphen = path.replace("_", "-")
        if sec_under in self._data and isinstance(self._data[sec_under], dict):
            return self._data[sec_under]
        if sec_hyphen in self._data and isinstance(self._data[sec_hyphen], dict):
            return self._data[sec_hyphen]

        resolved = self.resolve_key_path(path) or path

        parts = resolved.split(".")
        current = self._data
        for part in parts:
            if not isinstance(current, dict):
                current = default
                break
            if part in current:
                current = current[part]
            elif part.replace("-", "_") in current:
                current = current[part.replace("-", "_")]
            elif part.replace("_", "-") in current:
                current = current[part.replace("_", "-")]
            else:
                current = default
                break

        # Fallback to environment variable if setting is unset/empty
        if (current is None or current == "") and resolved in self.ENV_CREDENTIAL_MAP:
            env_var = self.ENV_CREDENTIAL_MAP[resolved]
            env_val = os.getenv(env_var)
            if env_val:
                return env_val

        return current

    def set_path(self, path: str, value: Any) -> str:
        """
        Sets a setting using dot-notation or flat key alias, applying type coercion,
        and persists to disk. Returns the canonical resolved path that was set.
        Raises ConfigError if the key cannot be resolved or is invalid.
        """
        resolved = self.resolve_key_path(path)
        if not resolved:
            if "." in path:
                resolved = path
            else:
                raise ConfigError(
                    f"Unknown configuration key '{path}'. "
                    f"Type 'config' to see available settings or use dot notation (e.g. 'domain_limits.max_per_domain')."
                )

        coerced = coerce_value(value)
        parts = resolved.split(".")
        current = self._data
        for part in parts[:-1]:
            norm_part = part
            if norm_part not in current:
                if norm_part.replace("-", "_") in current:
                    norm_part = norm_part.replace("-", "_")
                elif norm_part.replace("_", "-") in current:
                    norm_part = norm_part.replace("_", "-")
                else:
                    current[norm_part] = {}
            current = current[norm_part]

        last_part = parts[-1]
        if last_part not in current:
            if last_part.replace("-", "_") in current:
                last_part = last_part.replace("-", "_")
            elif last_part.replace("_", "-") in current:
                last_part = last_part.replace("_", "-")
        current[last_part] = coerced
        self.save()
        return resolved

    @property
    def download_dir(self) -> Path:
        """Get the base download directory as a resolved Path."""
        raw_dir = self.get("general", "download_dir", "downloads")
        p = Path(raw_dir)
        if not p.is_absolute():
            return (self.PROJECT_ROOT / p).resolve()
        return p.resolve()

    @property
    def archive_db_path(self) -> Path:
        """Get the SQLite archive database path as a resolved Path."""
        raw_path = self.get("archive", "db_path", "data/archive.db")
        p = Path(raw_path)
        if not p.is_absolute():
            return (self.PROJECT_ROOT / p).resolve()
        return p.resolve()

    @property
    def archive_enabled(self) -> bool:
        """Checks if the SQLite deduplication archive is enabled."""
        return bool(self.get("archive", "enabled", True))

    @property
    def notifications_enabled(self) -> bool:
        """Checks if audio chimes / notification alerts are enabled."""
        gen = self.get("general") or {}
        if gen.get("notifications") is False or gen.get("play_sound") is False:
            return False
        if "notifications" in gen:
            return bool(gen["notifications"])
        if "play_sound" in gen:
            return bool(gen["play_sound"])
        return True

    @property
    def proxy(self) -> Optional[str]:
        """Returns the global network proxy if set."""
        net = self.get("network") or {}
        return net.get("proxy") or None

    @property
    def engine_limits(self) -> Dict[str, int]:
        """Returns the dictionary of per-engine concurrency constraints."""
        limits = self.get("engine_limits") or {}
        if not isinstance(limits, dict):
            return dict(self.DEFAULT_SCHEMA["engine_limits"])
        return dict(limits)

    def get_engine_limit(self, engine_name: str, default: int = 999) -> int:
        """
        Returns max allowed concurrent jobs for an engine, matching both hyphens and underscores.
        """
        limits = self.engine_limits
        norm_hyphen = engine_name.replace("_", "-")
        norm_under = engine_name.replace("-", "_")
        if norm_hyphen in limits:
            return limits[norm_hyphen]
        if norm_under in limits:
            return limits[norm_under]
        return default

    @property
    def domain_limits(self) -> Dict[str, Any]:
        """Returns domain-level connection rate limiting settings."""
        dl = self.get("domain_limits") or {}
        if not isinstance(dl, dict):
            return dict(self.DEFAULT_SCHEMA["domain_limits"])
        return dict(dl)

    def get_max_per_domain(self) -> int:
        """Returns the maximum allowed simultaneous connections to a single hostname."""
        dl = self.domain_limits
        if not dl.get("enabled", True):
            return 999
        return int(dl.get("max_per_domain", 1))

    def get_backend_config(self, backend_name: str) -> Dict[str, Any]:
        """Returns isolated dictionary settings for a specific backend, incorporating environment credentials."""
        # Normalize name (yt-dlp -> yt_dlp)
        clean_name = backend_name.replace("-", "_")
        sec = self._data.get(clean_name, {})
        if not isinstance(sec, dict):
            sec = {}
        res = dict(sec)

        # Inject environment variable credentials if not configured in JSON
        if clean_name == "telegram_dl":
            if os.getenv("TELEGRAM_API_ID") and not res.get("api_id"):
                res["api_id"] = os.getenv("TELEGRAM_API_ID")
            if os.getenv("TELEGRAM_API_HASH") and not res.get("api_hash"):
                res["api_hash"] = os.getenv("TELEGRAM_API_HASH")
        elif clean_name == "terabox_dl":
            if os.getenv("TERABOX_NDUS_COOKIE") and not res.get("ndus_cookie"):
                res["ndus_cookie"] = os.getenv("TERABOX_NDUS_COOKIE")
            elif os.getenv("TERABOX_NDUS") and not res.get("ndus_cookie"):
                res["ndus_cookie"] = os.getenv("TERABOX_NDUS")
            if os.getenv("TERABOX_JS_TOKEN") and not res.get("js_token"):
                res["js_token"] = os.getenv("TERABOX_JS_TOKEN")

        # Inject global proxy fallback into backend config if not overridden
        if self.proxy and not res.get("proxy"):
            res["proxy"] = self.proxy
        return res

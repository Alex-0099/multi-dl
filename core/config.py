"""
Configuration manager for MULTI_DOWNLOADER.
Handles loading, defaults fallback, saving, and typed access to settings.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from core.exceptions import ConfigError


class ConfigManager:
    """Manages application-wide configuration with per-backend isolation."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load configuration from JSON file or initialize with defaults."""
        if not self.config_path.exists():
            raise ConfigError(f"Config file not found at: {self.config_path}")

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in config file: {e}")

    def save(self) -> None:
        """Persist current configuration to disk."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4)
        except OSError as e:
            raise ConfigError(f"Failed to write config file: {e}")

    def get(self, section: str, key: Optional[str] = None, default: Any = None) -> Any:
        """Retrieve a specific section or key."""
        sec_data = self._data.get(section, {})
        if key is None:
            return sec_data
        return sec_data.get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a configuration option in memory."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value

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

    def get_backend_config(self, backend_name: str) -> Dict[str, Any]:
        """Returns isolated dictionary settings for a specific backend."""
        # Normalize name (yt-dlp -> yt_dlp)
        clean_name = backend_name.replace("-", "_")
        return self._data.get(clean_name, {})

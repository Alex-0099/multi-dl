"""
SQLite-based unified archive manager.
Stores and tracks downloaded media across all backends to prevent duplicates.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid

from core.models import ArchiveEntry, MediaType
from core.exceptions import ArchiveDuplicateError


class ArchiveManager:
    """Manages cross-backend deduplication and download history in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id              TEXT PRIMARY KEY,
                    url             TEXT NOT NULL,
                    file_path       TEXT NOT NULL,
                    file_name       TEXT NOT NULL,
                    file_hash       TEXT,
                    file_size       INTEGER,
                    backend         TEXT NOT NULL,
                    source_site     TEXT,
                    media_type      TEXT DEFAULT 'unknown',
                    metadata_json   TEXT,
                    downloaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(url, backend)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON downloads(file_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_backend ON downloads(backend);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_site ON downloads(source_site);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_downloaded_at ON downloads(downloaded_at);")
            conn.commit()

    @staticmethod
    def calculate_file_hash(file_path: Path, chunk_size: int = 65536) -> Optional[str]:
        """Calculates SHA-256 hash of a downloaded file."""
        if not file_path.exists() or not file_path.is_file():
            return None
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()

    def is_url_downloaded(self, url: str, backend: Optional[str] = None) -> bool:
        """Checks if a URL has already been recorded in the archive."""
        with self._get_connection() as conn:
            if backend:
                cursor = conn.execute(
                    "SELECT 1 FROM downloads WHERE url = ? AND backend = ? LIMIT 1",
                    (url, backend)
                )
            else:
                cursor = conn.execute(
                    "SELECT 1 FROM downloads WHERE url = ? LIMIT 1",
                    (url,)
                )
            return cursor.fetchone() is not None

    def get_by_hash(self, file_hash: str) -> Optional[ArchiveEntry]:
        """Look up an existing archive entry by its file hash."""
        if not file_hash:
            return None
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM downloads WHERE file_hash = ? LIMIT 1",
                (file_hash,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_entry(row)
        return None

    def add_entry(self, entry: ArchiveEntry) -> None:
        """Saves a new download record to the archive."""
        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO downloads (
                        id, url, file_path, file_name, file_hash, file_size,
                        backend, source_site, media_type, metadata_json, downloaded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id or str(uuid.uuid4()),
                        entry.url,
                        entry.file_path,
                        entry.file_name,
                        entry.file_hash,
                        entry.file_size,
                        entry.backend,
                        entry.source_site,
                        entry.media_type.value if hasattr(entry.media_type, "value") else str(entry.media_type),
                        json.dumps(entry.metadata or {}),
                        entry.downloaded_at.isoformat() if entry.downloaded_at else datetime.now(timezone.utc).isoformat(),
                    )
                )
                conn.commit()
            except sqlite3.IntegrityError as e:
                raise ArchiveDuplicateError(f"Entry already exists in archive: {e}")

    def search(self, query: str, backend: Optional[str] = None) -> List[ArchiveEntry]:
        """Search downloads by filename or URL."""
        with self._get_connection() as conn:
            wildcard = f"%{query}%"
            if backend:
                cursor = conn.execute(
                    """
                    SELECT * FROM downloads 
                    WHERE (file_name LIKE ? OR url LIKE ?) AND backend = ?
                    ORDER BY downloaded_at DESC
                    """,
                    (wildcard, wildcard, backend)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM downloads 
                    WHERE file_name LIKE ? OR url LIKE ?
                    ORDER BY downloaded_at DESC
                    """,
                    (wildcard, wildcard)
                )
            return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_stats(self) -> Dict[str, Any]:
        """Returns summary metrics (total files, size, count per backend)."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_downloads,
                    COALESCE(SUM(file_size), 0) as total_bytes,
                    backend,
                    COUNT(backend) as count_by_backend
                FROM downloads
                GROUP BY backend
            """)
            rows = cursor.fetchall()
            stats: Dict[str, Any] = {"by_backend": {}, "total_count": 0, "total_bytes": 0}
            for row in rows:
                stats["by_backend"][row["backend"]] = row["count_by_backend"]
                stats["total_count"] += row["count_by_backend"]
                stats["total_bytes"] += row["total_bytes"]
            return stats

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> ArchiveEntry:
        return ArchiveEntry(
            id=row["id"],
            url=row["url"],
            file_path=row["file_path"],
            file_name=row["file_name"],
            file_hash=row["file_hash"],
            file_size=row["file_size"],
            backend=row["backend"],
            source_site=row["source_site"],
            media_type=MediaType(row["media_type"]) if row["media_type"] in [m.value for m in MediaType] else MediaType.UNKNOWN,
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            downloaded_at=datetime.fromisoformat(row["downloaded_at"]) if row["downloaded_at"] else datetime.now(timezone.utc)
        )

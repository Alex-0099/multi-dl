"""
Queue manager for batch downloads, priorities, and concurrent worker dispatching.
Supports persistence in data/queue.json and engine-aware slot constraints.
"""

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from core.models import QueueItem, TaskStatus
from core.router import URLRouter


class QueueManager:
    """Manages ordered download queue with persistence and engine-aware scheduling."""

    DEFAULT_ENGINE_LIMITS = {
        "telegram-dl": 1,  # Prevent SQLite session file lock collisions
        "yt-dlp": 2,        # Cap concurrent FFmpeg muxing jobs
    }

    def __init__(self, queue_file: Optional[Path] = None):
        self.queue_file = queue_file or Path("data/queue.json")
        self._items: Dict[str, QueueItem] = {}
        self.load()

    def add(
        self,
        url: str,
        backend: Optional[str] = None,
        priority: int = 0,
        options: Optional[Dict[str, Any]] = None,
    ) -> QueueItem:
        """Add a single URL to the queue with automatic pre-routing classification."""
        resolved_backend = backend or URLRouter.detect_backend_name(url)
        item_id = str(uuid.uuid4())[:8]
        item = QueueItem(
            id=item_id,
            url=url,
            backend=resolved_backend,
            priority=priority,
            options=options or {},
            status=TaskStatus.QUEUED,
        )
        self._items[item_id] = item
        self.save()
        return item

    def add_batch(
        self,
        urls: List[str],
        priority: int = 0,
        options: Optional[Dict[str, Any]] = None,
        backend: Optional[str] = None,
    ) -> List[QueueItem]:
        """Adds a collection of URLs to the queue, pre-classifying each link in bulk."""
        added = []
        for url in urls:
            clean_url = url.strip()
            if not clean_url or clean_url.startswith("#"):
                continue
            resolved_backend = backend or URLRouter.detect_backend_name(clean_url)
            item_id = str(uuid.uuid4())[:8]
            item = QueueItem(
                id=item_id,
                url=clean_url,
                backend=resolved_backend,
                priority=priority,
                options=options or {},
                status=TaskStatus.QUEUED,
            )
            self._items[item_id] = item
            added.append(item)
        if added:
            self.save()
        return added

    def get_next(self) -> Optional[QueueItem]:
        """Fetch the next pending queue item by priority, then creation time."""
        queued_items = [
            item for item in self._items.values()
            if item.status in (TaskStatus.QUEUED, TaskStatus.PENDING)
        ]
        if not queued_items:
            return None
        # Sort by priority desc, then added_at asc
        queued_items.sort(key=lambda x: (-x.priority, x.added_at))
        return queued_items[0]

    def get_next_eligible(
        self,
        active_engine_counts: Dict[str, int],
        engine_limits: Optional[Dict[str, int]] = None,
    ) -> Optional[QueueItem]:
        """
        Fetches the highest-priority pending task whose backend engine is not saturated.
        If an engine limit is reached (e.g. 1 active Telegram download), it gracefully
        skips ahead to other pending engine items (e.g. yt-dlp or gallery-dl) to prevent
        pipeline starvation.
        """
        limits = dict(self.DEFAULT_ENGINE_LIMITS)
        if engine_limits:
            limits.update(engine_limits)

        queued_items = [
            item for item in self._items.values()
            if item.status in (TaskStatus.QUEUED, TaskStatus.PENDING)
        ]
        if not queued_items:
            return None

        # Sort by priority desc, then added_at asc
        queued_items.sort(key=lambda x: (-x.priority, x.added_at))

        for candidate in queued_items:
            engine_name = candidate.backend or "unknown"
            active_count = active_engine_counts.get(engine_name, 0)
            max_allowed = limits.get(engine_name, 999)

            if active_count < max_allowed:
                return candidate

        return None

    def get_item(self, item_id: str) -> Optional[QueueItem]:
        """Retrieves a specific item by its ID."""
        return self._items.get(item_id)

    def mark_status(self, item_id: str, status: TaskStatus, error: Optional[str] = None) -> None:
        """Update the status of a specific queue entry."""
        if item_id in self._items:
            self._items[item_id].status = status
            if error:
                self._items[item_id].error_message = error
            self.save()

    def remove(self, item_id: str) -> None:
        """Remove an item from the queue."""
        if item_id in self._items:
            del self._items[item_id]
            self.save()

    def retry_failed(self) -> int:
        """Resets all failed items back to QUEUED status and clears error messages."""
        count = 0
        for item in self._items.values():
            if item.status == TaskStatus.FAILED:
                item.status = TaskStatus.QUEUED
                item.error_message = None
                count += 1
        if count > 0:
            self.save()
        return count

    def list_all(self, status_filter: Optional[TaskStatus] = None) -> List[QueueItem]:
        """List all items in the queue, optionally filtered by status."""
        items = list(self._items.values())
        if status_filter:
            items = [item for item in items if item.status == status_filter]
        items.sort(key=lambda x: (-x.priority, x.added_at))
        return items

    def clear(self, status_filter: Optional[TaskStatus] = None) -> int:
        """Clear items matching status_filter, or all items if None."""
        if status_filter is None:
            count = len(self._items)
            self._items.clear()
        else:
            to_remove = [k for k, v in self._items.items() if v.status == status_filter]
            count = len(to_remove)
            for k in to_remove:
                del self._items[k]
        self.save()
        return count

    def count_remaining(self) -> int:
        """Returns the number of items that are still queued or pending."""
        return sum(
            1 for item in self._items.values()
            if item.status in (TaskStatus.QUEUED, TaskStatus.PENDING)
        )

    def stats(self) -> Dict[str, Any]:
        """Calculates current queue statistics grouped by status and backend."""
        status_counts = Counter(item.status.value for item in self._items.values())
        backend_counts = Counter((item.backend or "auto") for item in self._items.values())
        return {
            "total": len(self._items),
            "by_status": dict(status_counts),
            "by_backend": dict(backend_counts),
        }

    def save(self) -> None:
        """Save queue items to disk."""
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        serialized = [
            {
                "id": item.id,
                "url": item.url,
                "backend": item.backend,
                "priority": item.priority,
                "options": item.options,
                "status": item.status.value,
                "added_at": item.added_at.isoformat(),
                "error_message": item.error_message,
            }
            for item in self._items.values()
        ]
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=4)

    def load(self) -> None:
        """Load queue from disk if present."""
        if not self.queue_file.exists():
            return
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    try:
                        added_at = datetime.fromisoformat(entry.get("added_at")) if entry.get("added_at") else datetime.now()
                    except Exception:
                        added_at = datetime.now()
                    self._items[entry["id"]] = QueueItem(
                        id=entry["id"],
                        url=entry["url"],
                        backend=entry.get("backend"),
                        priority=entry.get("priority", 0),
                        options=entry.get("options", {}),
                        status=TaskStatus(entry.get("status", TaskStatus.QUEUED.value)),
                        added_at=added_at,
                        error_message=entry.get("error_message"),
                    )
        except Exception:
            pass

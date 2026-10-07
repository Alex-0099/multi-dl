"""
Queue manager for batch downloads and priorities.
Supports persistence in data/queue.json.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from core.models import QueueItem, TaskStatus


class QueueManager:
    """Manages ordered download queue with persistence."""

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
        """Add a single URL to the queue."""
        item_id = str(uuid.uuid4())[:8]
        item = QueueItem(
            id=item_id,
            url=url,
            backend=backend,
            priority=priority,
            options=options or {},
            status=TaskStatus.QUEUED,
        )
        self._items[item_id] = item
        self.save()
        return item

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
                    self._items[entry["id"]] = QueueItem(
                        id=entry["id"],
                        url=entry["url"],
                        backend=entry.get("backend"),
                        priority=entry.get("priority", 0),
                        options=entry.get("options", {}),
                        status=TaskStatus(entry.get("status", TaskStatus.QUEUED.value)),
                        error_message=entry.get("error_message"),
                    )
        except Exception:
            pass

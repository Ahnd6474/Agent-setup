# type: ignore
"""Persistent per-session chat resource allocations."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .schemas import ResourceAllocation, now_iso


class ResourceManager:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._semaphores: dict[str, tuple[int, threading.BoundedSemaphore]] = {}

    def get(self, session_id: str) -> ResourceAllocation:
        path = self._path(session_id)
        if not path.exists():
            return self.allocate(session_id)
        return ResourceAllocation.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def allocate(self, session_id: str, overrides: dict[str, Any] | None = None) -> ResourceAllocation:
        current = self.get(session_id).to_dict() if self._path(session_id).exists() else {}
        values = {**current, **(overrides or {}), "session_id": session_id}
        allocation = ResourceAllocation.create(
            session_id=session_id,
            compute_slots=self._positive_int(values.get("compute_slots", 1), "compute_slots"),
            compute_nodes=self._compute_nodes(values.get("compute_nodes")),
            disk_quota_bytes=self._positive_int(values.get("disk_quota_bytes", 5_000_000_000), "disk_quota_bytes"),
            memory_message_limit=self._positive_int(
                values.get("memory_message_limit", 24),
                "memory_message_limit",
            ),
            memory_char_limit=self._positive_int(values.get("memory_char_limit", 48_000), "memory_char_limit"),
        )
        used_bytes = self._size_bytes(self.store.session_dir(session_id))
        if used_bytes > allocation.disk_quota_bytes:
            raise ValueError(
                f"disk_quota_bytes is below current usage: used={used_bytes}, "
                f"quota={allocation.disk_quota_bytes}"
            )
        if current:
            allocation.created_at = str(current["created_at"])
            allocation.updated_at = now_iso()
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(allocation.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.store.commit(f"allocate resources to {session_id}")
        with self._lock:
            self._semaphores.pop(session_id, None)
        return allocation

    def usage(self, session_id: str) -> dict[str, int]:
        session_bytes = self._size_bytes(self.store.session_dir(session_id))
        allocation = self.get(session_id)
        return {
            "session_bytes": session_bytes,
            "used_bytes": session_bytes,
            "available_bytes": max(0, allocation.disk_quota_bytes - session_bytes),
        }

    def ensure_disk_available(self, session_id: str, requested_bytes: int = 0) -> None:
        allocation = self.get(session_id)
        used = self.usage(session_id)["used_bytes"]
        if used + requested_bytes > allocation.disk_quota_bytes:
            raise RuntimeError(
                f"session disk quota exceeded: used={used}, requested={requested_bytes}, "
                f"quota={allocation.disk_quota_bytes}"
            )

    @contextlib.contextmanager
    def compute_slot(self, session_id: str, timeout_seconds: float = 30.0) -> Iterator[ResourceAllocation]:
        allocation = self.get(session_id)
        semaphore = self._semaphore(session_id, allocation.compute_slots)
        if not semaphore.acquire(timeout=timeout_seconds):
            raise RuntimeError(f"session compute allocation is busy: {session_id}")
        try:
            yield allocation
        finally:
            semaphore.release()

    def _path(self, session_id: str) -> Path:
        return self.store.session_dir(session_id) / "resources.json"

    def _semaphore(self, session_id: str, slots: int) -> threading.BoundedSemaphore:
        with self._lock:
            current = self._semaphores.get(session_id)
            if current is None or current[0] != slots:
                current = (slots, threading.BoundedSemaphore(slots))
                self._semaphores[session_id] = current
            return current[1]

    @staticmethod
    def _positive_int(value: Any, name: str) -> int:
        parsed = int(value)
        if parsed < 1:
            raise ValueError(f"{name} must be positive")
        return parsed

    @staticmethod
    def _compute_nodes(value: Any) -> list[str]:
        nodes = list(value or ["node2", "node3", "node4"])
        if not nodes or not all(isinstance(node, str) and node for node in nodes):
            raise ValueError("compute_nodes must contain node names")
        return nodes

    @staticmethod
    def _size_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

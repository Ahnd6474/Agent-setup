# type: ignore
"""Data structures shared by the local agentic server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

Role = Literal["system", "user", "assistant", "tool"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


@dataclass
class Message:
    role: Role
    content: str
    reasoning: str | None = None
    created_at: str = field(default_factory=now_iso)
    message_id: str = field(default_factory=lambda: new_id("msg"))

    def to_dict(self) -> dict[str, str | None]:
        return {
            "role": self.role,
            "content": self.content,
            "reasoning": self.reasoning,
            "created_at": self.created_at,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            role=data["role"],
            content=data["content"],
            reasoning=data.get("reasoning"),
            created_at=data.get("created_at", now_iso()),
            message_id=data.get("message_id", new_id("msg")),
        )


@dataclass
class SessionRecord:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def create(cls, title: str, metadata: dict[str, Any] | None = None) -> "SessionRecord":
        created_at = now_iso()
        return cls(
            session_id=new_id("ses"),
            title=title,
            created_at=created_at,
            updated_at=created_at,
            metadata=metadata or {},
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            session_id=data["session_id"],
            title=data["title"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class ResourceAllocation:
    session_id: str
    compute_slots: int
    compute_nodes: list[str]
    disk_quota_bytes: int
    memory_message_limit: int
    memory_char_limit: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "compute_slots": self.compute_slots,
            "compute_nodes": self.compute_nodes,
            "disk_quota_bytes": self.disk_quota_bytes,
            "memory_message_limit": self.memory_message_limit,
            "memory_char_limit": self.memory_char_limit,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        compute_slots: int = 1,
        compute_nodes: list[str] | None = None,
        disk_quota_bytes: int = 5_000_000_000,
        memory_message_limit: int = 24,
        memory_char_limit: int = 48_000,
    ) -> "ResourceAllocation":
        created_at = now_iso()
        return cls(
            session_id=session_id,
            compute_slots=compute_slots,
            compute_nodes=compute_nodes or ["node2", "node3", "node4"],
            disk_quota_bytes=disk_quota_bytes,
            memory_message_limit=memory_message_limit,
            memory_char_limit=memory_char_limit,
            created_at=created_at,
            updated_at=created_at,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceAllocation":
        return cls(
            session_id=data["session_id"],
            compute_slots=int(data["compute_slots"]),
            compute_nodes=list(data["compute_nodes"]),
            disk_quota_bytes=int(data["disk_quota_bytes"]),
            memory_message_limit=int(data["memory_message_limit"]),
            memory_char_limit=int(data["memory_char_limit"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

# type: ignore
"""Data structures shared by the local agentic server."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

Role = Literal["system", "user", "assistant", "tool"]
Mode = Literal["chat", "coding"]
RunStatus = Literal["running", "completed", "failed"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


@dataclass
class Message:
    role: Role
    content: str
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            role=data["role"],
            content=data["content"],
            created_at=data.get("created_at", now_iso()),
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
class RunRecord:
    run_id: str
    session_id: str
    mode: Mode
    prompt: str
    source_dir: str | None
    workspace_dir: str | None
    status: RunStatus
    created_at: str
    updated_at: str
    target: dict[str, Any]
    limits: dict[str, Any]
    sandbox: dict[str, Any]
    llm_backend: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "prompt": self.prompt,
            "source_dir": self.source_dir,
            "workspace_dir": self.workspace_dir,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "target": self.target,
            "limits": self.limits,
            "sandbox": self.sandbox,
            "llm_backend": self.llm_backend,
            "error": self.error,
        }

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        mode: Mode,
        prompt: str,
        source_dir: Path | None,
        workspace_dir: Path | None,
        target: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
        sandbox: dict[str, Any] | None = None,
        llm_backend: dict[str, Any] | None = None,
    ) -> "RunRecord":
        created_at = now_iso()
        return cls(
            run_id=new_id("run"),
            session_id=session_id,
            mode=mode,
            prompt=prompt,
            source_dir=str(source_dir) if source_dir else None,
            workspace_dir=str(workspace_dir) if workspace_dir else None,
            status="running",
            created_at=created_at,
            updated_at=created_at,
            target=target or {},
            limits=limits or {},
            sandbox=sandbox or {},
            llm_backend=llm_backend or {},
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=data["run_id"],
            session_id=data["session_id"],
            mode=data["mode"],
            prompt=data["prompt"],
            source_dir=data.get("source_dir"),
            workspace_dir=data.get("workspace_dir"),
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            target=data.get("target", {}),
            limits=data.get("limits", {}),
            sandbox=data.get("sandbox", {}),
            llm_backend=data.get("llm_backend", {}),
            error=data.get("error"),
        )

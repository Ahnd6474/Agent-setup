# type: ignore
"""Git-backed durable store for chat artifacts."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from .schemas import Message, SessionRecord, new_id, now_iso

DEFAULT_AGENTIC_HOME = Path("~/.agentic-local").expanduser()


class AgentStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or os.environ.get("AGENTIC_HOME", DEFAULT_AGENTIC_HOME)).expanduser()
        self.store_dir = self.root / "store"
        self.sessions_dir = self.store_dir / "sessions"
        self.max_messages = int(os.environ.get("AGENTIC_MAX_MESSAGES_PER_SESSION", "100"))
        self.max_git_commits = int(os.environ.get("AGENTIC_MAX_GIT_COMMITS", "50"))
        self._data_lock = threading.RLock()
        self._git_lock = threading.RLock()

    @contextlib.contextmanager
    def transaction(self):
        with self._data_lock:
            yield

    def init(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        if not (self.store_dir / ".git").exists():
            self._git(["init"])
        self._git(["config", "user.name", "agentic-local"])
        self._git(["config", "user.email", "agentic-local@example.invalid"])
        gitignore = self.store_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n.DS_Store\n", encoding="utf-8")
            self.commit("initialize agentic local store")

    def create_session(self, title: str, metadata: dict[str, Any] | None = None) -> SessionRecord:
        with self._data_lock:
            self.init()
            session = SessionRecord.create(title, metadata)
            session_dir = self.session_dir(session.session_id)
            session_dir.mkdir(parents=True, exist_ok=False)
            self._write_json(session_dir / "session.json", session.to_dict())
            (session_dir / "chat.md").write_text(f"# {title}\n\n", encoding="utf-8")
            self.commit(f"create session {session.session_id}")
            return session

    def get_session(self, session_id: str) -> SessionRecord:
        with self._data_lock:
            data = self._read_json(self.session_dir(session_id) / "session.json")
            return SessionRecord.from_dict(data)

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        user_edited: bool = True,
        commit: bool = True,
    ) -> SessionRecord:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("session title cannot be empty")
        clean_title = clean_title[:120]
        with self._data_lock:
            session = self.get_session(session_id)
            session.title = clean_title
            session.updated_at = now_iso()
            session.metadata = {
                **session.metadata,
                "title_user_edited": bool(user_edited),
                "title_auto_generated": not user_edited,
            }
            session_dir = self.session_dir(session_id)
            self._write_json(session_dir / "session.json", session.to_dict())
            self._rebuild_chat_markdown(session_id, self.list_messages(session_id))
            if commit:
                self.commit(f"rename session {session_id}")
            return session

    def list_sessions(self) -> list[SessionRecord]:
        with self._data_lock:
            self.init()
            records: list[SessionRecord] = []
            for path in sorted(self.sessions_dir.glob("*/session.json")):
                records.append(SessionRecord.from_dict(self._read_json(path)))
            return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def delete_session(self, session_id: str) -> None:
        with self._data_lock:
            session_dir = self.session_dir(session_id)
            if not session_dir.is_dir():
                raise FileNotFoundError(f"session not found: {session_id}")
            shutil.rmtree(session_dir)
            self.commit(f"delete session {session_id}")

    def append_message(self, session_id: str, message: Message, *, commit: bool = True) -> None:
        with self._data_lock:
            session = self.get_session(session_id)
            session.updated_at = now_iso()
            session_dir = self.session_dir(session_id)
            self._migrate_messages(session_id)
            self._write_json(session_dir / "session.json", session.to_dict())
            with (session_dir / "chat.md").open("a", encoding="utf-8") as f:
                f.write(f"## {message.role} - {message.created_at}\n\n{message.content}\n\n")
            with (session_dir / "messages.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
            self.compact_session(session_id)
            if commit:
                self.commit(f"append {message.role} message to {session_id}")

    def update_message(
        self,
        session_id: str,
        message_id: str,
        *,
        content: str,
        reasoning: str | None,
    ) -> None:
        with self._data_lock:
            messages = self.list_messages(session_id)
            for message in messages:
                if message.message_id == message_id:
                    message.content = content
                    message.reasoning = reasoning
                    break
            else:
                raise FileNotFoundError(f"message not found: {message_id}")
            self._write_messages(session_id, messages)
            self._rebuild_chat_markdown(session_id, messages)

    def list_messages(self, session_id: str) -> list[Message]:
        with self._data_lock:
            path = self._migrate_messages(session_id)
            return [
                Message.from_dict(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

    def _migrate_messages(self, session_id: str) -> Path:
        session_dir = self.session_dir(session_id)
        path = session_dir / "messages.jsonl"
        if path.exists():
            return path
        chat_path = session_dir / "chat.md"
        messages: list[Message] = []
        if chat_path.exists():
            pattern = re.compile(
                r"^## (system|user|assistant|tool) - ([^\n]+)\n\n(.*?)(?=^## |\Z)",
                re.MULTILINE | re.DOTALL,
            )
            for role, created_at, content in pattern.findall(chat_path.read_text(encoding="utf-8")):
                messages.append(Message(role=role, content=content.strip(), created_at=created_at))
        path.write_text(
            "".join(json.dumps(message.to_dict(), ensure_ascii=False) + "\n" for message in messages),
            encoding="utf-8",
        )
        return path

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def commit(self, message: str) -> None:
        with self._git_lock:
            self._git(["add", "-A"])
            status = self._git(["status", "--porcelain"], capture=True).stdout.strip()
            if not status:
                return
            self._git(["commit", "-m", message])
            self._compact_repository_if_needed()

    def compact_session(self, session_id: str) -> None:
        with self._data_lock:
            path = self._migrate_messages(session_id)
            messages = [
                Message.from_dict(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(messages) <= self.max_messages:
                return
            retained_count = max(1, self.max_messages - 1)
            removed = len(messages) - retained_count
            retained = messages[-retained_count:]
            notice = Message(role="system", content=f"{removed} older messages were removed by the retention policy.")
            compacted = [notice, *retained]
            self._write_messages(session_id, compacted)
            self._rebuild_chat_markdown(session_id, compacted)

    def _write_messages(self, session_id: str, messages: list[Message]) -> None:
        path = self.session_dir(session_id) / "messages.jsonl"
        path.write_text(
            "".join(
                json.dumps(message.to_dict(), ensure_ascii=False) + "\n"
                for message in messages
            ),
            encoding="utf-8",
        )

    def _rebuild_chat_markdown(
        self, session_id: str, messages: list[Message]
    ) -> None:
        session = self.get_session(session_id)
        chat_path = self.session_dir(session_id) / "chat.md"
        chat_path.write_text(
            f"# {session.title}\n\n"
            + "".join(
                f"## {message.role} - {message.created_at}\n\n{message.content}\n\n"
                for message in messages
            ),
            encoding="utf-8",
        )

    def _compact_repository_if_needed(self) -> None:
        count = int(self._git(["rev-list", "--count", "HEAD"], capture=True).stdout.strip() or "0")
        if count < self.max_git_commits:
            return
        branch = self._git(["branch", "--show-current"], capture=True).stdout.strip() or "main"
        temporary_branch = f"agentic-compact-{new_id('git')}"
        self._git(["checkout", "--orphan", temporary_branch])
        self._git(["add", "-A"])
        self._git(["commit", "-m", "compact agentic store"])
        self._git(["branch", "-D", branch])
        self._git(["branch", "-m", branch])
        self._git(["reflog", "expire", "--expire=now", "--all"])
        self._git(["gc", "--prune=now"])

    def _git(self, args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["git", *args],
            cwd=self.store_dir,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        )

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

# type: ignore
"""Git-backed durable store for chat and agent run artifacts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .schemas import Message, RunRecord, SessionRecord, new_id, now_iso

DEFAULT_AGENTIC_HOME = Path("~/.agentic-local").expanduser()


class AgentStore:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or os.environ.get("AGENTIC_HOME", DEFAULT_AGENTIC_HOME)).expanduser()
        self.store_dir = self.root / "store"
        self.sessions_dir = self.store_dir / "sessions"
        self.workspaces_dir = self.root / "workspaces"
        self.envs_dir = self.root / "envs"

    def init(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.envs_dir.mkdir(parents=True, exist_ok=True)
        if not (self.store_dir / ".git").exists():
            self._git(["init"])
        self._git(["config", "user.name", "agentic-local"])
        self._git(["config", "user.email", "agentic-local@example.invalid"])
        gitignore = self.store_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n.DS_Store\n", encoding="utf-8")
            self.commit("initialize agentic local store")

    def create_session(self, title: str, metadata: dict[str, Any] | None = None) -> SessionRecord:
        self.init()
        session = SessionRecord.create(title, metadata)
        session_dir = self.session_dir(session.session_id)
        session_dir.mkdir(parents=True, exist_ok=False)
        self._write_json(session_dir / "session.json", session.to_dict())
        (session_dir / "chat.md").write_text(f"# {title}\n\n", encoding="utf-8")
        (session_dir / "runs").mkdir()
        self.commit(f"create session {session.session_id}")
        return session

    def get_session(self, session_id: str) -> SessionRecord:
        data = self._read_json(self.session_dir(session_id) / "session.json")
        return SessionRecord.from_dict(data)

    def list_sessions(self) -> list[SessionRecord]:
        self.init()
        records: list[SessionRecord] = []
        for path in sorted(self.sessions_dir.glob("*/session.json")):
            records.append(SessionRecord.from_dict(self._read_json(path)))
        return records

    def append_message(self, session_id: str, message: Message, *, commit: bool = True) -> None:
        session = self.get_session(session_id)
        session.updated_at = now_iso()
        session_dir = self.session_dir(session_id)
        self._write_json(session_dir / "session.json", session.to_dict())
        with (session_dir / "chat.md").open("a", encoding="utf-8") as f:
            f.write(f"## {message.role} - {message.created_at}\n\n{message.content}\n\n")
        if commit:
            self.commit(f"append {message.role} message to {session_id}")

    def create_run(
        self,
        *,
        session_id: str,
        mode: str,
        prompt: str,
        source_dir: Path | None,
        workspace_dir: Path | None,
        target: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
        sandbox: dict[str, Any] | None = None,
        llm_backend: dict[str, Any] | None = None,
    ) -> RunRecord:
        self.init()
        run = RunRecord.create(
            session_id=session_id,
            mode=mode,  # type: ignore[arg-type]
            prompt=prompt,
            source_dir=source_dir,
            workspace_dir=workspace_dir,
            target=target,
            limits=limits,
            sandbox=sandbox,
            llm_backend=llm_backend,
        )
        run_dir = self.run_dir(session_id, run.run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        self._write_json(run_dir / "manifest.json", run.to_dict())
        (run_dir / "transcript.jsonl").touch()
        self.commit(f"create run {run.run_id}")
        return run

    def update_run(self, run: RunRecord) -> None:
        run.updated_at = now_iso()
        self._write_json(self.run_dir(run.session_id, run.run_id) / "manifest.json", run.to_dict())

    def append_transcript(self, run: RunRecord, event: dict[str, Any]) -> None:
        event = {"created_at": now_iso(), **event}
        with (self.run_dir(run.session_id, run.run_id) / "transcript.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_run_artifact(self, run: RunRecord, name: str, content: str) -> Path:
        if "/" in name or name.startswith("."):
            raise ValueError(f"invalid artifact name: {name}")
        path = self.run_dir(run.session_id, run.run_id) / name
        path.write_text(content, encoding="utf-8")
        return path

    def read_run_artifact(self, session_id: str, run_id: str, name: str) -> str:
        return (self.run_dir(session_id, run_id) / name).read_text(encoding="utf-8")

    def complete_run(self, run: RunRecord, *, patch: str = "", error: str | None = None) -> None:
        run.status = "failed" if error else "completed"
        run.error = error
        if patch:
            self.write_run_artifact(run, "result.patch", patch)
        else:
            self.write_run_artifact(run, "result.patch", "")
        self.update_run(run)
        self.commit(f"{run.status} run {run.run_id}")

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def run_dir(self, session_id: str, run_id: str) -> Path:
        return self.session_dir(session_id) / "runs" / run_id

    def new_workspace_dir(self, session_id: str, run_id: str | None = None) -> Path:
        return self.workspaces_dir / session_id / (run_id or new_id("run"))

    def commit(self, message: str) -> None:
        self._git(["add", "-A"])
        status = self._git(["status", "--porcelain"], capture=True).stdout.strip()
        if not status:
            return
        self._git(["commit", "-m", message])

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

# type: ignore
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from exo_tools.agent_core.environment import EnvironmentManager
from exo_tools.agent_core.runner import AgentRunner
from exo_tools.agent_core.schemas import Message
from exo_tools.agent_core.store import AgentStore
from exo_tools.agent_core.workspace import WorkspaceManager


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        del messages, temperature
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_store_chat_markdown_and_git_commit(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("test session")

    store.append_message(session.session_id, Message(role="user", content="hello"))

    chat = (store.session_dir(session.session_id) / "chat.md").read_text()
    assert "# test session" in chat
    assert "hello" in chat
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=store.store_dir,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "append user message" in log


def test_workspace_diff_and_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('old')\n")

    workspace = tmp_path / "workspace"
    manager = WorkspaceManager()
    manager.prepare(source, workspace)
    (workspace / "app.py").write_text("print('new')\n")
    patch = manager.diff(workspace)

    assert "print('new')" in patch
    manager.restore(source, patch)
    assert (source / "app.py").read_text() == "print('new')\n"


def test_runner_coding_loop_with_fake_llm(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('old')\n")

    patch = """diff --git a/app.py b/app.py
index 6bb6a16..3d6bbb7 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print('old')
+print('new')
"""
    fake_llm = FakeLLM(
        [
            json.dumps({"tool": "apply_patch", "args": {"patch": patch}}),
            json.dumps({"tool": "finish", "args": {"message": "done"}}),
        ]
    )
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("coding")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    run = runner.run_coding(
        session_id=session.session_id,
        prompt="update app",
        source_dir=source,
        target={"sandbox": {"create_venv": False}},
    )

    assert run.status == "completed"
    assert run.target["server_node"] == "node1"
    assert run.target["io_node"] == "node1"
    assert run.target["llm_inference_nodes"] == ["node2", "node3", "node4"]
    assert run.sandbox["write_scope"] == "workspace_only"
    assert run.sandbox["environment"]["profile"] == "full"
    assert run.sandbox["environment"]["create_venv"] is False
    assert run.llm_backend["routing"] == "node1_api_io_with_worker_inference"
    result_patch = store.read_run_artifact(session.session_id, run.run_id, "result.patch")
    assert "print('new')" in result_patch


def test_workspace_rejects_oversized_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.txt").write_text("x" * 32)

    workspace = tmp_path / "workspace"
    manager = WorkspaceManager()

    try:
        manager.prepare(source, workspace, max_workspace_bytes=4)
    except RuntimeError as e:
        assert "exceeds limit" in str(e)
    else:
        raise AssertionError("expected oversized workspace rejection")
    assert not workspace.exists()


def test_environment_profile_writes_requirements_and_inventory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = EnvironmentManager(tmp_path / "agentic")

    info = manager.prepare(profile_name="document", workspace=workspace, create_venv=False)

    assert info["profile"] == "document"
    assert "python-docx" in info["python_packages"]
    assert "rg" in info["system_tools"]
    requirements = workspace / ".agentic" / "requirements-document.txt"
    assert "pdfplumber" in requirements.read_text()


def test_korean_document_profile_includes_hwpx_and_ocr(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = EnvironmentManager(tmp_path / "agentic")

    info = manager.prepare(profile_name="korean_document", workspace=workspace, create_venv=False)

    assert "jakal-hwpx" in info["python_packages"]
    assert "hwp-hwpx-parser" in info["python_packages"]
    assert "python-hwpx" in info["python_packages"]
    assert "pytesseract" in info["python_packages"]
    assert "tesseract" in info["system_tools"]
    requirements = workspace / ".agentic" / "requirements-korean_document.txt"
    text = requirements.read_text()
    assert "jakal-hwpx" in text
    assert "hwp-hwpx-parser" in text

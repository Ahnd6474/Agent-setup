# type: ignore
from __future__ import annotations

from pathlib import Path

import pytest
from exo_tools.agent_server import create_app
from fastapi.testclient import TestClient


def test_session_api_uses_git_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    created = client.post("/sessions", json={"title": "api session"}).json()

    assert created["title"] == "api session"
    session_id = created["session_id"]
    fetched = client.get(f"/sessions/{session_id}").json()
    assert fetched["session"]["session_id"] == session_id
    assert "# api session" in fetched["chat_md"]
    listed = client.get("/sessions").json()
    assert listed["sessions"][0]["session_id"] == session_id


def test_ui_route_serves_local_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agentic Local Server" in response.text
    assert "/sessions" in response.text

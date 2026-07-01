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
    assert fetched["messages"] == []
    listed = client.get("/sessions").json()
    assert listed["sessions"][0]["session_id"] == session_id
    resources = client.get(f"/sessions/{session_id}/resources").json()
    assert resources["allocation"]["compute_nodes"] == ["node2", "node3", "node4"]

    updated = client.put(
        f"/sessions/{session_id}/resources",
        json={
            "compute_slots": 2,
            "compute_nodes": ["node2", "node3", "node4"],
            "disk_quota_bytes": 2_000_000,
            "memory_message_limit": 12,
            "memory_char_limit": 24_000,
        },
    ).json()
    assert updated["allocation"]["compute_slots"] == 2

    renamed = client.put(
        f"/sessions/{session_id}",
        json={"title": "renamed api"},
    ).json()
    assert renamed["session"]["title"] == "renamed api"
    fetched = client.get(f"/sessions/{session_id}").json()
    assert fetched["session"]["metadata"]["title_user_edited"] is True
    assert "# renamed api" in fetched["chat_md"]


def test_ui_route_serves_local_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Exodus</title>" in response.text
    assert "Exodus" in response.text
    assert "/assets/exodus-logo.png" in response.text
    assert "--sidebar: #0b4f23" in response.text
    assert "--sidebar-hover: rgba(255, 255, 255, 0.13)" in response.text
    assert "Agentic Local" not in response.text
    assert "/sessions" in response.text
    assert 'id="reasoning-effort"' in response.text
    assert "reasoning_effort: requestReasoningEffort" in response.text
    assert "state.pendingRequests += 1" in response.text
    assert "pending_message_ids" in response.text
    assert 'const title = "새 채팅"' in response.text
    assert "startRenameSession" in response.text
    assert "session-title-input" in response.text
    assert 'method: "PUT"' in response.text
    assert ".map(file => file.dataUrl)" in response.text
    assert "생각 과정" in response.text
    assert 'event.type === "reasoning"' in response.text
    assert "__MATH_BLOCK_" not in response.text
    assert "\\uE000MATH" in response.text
    assert "splitThinkTaggedContent" in response.text
    assert 'const startTag = "<think>"' in response.text
    assert 'const endTag = "</think>"' in response.text
    assert 'data-tab="run"' not in response.text
    assert 'id="run-tab"' not in response.text
    assert 'id="cluster-link"' in response.text
    assert 'state.permissions.includes("cluster:manage")' in response.text


def test_exodus_logo_asset_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    response = client.get("/assets/exodus-logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")


def test_cluster_control_requires_admin_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agentic"
    monkeypatch.setenv("AGENTIC_HOME", str(home))
    monkeypatch.setenv("AGENTIC_CLUSTER_CONTROL_URL", "http://cluster.test:52415")
    monkeypatch.delenv("EXO_TESTS", raising=False)

    from exo_tools.agent_core.auth import AuthService

    auth = AuthService(home / "auth")
    auth.create_user("admin", "admin-password-123", "admin")
    auth.create_user("regular", "regular-password-123", "user")

    admin_client = TestClient(create_app())
    assert admin_client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-password-123"},
    ).status_code == 200
    allowed = admin_client.get("/cluster-control", follow_redirects=False)
    assert allowed.status_code == 302
    assert allowed.headers["location"] == "http://cluster.test:52415/"

    user_client = TestClient(create_app())
    assert user_client.post(
        "/auth/login",
        json={"username": "regular", "password": "regular-password-123"},
    ).status_code == 200
    assert user_client.get("/cluster-control", follow_redirects=False).status_code == 403


def test_run_api_is_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    paths = client.get("/openapi.json").json()["paths"]

    assert not any("/runs" in path for path in paths)


def test_delete_session_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())
    session_id = client.post("/sessions", json={"title": "delete api"}).json()[
        "session_id"
    ]

    response = client.delete(f"/sessions/{session_id}")

    assert response.json() == {"ok": True}
    assert client.get(f"/sessions/{session_id}").status_code == 404


def test_stream_api_uses_reasoning_content_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))

    def fake_stream_events(*_args, **_kwargs):
        yield {"type": "reasoning", "delta": "inspect"}
        yield {"type": "content", "delta": "answer"}

    monkeypatch.setattr(
        "exo_tools.agent_core.runner.AgentRunner.stream_prepared_turn",
        fake_stream_events,
    )
    client = TestClient(create_app())
    session_id = client.post("/sessions", json={"title": "stream api"}).json()[
        "session_id"
    ]

    response = client.post(
        f"/sessions/{session_id}/messages/stream",
        json={"message": "question", "reasoning_effort": "high"},
    )

    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-assistant-message-id"].startswith("msg_")
    assert response.text.splitlines() == [
        '{"type": "reasoning", "delta": "inspect"}',
        '{"type": "content", "delta": "answer"}',
    ]


def test_admin_page_exposes_master_admin_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTIC_HOME", str(tmp_path / "agentic"))
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert "관리자 (master만 생성 가능)" in response.text
    assert 'me.user.role==="master"' in response.text
    assert 'value="master"' not in response.text


def test_master_created_mixed_case_account_can_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agentic"
    monkeypatch.setenv("AGENTIC_HOME", str(home))
    monkeypatch.delenv("EXO_TESTS", raising=False)

    from exo_tools.agent_core.auth import AuthService

    auth = AuthService(home / "auth")
    auth.create_user("master", "master-password-123", "master")
    client = TestClient(create_app())

    login = client.post(
        "/auth/login",
        json={"username": "master", "password": "master-password-123"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "MixedCaseUser",
            "password": "mixed-password-123",
            "role": "user",
        },
    )
    assert created.status_code == 200
    assert created.json()["username"] == "mixedcaseuser"

    user_client = TestClient(create_app())
    user_login = user_client.post(
        "/auth/login",
        json={"username": "MixedCaseUser", "password": "mixed-password-123"},
    )
    assert user_login.status_code == 200
    assert user_login.json()["user"]["username"] == "mixedcaseuser"


def test_master_account_cannot_be_created_from_admin_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agentic"
    monkeypatch.setenv("AGENTIC_HOME", str(home))
    monkeypatch.delenv("EXO_TESTS", raising=False)

    from exo_tools.agent_core.auth import AuthService

    auth = AuthService(home / "auth")
    auth.create_user("master", "master-password-123", "master")
    client = TestClient(create_app())
    login = client.post(
        "/auth/login",
        json={"username": "master", "password": "master-password-123"},
    )
    csrf = login.json()["csrf_token"]

    rejected = client.post(
        "/admin/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "another-master",
            "password": "another-password-123",
            "role": "master",
        },
    )

    assert rejected.status_code == 400

# type: ignore
from __future__ import annotations

from pathlib import Path

import pytest
from exo_tools.agent_core.auth import AuthService
from exo_tools.agent_server import create_app
from fastapi.testclient import TestClient


def test_role_hierarchy_and_permission_overrides(tmp_path: Path) -> None:
    auth = AuthService(tmp_path / "auth")
    master = auth.create_user("master", "master-password-123", "master")
    admin = auth.create_user("admin", "admin-password-123", "admin")
    user = auth.create_user("regular", "regular-password-123", "user")

    assert "system:manage" in auth.permissions(master)
    assert "cluster:manage" in auth.permissions(master)
    assert "cluster:manage" in auth.permissions(admin)
    assert "cluster:manage" not in auth.permissions(user)
    assert "users:create" in auth.permissions(admin)
    assert "users:create" not in auth.permissions(user)

    auth.set_permission(admin, user.user_id, "users:read", True)
    assert "users:read" in auth.permissions(user)

    with pytest.raises(PermissionError):
        auth.set_role(admin, master.user_id, "user")


def test_authenticate_normalizes_created_usernames(tmp_path: Path) -> None:
    auth = AuthService(tmp_path / "auth")
    created = auth.create_user("MixedCaseUser", "mixed-password-123", "user")

    assert created.username == "mixedcaseuser"
    assert auth.authenticate("MixedCaseUser", "mixed-password-123") == created
    assert auth.authenticate(" mixedcaseuser ", "mixed-password-123") == created


def test_login_csrf_and_session_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "agentic"
    monkeypatch.setenv("AGENTIC_HOME", str(home))
    monkeypatch.delenv("EXO_TESTS", raising=False)
    auth = AuthService(home / "auth")
    auth.create_user("master", "master-password-123", "master")
    auth.create_user("regular", "regular-password-123", "user")
    client = TestClient(create_app())

    login = client.post("/auth/login", json={"username": "regular", "password": "regular-password-123"})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    rejected = client.post("/sessions", json={"title": "missing csrf"})
    assert rejected.status_code == 403

    created = client.post(
        "/sessions",
        headers={"X-CSRF-Token": csrf},
        json={"title": "owned session"},
    )
    assert created.status_code == 200
    assert created.json()["metadata"]["owner_user_id"] > 0

    other_client = TestClient(create_app())
    other_login = other_client.post(
        "/auth/login",
        json={"username": "master", "password": "master-password-123"},
    )
    other_csrf = other_login.json()["csrf_token"]
    other_session = other_client.post(
        "/sessions",
        headers={"X-CSRF-Token": other_csrf},
        json={"title": "master session"},
    )
    hidden = client.get(f"/sessions/{other_session.json()['session_id']}")
    assert hidden.status_code == 404

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from exo_tools.accounts import bootstrap_accounts, create_account
from exo_tools.agent_core.auth import AuthService


def test_bootstrap_accounts_creates_credentials(tmp_path: Path) -> None:
    home = tmp_path / "agentic"

    result = bootstrap_accounts(home=home)

    auth = AuthService(home / "auth")
    assert auth.authenticate("master", result.master_password) == result.master
    assert auth.authenticate("admin", result.admin_password) == result.admin
    assert stat.S_IMODE(result.credentials_path.stat().st_mode) == 0o600

    with pytest.raises(RuntimeError, match="accounts already exist"):
        bootstrap_accounts(home=home)


def test_create_account_enforces_role_hierarchy(tmp_path: Path) -> None:
    home = tmp_path / "agentic"
    result = bootstrap_accounts(home=home)

    user = create_account(
        "regular",
        "regular-password-123",
        actor_username="admin",
        actor_password=result.admin_password,
        home=home,
    )
    created_admin = create_account(
        "operator",
        "operator-password-123",
        "admin",
        actor_username="master",
        actor_password=result.master_password,
        home=home,
    )

    assert user.role == "user"
    assert created_admin.role == "admin"

    with pytest.raises(PermissionError, match="only master"):
        create_account(
            "forbidden-admin",
            "forbidden-password-123",
            "admin",
            actor_username="admin",
            actor_password=result.admin_password,
            home=home,
        )


def test_create_account_rejects_invalid_actor_and_duplicate_username(
    tmp_path: Path,
) -> None:
    home = tmp_path / "agentic"
    result = bootstrap_accounts(home=home)

    with pytest.raises(PermissionError, match="invalid actor credentials"):
        create_account(
            "regular",
            "regular-password-123",
            actor_username="master",
            actor_password="wrong-password",
            home=home,
        )

    with pytest.raises(ValueError, match="username already exists"):
        create_account(
            "admin",
            "another-password-123",
            actor_username="master",
            actor_password=result.master_password,
            home=home,
        )

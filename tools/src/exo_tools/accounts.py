"""Public Python API for Agentic Local account creation."""

from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from exo_tools.agent_core.auth import AuthService, AuthUser

CreatableRole = Literal["user", "admin"]


@dataclass(frozen=True)
class BootstrapResult:
    """Credentials created while initializing an empty account database."""

    master: AuthUser
    admin: AuthUser
    credentials_path: Path
    master_password: str = field(repr=False)
    admin_password: str = field(repr=False)


def create_account(
    username: str,
    password: str,
    role: CreatableRole = "user",
    *,
    actor_username: str,
    actor_password: str,
    home: Path | str | None = None,
) -> AuthUser:
    """Create an account after authenticating an authorized local actor.

    Admin actors can create user accounts. Master actors can create user and
    admin accounts. Master accounts can only be created by ``bootstrap_accounts``.

    Raises:
        PermissionError: Actor authentication or authorization failed.
        ValueError: Account input is invalid or the username already exists.
    """

    if role not in ("user", "admin"):
        raise ValueError("role must be user or admin")

    auth = _auth_service(home)
    actor = auth.authenticate(actor_username, actor_password)
    if actor is None:
        raise PermissionError("invalid actor credentials")
    auth.require(actor, "users:create")
    if role == "admin" and actor.role != "master":
        raise PermissionError("only master can create admins")

    try:
        return auth.create_user(username, password, role)
    except sqlite3.IntegrityError as error:
        raise ValueError(f"username already exists: {username}") from error


def bootstrap_accounts(*, home: Path | str | None = None) -> BootstrapResult:
    """Create the initial master and admin accounts in an empty database.

    Random temporary passwords are written to
    ``<AGENTIC_HOME>/auth/bootstrap-credentials.txt`` with mode ``0600``.

    Raises:
        RuntimeError: One or more accounts already exist.
    """

    auth = _auth_service(home)
    if auth.list_users():
        raise RuntimeError("accounts already exist; bootstrap requires an empty database")

    master_password = secrets.token_urlsafe(18)
    admin_password = secrets.token_urlsafe(18)
    master = auth.create_user("master", master_password, "master")
    admin = auth.create_user("admin", admin_password, "admin")
    credentials_path = auth.root / "bootstrap-credentials.txt"
    credentials_path.write_text(
        "Temporary credentials. Change both passwords after first login.\n"
        f"master={master_password}\n"
        f"admin={admin_password}\n",
        encoding="utf-8",
    )
    os.chmod(credentials_path, 0o600)
    return BootstrapResult(
        master=master,
        admin=admin,
        credentials_path=credentials_path,
        master_password=master_password,
        admin_password=admin_password,
    )


def _auth_service(home: Path | str | None) -> AuthService:
    resolved_home = Path(
        home if home is not None else os.environ.get("AGENTIC_HOME", "~/.agentic-local")
    ).expanduser()
    return AuthService(resolved_home / "auth")

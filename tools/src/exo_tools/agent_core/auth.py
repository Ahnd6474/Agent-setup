# type: ignore
"""Lightweight node1 authentication, sessions, roles, and permissions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

Role = Literal["user", "admin", "master"]

ROLE_RANK: dict[Role, int] = {"user": 1, "admin": 2, "master": 3}
ROLE_PERMISSIONS: dict[Role, set[str]] = {
    "user": {
        "chat:read",
        "chat:write",
        "resources:read",
        "resources:update",
        "account:password",
    },
    "admin": {
        "users:read",
        "users:create",
        "users:update",
        "permissions:update",
        "cluster:manage",
    },
    "master": {
        "admins:create",
        "admins:update",
        "system:manage",
    },
}


@dataclass(frozen=True)
class AuthUser:
    user_id: int
    username: str
    role: Role
    google_email: str | None
    disabled: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "google_email": self.google_email,
            "disabled": self.disabled,
        }


@dataclass(frozen=True)
class LoginSession:
    token: str
    csrf_token: str
    expires_at: str


class LoginRateLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            failures = self._failures[key]
            self._discard_expired(failures)
            if len(failures) >= self.max_failures:
                raise PermissionError("too many login attempts; try again later")

    def failed(self, key: str) -> None:
        with self._lock:
            failures = self._failures[key]
            self._discard_expired(failures)
            failures.append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _discard_expired(self, failures: deque[float]) -> None:
        cutoff = time.monotonic() - self.window_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()


class AuthService:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.database_path = self.root / "auth.db"
        self.init()

    def init(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash BLOB,
                    password_salt BLOB,
                    role TEXT NOT NULL CHECK(role IN ('user', 'admin', 'master')),
                    google_sub TEXT UNIQUE,
                    google_email TEXT,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS permission_overrides (
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    permission TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    PRIMARY KEY(user_id, permission)
                );
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash BLOB PRIMARY KEY,
                    user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                );
                """
            )
        os.chmod(self.database_path, 0o600)

    def create_user(self, username: str, password: str | None, role: Role = "user") -> AuthUser:
        normalized = self._validate_username(username)
        if password is None:
            password_hash, salt = None, None
        else:
            self._validate_password(password)
            salt = secrets.token_bytes(16)
            password_hash = self._password_hash(password, salt)
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users(username, password_hash, password_salt, role, created_at, password_changed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, password_hash, salt, role, now, now if password else None),
            )
            user_id = int(cursor.lastrowid)
        return self.get_user(user_id)

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        normalized = username.strip().lower()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (normalized,),
            ).fetchone()
        if row is None or row["disabled"] or row["password_hash"] is None:
            return None
        candidate = self._password_hash(password, row["password_salt"])
        if not hmac.compare_digest(candidate, row["password_hash"]):
            return None
        return self._user_from_row(row)

    def change_password(self, user_id: int, current_password: str | None, new_password: str, *, force: bool = False) -> None:
        user = self.get_user(user_id)
        with self._connect() as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE user_id = ?", (user_id,)).fetchone()
        has_password = row is not None and row["password_hash"] is not None
        if not force and has_password and (
            current_password is None or self.authenticate(user.username, current_password) is None
        ):
            raise PermissionError("current password is incorrect")
        self._validate_password(new_password)
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(new_password, salt)
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, password_salt = ?, password_changed_at = ? WHERE user_id = ?",
                (password_hash, salt, self._now(), user_id),
            )
            connection.execute("DELETE FROM login_sessions WHERE user_id = ?", (user_id,))

    def create_session(self, user_id: int, hours: int = 24) -> LoginSession:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO login_sessions(token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (self._token_hash(token), user_id, csrf_token, expires_at),
            )
        return LoginSession(token=token, csrf_token=csrf_token, expires_at=expires_at)

    def user_for_session(self, token: str | None) -> tuple[AuthUser, str] | None:
        if not token:
            return None
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.*, login_sessions.csrf_token
                FROM login_sessions
                JOIN users USING(user_id)
                WHERE token_hash = ? AND expires_at > ? AND disabled = 0
                """,
                (self._token_hash(token), now),
            ).fetchone()
        if row is None:
            return None
        return self._user_from_row(row), str(row["csrf_token"])

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM login_sessions WHERE token_hash = ?", (self._token_hash(token),))

    def get_user(self, user_id: int) -> AuthUser:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(f"user not found: {user_id}")
        return self._user_from_row(row)

    def get_user_by_google_sub(self, google_sub: str) -> AuthUser | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return self._user_from_row(row) if row is not None else None

    def list_users(self) -> list[AuthUser]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY user_id").fetchall()
        return [self._user_from_row(row) for row in rows]

    def set_role(self, actor: AuthUser, target_user_id: int, role: Role) -> AuthUser:
        target = self.get_user(target_user_id)
        if ROLE_RANK[actor.role] <= ROLE_RANK[target.role] or ROLE_RANK[actor.role] <= ROLE_RANK[role]:
            raise PermissionError("cannot manage an equal or higher role")
        with self._connect() as connection:
            connection.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, target_user_id))
        return self.get_user(target_user_id)

    def set_disabled(self, actor: AuthUser, target_user_id: int, disabled: bool) -> AuthUser:
        target = self.get_user(target_user_id)
        if ROLE_RANK[actor.role] <= ROLE_RANK[target.role]:
            raise PermissionError("cannot manage an equal or higher role")
        with self._connect() as connection:
            connection.execute("UPDATE users SET disabled = ? WHERE user_id = ?", (int(disabled), target_user_id))
            if disabled:
                connection.execute("DELETE FROM login_sessions WHERE user_id = ?", (target_user_id,))
        return self.get_user(target_user_id)

    def set_permission(self, actor: AuthUser, target_user_id: int, permission: str, allowed: bool) -> None:
        target = self.get_user(target_user_id)
        if ROLE_RANK[actor.role] <= ROLE_RANK[target.role]:
            raise PermissionError("cannot manage an equal or higher role")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO permission_overrides(user_id, permission, allowed) VALUES (?, ?, ?)
                ON CONFLICT(user_id, permission) DO UPDATE SET allowed = excluded.allowed
                """,
                (target_user_id, permission, int(allowed)),
            )

    def permissions(self, user: AuthUser) -> set[str]:
        permissions: set[str] = set()
        for role, rank in ROLE_RANK.items():
            if rank <= ROLE_RANK[user.role]:
                permissions.update(ROLE_PERMISSIONS[role])
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT permission, allowed FROM permission_overrides WHERE user_id = ?",
                (user.user_id,),
            ).fetchall()
        for row in rows:
            if row["allowed"]:
                permissions.add(str(row["permission"]))
            else:
                permissions.discard(str(row["permission"]))
        return permissions

    def require(self, user: AuthUser, permission: str) -> None:
        if permission not in self.permissions(user):
            raise PermissionError(f"permission required: {permission}")

    def create_oauth_state(self, user_id: int | None = None) -> str:
        state = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO oauth_states(state_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (self._token_hash(state), user_id, expires_at),
            )
        return state

    def consume_oauth_state(self, state: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM oauth_states WHERE state_hash = ? AND expires_at > ?",
                (self._token_hash(state), self._now()),
            ).fetchone()
            connection.execute("DELETE FROM oauth_states WHERE state_hash = ?", (self._token_hash(state),))
        if row is None:
            raise PermissionError("invalid or expired OAuth state")
        return int(row["user_id"]) if row["user_id"] is not None else None

    def link_google(self, user_id: int, google_sub: str, google_email: str) -> AuthUser:
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET google_sub = ?, google_email = ? WHERE user_id = ?",
                (google_sub, google_email, user_id),
            )
        return self.get_user(user_id)

    def create_google_user(self, google_sub: str, google_email: str) -> AuthUser:
        base = "".join(character for character in google_email.split("@", 1)[0].lower() if character.isalnum() or character in "_-")
        base = (base or "google-user")[:24]
        username = base
        suffix = 1
        while self._username_exists(username):
            suffix += 1
            username = f"{base[:20]}-{suffix}"
        user = self.create_user(username, None, "user")
        return self.link_google(user.user_id, google_sub, google_email)

    def _username_exists(self, username: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @staticmethod
    def _validate_username(username: str) -> str:
        normalized = username.strip().lower()
        if not 3 <= len(normalized) <= 32:
            raise ValueError("username must be 3-32 characters")
        if not all(character.isalnum() or character in "_-" for character in normalized):
            raise ValueError("username may only contain letters, numbers, '_' and '-'")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        if len(password.encode("utf-8")) > 256:
            raise ValueError("password is too long")

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            google_email=str(row["google_email"]) if row["google_email"] is not None else None,
            disabled=bool(row["disabled"]),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

# type: ignore
"""Node1 account administration CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from exo_tools.accounts import bootstrap_accounts
from exo_tools.agent_core.auth import AuthService


def _auth() -> AuthService:
    home = Path(os.environ.get("AGENTIC_HOME", "~/.agentic-local")).expanduser()
    return AuthService(home / "auth")


def bootstrap() -> None:
    try:
        result = bootstrap_accounts()
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    print(f"created master and admin; credentials={result.credentials_path}")


def list_users() -> None:
    auth = _auth()
    for user in auth.list_users():
        print(f"{user.user_id}\t{user.username}\t{user.role}\tdisabled={str(user.disabled).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-account")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("bootstrap")
    subcommands.add_parser("list")
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "list":
        list_users()


if __name__ == "__main__":
    main()

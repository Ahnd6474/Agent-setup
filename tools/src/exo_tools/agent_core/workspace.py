# type: ignore
"""Workspace copy, diff, restore, and cleanup helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "dist",
    "build",
    ".DS_Store",
}

MODEL_SUFFIXES = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx")

DEFAULT_SANDBOX = {
    "type": "copy",
    "io_owner": "node1",
    "execution": "workspace",
    "network": "disabled_for_tools",
    "write_scope": "workspace_only",
    "max_workspace_bytes": 5_000_000_000,
    "environment_profile": "full",
    "create_venv": True,
    "install_packages": False,
}


class WorkspaceManager:
    def __init__(self, excludes: set[str] | None = None) -> None:
        self.excludes = excludes or DEFAULT_EXCLUDES

    def prepare(
        self,
        source_dir: Path | str,
        workspace_dir: Path | str,
        *,
        max_workspace_bytes: int | None = None,
    ) -> Path:
        source = Path(source_dir).expanduser().resolve()
        workspace = Path(workspace_dir).expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"source_dir is not a directory: {source}")
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, workspace, ignore=self._ignore)
        limit = max_workspace_bytes or int(DEFAULT_SANDBOX["max_workspace_bytes"])
        size = self.size_bytes(workspace)
        if size > limit:
            shutil.rmtree(workspace)
            raise RuntimeError(f"workspace size {size} exceeds limit {limit}")
        self._init_baseline_git(workspace)
        return workspace

    def diff(self, workspace_dir: Path | str) -> str:
        workspace = Path(workspace_dir).expanduser().resolve()
        result = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff"],
            cwd=workspace,
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout

    def restore(self, target_dir: Path | str, patch: str) -> None:
        target = Path(target_dir).expanduser().resolve()
        if not target.is_dir():
            raise FileNotFoundError(f"target_dir is not a directory: {target}")
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            cwd=target,
            check=True,
            text=True,
            capture_output=True,
        )

    def cleanup(self, workspace_dir: Path | str) -> None:
        workspace = Path(workspace_dir).expanduser().resolve()
        if workspace.exists():
            shutil.rmtree(workspace)

    def sandbox_profile(self, overrides: dict | None = None) -> dict:
        return {**DEFAULT_SANDBOX, **(overrides or {})}

    @staticmethod
    def size_bytes(path: Path) -> int:
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
        return total

    def _ignore(self, _dir: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in self.excludes or name.endswith(MODEL_SUFFIXES):
                ignored.add(name)
        return ignored

    @staticmethod
    def _init_baseline_git(workspace: Path) -> None:
        subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "config", "user.name", "agentic-local"], cwd=workspace, check=True)
        subprocess.run(["git", "config", "user.email", "agentic-local@example.invalid"], cwd=workspace, check=True)
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"],
            cwd=workspace,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

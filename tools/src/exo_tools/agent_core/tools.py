# type: ignore
"""Tool execution layer exposed to the local LLM agent loop."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


class ToolError(RuntimeError):
    pass


class AgentTools:
    def __init__(
        self,
        root: Path | str,
        *,
        command_timeout_s: int = 120,
        max_output_bytes: int = 64_000,
        env: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.command_timeout_s = command_timeout_s
        self.max_output_bytes = max_output_bytes
        self.env = env or {}

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "list_files":
            return {"files": self.list_files(args.get("path", "."))}
        if tool == "read_file":
            return {"content": self.read_file(args["path"], int(args.get("max_bytes", 64_000)))}
        if tool == "search":
            return {"matches": self.search(args["query"], args.get("path", "."))}
        if tool == "run_command":
            return self.run_command(args["command"], int(args.get("timeout_seconds", self.command_timeout_s)))
        if tool == "apply_patch":
            self.apply_patch(args["patch"])
            return {"ok": True}
        if tool == "finish":
            return {"final": args.get("message", "")}
        raise ToolError(f"unknown tool: {tool}")

    def list_files(self, path: str = ".") -> list[str]:
        target = self._safe_path(path)
        if target.is_file():
            return [str(target.relative_to(self.root))]
        result: list[str] = []
        for item in sorted(target.rglob("*")):
            if item.is_file() and ".git" not in item.parts:
                result.append(str(item.relative_to(self.root)))
        return result[:2000]

    def read_file(self, path: str, max_bytes: int = 64_000) -> str:
        target = self._safe_path(path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        data = target.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def search(self, query: str, path: str = ".") -> list[str]:
        target = self._safe_path(path)
        cmd = ["rg", "-n", "--hidden", "-g", "!.git", query, str(target)]
        try:
            proc = subprocess.run(cmd, cwd=self.root, text=True, capture_output=True, timeout=30, check=False)
            output = proc.stdout
        except FileNotFoundError:
            output = self._python_search(query, target)
        return output[: self.max_output_bytes].splitlines()

    def run_command(self, command: str, timeout_seconds: int) -> dict[str, Any]:
        proc = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=min(timeout_seconds, self.command_timeout_s),
            env={**os.environ, **self.env, "AGENTIC_WORKSPACE": str(self.root)},
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout[: self.max_output_bytes],
            "stderr": proc.stderr[: self.max_output_bytes],
        }

    def apply_patch(self, patch: str) -> None:
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=patch,
            text=True,
            capture_output=True,
            check=True,
        )

    def _safe_path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolError(f"path escapes workspace: {path}")
        return target

    @staticmethod
    def _python_search(query: str, target: Path) -> str:
        lines: list[str] = []
        files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
        for path in files:
            if ".git" in path.parts:
                continue
            try:
                for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        lines.append(f"{path}:{idx}:{line}")
            except OSError:
                continue
        return "\n".join(lines)

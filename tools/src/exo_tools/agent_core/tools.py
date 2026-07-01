# type: ignore
"""Server-side tool executors for the Exodus web agent loop."""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


def _function_tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


WEB_SEARCH_TOOL = _function_tool(
    "web_search",
    (
        "Search the public web for current information. Use this when the "
        "answer depends on recent, external, or source-backed facts."
    ),
    {
        "query": {"type": "string", "description": "The web search query."},
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 8,
            "description": "Maximum number of search results to return.",
        },
    },
    ["query"],
)

LIST_FILES_TOOL = _function_tool(
    "list_files",
    "List files under an allowed workspace directory.",
    {
        "path": {
            "type": "string",
            "description": "Workspace-relative or absolute path to list.",
        },
        "max_entries": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "description": "Maximum number of entries to return.",
        },
    },
    ["path"],
)

SEARCH_FILES_TOOL = _function_tool(
    "search_files",
    "Search text in allowed workspace files using ripgrep.",
    {
        "query": {"type": "string", "description": "Text or regex to search for."},
        "path": {
            "type": "string",
            "description": "Workspace-relative or absolute directory/file to search.",
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Maximum matching lines to return.",
        },
    },
    ["query"],
)

READ_FILE_TOOL = _function_tool(
    "read_file",
    "Read a UTF-8 text file from an allowed workspace path.",
    {
        "path": {"type": "string", "description": "File path to read."},
        "max_bytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200000,
            "description": "Maximum bytes to return.",
        },
    },
    ["path"],
)

WRITE_FILE_TOOL = _function_tool(
    "write_file",
    "Create or overwrite a UTF-8 text file under an allowed workspace path.",
    {
        "path": {"type": "string", "description": "File path to write."},
        "content": {"type": "string", "description": "Full file content."},
    },
    ["path", "content"],
)

APPLY_PATCH_TOOL = _function_tool(
    "apply_patch",
    "Apply a unified diff patch inside an allowed workspace.",
    {
        "patch": {
            "type": "string",
            "description": "Unified diff content accepted by the patch command.",
        },
        "cwd": {
            "type": "string",
            "description": "Allowed workspace directory where the patch should apply.",
        },
    },
    ["patch"],
)

PYTHON_SHELL_TOOL = _function_tool(
    "python_shell",
    (
        "Run short Python code in an isolated subprocess inside an allowed "
        "workspace. Use for calculations, file transformations, tests, and "
        "document generation."
    ),
    {
        "code": {"type": "string", "description": "Python code passed to python -c."},
        "cwd": {
            "type": "string",
            "description": "Allowed workspace directory where code should run.",
        },
        "timeout_s": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60,
            "description": "Execution timeout in seconds.",
        },
    },
    ["code"],
)

DEFAULT_TOOLS: list[dict[str, Any]] = [
    WEB_SEARCH_TOOL,
    LIST_FILES_TOOL,
    SEARCH_FILES_TOOL,
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    APPLY_PATCH_TOOL,
    PYTHON_SHELL_TOOL,
]


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        css = values.get("class", "")
        if tag == "a" and "result__a" in css:
            href = values.get("href", "")
            self._current = {"title": "", "url": self._clean_url(href), "snippet": ""}
            self._capture_title = True
            return
        if self._current is not None and tag in ("a", "div") and "result__snippet" in css:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current is not None:
            self._capture_title = False
            if self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
            self._current = None
        if tag in ("a", "div") and self._capture_snippet:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        text = html.unescape(data).strip()
        if not text:
            return
        if self._capture_title:
            self._current["title"] = (self._current["title"] + " " + text).strip()
        elif self._capture_snippet:
            self._current["snippet"] = (
                self._current["snippet"] + " " + text
            ).strip()

    @staticmethod
    def _clean_url(href: str) -> str:
        parsed = urllib.parse.urlsplit(href)
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return query["uddg"][0]
        return href


class ToolExecutor:
    def __init__(self) -> None:
        self.enabled = os.environ.get("AGENTIC_TOOLS_ENABLED", "true").lower() != "false"
        self.web_search_enabled = (
            os.environ.get("AGENTIC_WEB_SEARCH_ENABLED", "true").lower() != "false"
        )
        self.write_enabled = (
            os.environ.get("AGENTIC_WRITE_TOOLS_ENABLED", "true").lower() != "false"
        )
        self.python_enabled = (
            os.environ.get("AGENTIC_PYTHON_SHELL_ENABLED", "true").lower() != "false"
        )
        configured_roots = [
            item.strip()
            for item in os.environ.get("AGENTIC_ALLOWED_SOURCE_ROOTS", "").split(",")
            if item.strip()
        ]
        if not configured_roots:
            configured_roots = [os.getcwd()]
        self.allowed_roots = [Path(root).expanduser().resolve() for root in configured_roots]
        self.sandbox_root = Path(
            os.environ.get("AGENTIC_SANDBOX_ROOT", "~/.agentic-local/workspaces")
        ).expanduser().resolve()
        self.use_macos_sandbox = (
            os.environ.get("AGENTIC_USE_MACOS_SANDBOX", "true").lower() != "false"
            and shutil.which("sandbox-exec") is not None
        )

    def specs(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        specs: list[dict[str, Any]] = []
        if self.web_search_enabled:
            specs.append(WEB_SEARCH_TOOL)
        specs.extend([LIST_FILES_TOOL, SEARCH_FILES_TOOL, READ_FILE_TOOL])
        if self.write_enabled:
            specs.extend([WRITE_FILE_TOOL, APPLY_PATCH_TOOL])
        if self.python_enabled:
            specs.append(PYTHON_SHELL_TOOL)
        return specs

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if not self.enabled:
                return {"ok": False, "error": "tools are disabled"}
            if name == "web_search" and self.web_search_enabled:
                return self.web_search(arguments)
            if name == "list_files":
                return self.list_files(arguments)
            if name == "search_files":
                return self.search_files(arguments)
            if name == "read_file":
                return self.read_file(arguments)
            if name == "write_file" and self.write_enabled:
                return self.write_file(arguments)
            if name == "apply_patch" and self.write_enabled:
                return self.apply_patch(arguments)
            if name == "python_shell" and self.python_enabled:
                return self.python_shell(arguments)
            return {"ok": False, "error": f"unknown or disabled tool: {name}"}
        except Exception as error:
            return {"ok": False, "error": str(error), "tool": name}

    def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_allowed(str(arguments.get("path") or "."))
        if not path.exists():
            return {"ok": False, "error": f"path does not exist: {path}"}
        if not path.is_dir():
            return {
                "ok": True,
                "path": self._display_path(path),
                "entries": [self._entry(path)],
            }
        max_entries = self._bounded_int(arguments.get("max_entries"), 100, 1, 500)
        entries = [self._entry(child) for child in sorted(path.iterdir())[:max_entries]]
        return {"ok": True, "path": self._display_path(path), "entries": entries}

    def search_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "")
        if not query:
            return {"ok": False, "error": "search_files.query is required"}
        path = self._resolve_allowed(str(arguments.get("path") or "."))
        max_results = self._bounded_int(arguments.get("max_results"), 50, 1, 200)
        try:
            completed = subprocess.run(
                [
                    "rg",
                    "--line-number",
                    "--no-heading",
                    "--color=never",
                    query,
                    str(path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "rg is not installed"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "search timed out"}
        return {
            "ok": completed.returncode in (0, 1),
            "query": query,
            "path": self._display_path(path),
            "matches": completed.stdout.splitlines()[:max_results],
            "stderr": completed.stderr[-2_000:],
        }

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_allowed(str(arguments.get("path") or ""))
        if not path.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        max_bytes = self._bounded_int(arguments.get("max_bytes"), 80_000, 1, 200_000)
        data = path.read_bytes()[:max_bytes]
        return {
            "ok": True,
            "path": self._display_path(path),
            "truncated": path.stat().st_size > len(data),
            "content": data.decode("utf-8", errors="replace"),
        }

    def write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_allowed(str(arguments.get("path") or ""))
        content = str(arguments.get("content") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "path": self._display_path(path),
            "bytes": len(content.encode("utf-8")),
        }

    def apply_patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        patch = str(arguments.get("patch") or "")
        if not patch.strip():
            return {"ok": False, "error": "patch is required"}
        cwd = self._resolve_allowed(str(arguments.get("cwd") or "."))
        if not cwd.is_dir():
            cwd = cwd.parent
        try:
            completed = subprocess.run(
                ["patch", "-p0"],
                input=patch,
                text=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "patch timed out"}
        return {
            "ok": completed.returncode == 0,
            "cwd": self._display_path(cwd),
            "stdout": completed.stdout[-8_000:],
            "stderr": completed.stderr[-8_000:],
            "returncode": completed.returncode,
        }

    def python_shell(self, arguments: dict[str, Any]) -> dict[str, Any]:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return {"ok": False, "error": "code is required"}
        sandbox_id = self._safe_sandbox_id(str(arguments.get("sandbox_id") or "default"))
        sandbox_dir = (self.sandbox_root / sandbox_id / "python").resolve()
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        cwd = self._resolve_under(sandbox_dir, str(arguments.get("cwd") or "."))
        cwd.mkdir(parents=True, exist_ok=True)
        timeout_s = self._bounded_int(arguments.get("timeout_s"), 20, 1, 60)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "AGENTIC_SANDBOX_DIR": str(sandbox_dir),
        }
        python_bin = os.environ.get("AGENTIC_PYTHON_BIN", sys.executable)
        command = [python_bin, "-I", "-c", code]
        if self.use_macos_sandbox:
            command = [
                "sandbox-exec",
                "-p",
                self._macos_python_profile(sandbox_dir, python_bin),
                *command,
            ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "python timed out"}
        return {
            "ok": completed.returncode == 0,
            "cwd": str(cwd.relative_to(sandbox_dir) if cwd != sandbox_dir else "."),
            "sandbox": str(sandbox_dir),
            "sandboxed": self.use_macos_sandbox,
            "stdout": completed.stdout[-20_000:],
            "stderr": completed.stderr[-20_000:],
            "returncode": completed.returncode,
        }

    def web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "web_search.query is required"}
        max_results = self._bounded_int(arguments.get("max_results"), 5, 1, 8)
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Exodus/1.0"
                )
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read(1_000_000).decode("utf-8", errors="replace")
        except Exception as error:
            return {"ok": False, "error": f"web_search failed: {error}"}
        parser = _DuckDuckGoHTMLParser()
        parser.feed(body)
        return {
            "ok": True,
            "query": query,
            "results": parser.results[:max_results],
            "source": "duckduckgo_html",
        }

    def _resolve_allowed(self, supplied: str) -> Path:
        if not supplied:
            raise ValueError("path is required")
        raw = Path(supplied).expanduser()
        if not raw.is_absolute():
            raw = self.allowed_roots[0] / raw
        resolved = raw.resolve()
        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_roots
        ):
            allowed = ", ".join(str(root) for root in self.allowed_roots)
            raise PermissionError(
                f"path is outside allowed roots: {resolved}; allowed: {allowed}"
            )
        return resolved

    def _resolve_under(self, root: Path, supplied: str) -> Path:
        raw = Path(supplied).expanduser()
        if raw.is_absolute():
            try:
                raw.relative_to(root)
            except ValueError as error:
                raise PermissionError(
                    f"path is outside python sandbox: {raw}; sandbox: {root}"
                ) from error
            resolved = raw.resolve()
        else:
            resolved = (root / raw).resolve()
        if not (resolved == root or root in resolved.parents):
            raise PermissionError(
                f"path is outside python sandbox: {resolved}; sandbox: {root}"
            )
        return resolved

    @staticmethod
    def _safe_sandbox_id(value: str) -> str:
        cleaned = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in value
        ).strip("._-")
        return cleaned[:80] or "default"

    def _macos_python_profile(self, sandbox_dir: Path, python_bin: str) -> str:
        del python_bin
        sandbox = self._sandbox_quote(str(sandbox_dir))
        return f"""
(version 1)
(deny default)
(allow process*)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read-metadata)
(allow file-read*)
(allow file-write*
  (subpath "{sandbox}")
  (literal "/dev/null"))
"""

    @staticmethod
    def _sandbox_quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _display_path(self, path: Path) -> str:
        resolved = path.resolve()
        for root in self.allowed_roots:
            if resolved == root:
                return "."
            if root in resolved.parents:
                return str(resolved.relative_to(root))
        return str(resolved)

    def _entry(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": self._display_path(path),
            "type": "dir" if path.is_dir() else "file",
            "bytes": stat.st_size,
        }

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))


def tool_result_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

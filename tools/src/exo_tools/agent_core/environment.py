# type: ignore
"""Sandbox environment profiles for agentic workspaces."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ENV_PROFILES: dict[str, dict[str, Any]] = {
    "coding": {
        "python_packages": ["pytest", "ruff", "mypy", "httpx", "pydantic"],
        "system_tools": ["git", "rg", "python3", "curl"],
        "description": "General coding, testing, debugging, and repo inspection.",
    },
    "document": {
        "python_packages": [
            "python-docx",
            "pypdf",
            "pdfplumber",
            "reportlab",
            "openpyxl",
            "python-pptx",
            "pillow",
            "markdown",
            "beautifulsoup4",
            "lxml",
        ],
        "system_tools": ["git", "rg", "python3", "curl", "pdftotext", "pandoc", "tesseract"],
        "description": "Document parsing, Office/PDF generation, markdown, OCR-capable workflows.",
    },
    "ocr": {
        "python_packages": [
            "pillow",
            "pytesseract",
            "opencv-python-headless",
            "pdf2image",
            "numpy",
        ],
        "system_tools": ["git", "rg", "python3", "curl", "tesseract", "pdftoppm", "pdftotext"],
        "description": "OCR for scanned PDFs and images. Requires system tesseract for best results.",
    },
    "korean_document": {
        "python_packages": [
            "jakal-hwpx",
            "hwp-hwpx-parser",
            "python-hwpx",
            "olefile",
            "beautifulsoup4",
            "lxml",
            "python-docx",
            "pypdf",
            "pdfplumber",
            "pillow",
            "pytesseract",
        ],
        "system_tools": ["git", "rg", "python3", "curl", "tesseract", "pdftotext", "pdftoppm"],
        "description": "Korean HWP/HWPX document parsing with jakal-hwpx as the primary processor plus OCR fallback.",
    },
    "research": {
        "python_packages": ["httpx", "beautifulsoup4", "lxml", "markdownify", "duckduckgo-search"],
        "system_tools": ["git", "rg", "python3", "curl"],
        "description": "Local search, web fetch helpers, and lightweight source collection.",
    },
}

ENV_PROFILES["full"] = {
    "python_packages": sorted(
        set(ENV_PROFILES["coding"]["python_packages"])
        | set(ENV_PROFILES["document"]["python_packages"])
        | set(ENV_PROFILES["ocr"]["python_packages"])
        | set(ENV_PROFILES["korean_document"]["python_packages"])
        | set(ENV_PROFILES["research"]["python_packages"])
    ),
    "system_tools": sorted(
        set(ENV_PROFILES["coding"]["system_tools"])
        | set(ENV_PROFILES["document"]["system_tools"])
        | set(ENV_PROFILES["ocr"]["system_tools"])
        | set(ENV_PROFILES["korean_document"]["system_tools"])
        | set(ENV_PROFILES["research"]["system_tools"])
    ),
    "description": "Default all-purpose coding, document, OCR, Korean document, and search environment.",
}


class EnvironmentManager:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self.envs_dir = self.root / "envs"

    def prepare(
        self,
        *,
        profile_name: str,
        workspace: Path,
        install_packages: bool = False,
        create_venv: bool = True,
    ) -> dict[str, Any]:
        profile = self.profile(profile_name)
        env_dir = self.envs_dir / profile_name
        workspace_meta = workspace / ".agentic"
        workspace_meta.mkdir(parents=True, exist_ok=True)

        requirements_path = workspace_meta / f"requirements-{profile_name}.txt"
        requirements_path.write_text("\n".join(profile["python_packages"]) + "\n", encoding="utf-8")

        if create_venv:
            self._ensure_venv(env_dir)
            if install_packages and profile["python_packages"]:
                subprocess.run(
                    [str(env_dir / "bin" / "python"), "-m", "pip", "install", "-r", str(requirements_path)],
                    check=True,
                )

        tool_inventory = self.tool_inventory(profile)
        info = {
            "profile": profile_name,
            "mode": "shared_venv",
            "venv_path": str(env_dir),
            "requirements_path": str(requirements_path),
            "install_packages": install_packages,
            "create_venv": create_venv,
            "python_packages": profile["python_packages"],
            "system_tools": tool_inventory,
            "env": self.execution_env(env_dir if create_venv else None),
        }
        (workspace_meta / "environment.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return info

    def profile(self, profile_name: str) -> dict[str, Any]:
        if profile_name not in ENV_PROFILES:
            raise ValueError(f"unknown environment profile: {profile_name}")
        return ENV_PROFILES[profile_name]

    def execution_env(self, env_dir: Path | None) -> dict[str, str]:
        env = {
            "AGENTIC_ENV_ROOT": str(self.envs_dir),
            "PYTHONNOUSERSITE": "1",
        }
        if env_dir is not None:
            env["VIRTUAL_ENV"] = str(env_dir)
            env["PATH"] = str(env_dir / "bin") + os.pathsep + os.environ.get("PATH", "")
        return env

    def tool_inventory(self, profile: dict[str, Any]) -> dict[str, str | None]:
        return {tool: shutil.which(tool) for tool in profile["system_tools"]}

    @staticmethod
    def _ensure_venv(env_dir: Path) -> None:
        python = env_dir / "bin" / "python"
        if python.exists():
            return
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["python3", "-m", "venv", str(env_dir)], check=True)

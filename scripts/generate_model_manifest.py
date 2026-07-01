#!/usr/bin/env python3
"""Record local model/cache contents without copying oversized weights."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def repo_id(relative_path: Path) -> str | None:
    top = relative_path.parts[0] if relative_path.parts else ""
    if "--" not in top:
        return None
    owner, name = top.split("--", 1)
    return f"{owner}/{name}"


def scan(root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        relative = path.relative_to(root)
        total_bytes += stat.st_size
        files.append(
            {
                "path": relative.as_posix(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
                "repository": repo_id(relative),
                "incomplete": path.suffix == ".aria2" or "incomplete" in path.name,
            }
        )
    return {
        "source": str(root),
        "total_bytes": total_bytes,
        "file_count": len(files),
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Weights remain in external/local model storage because GitHub LFS has per-file limits.",
        "roots": [scan(root.expanduser().resolve()) for root in args.roots],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

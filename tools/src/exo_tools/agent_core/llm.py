# type: ignore
"""OpenAI-compatible local LLM client."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str = "x"
    timeout_s: float = 300.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("AGENTIC_LLM_BASE_URL", "http://127.0.0.1:52415/v1"),
            model=os.environ.get("AGENTIC_LLM_MODEL", "local-agentic-model"),
            api_key=os.environ.get("AGENTIC_LLM_API_KEY", "x"),
            timeout_s=float(os.environ.get("AGENTIC_LLM_TIMEOUT_S", "300")),
        )


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e

        return self._extract_content(data)

    def complete_stream(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> Iterator[str]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.config.timeout_s)
            with resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta") or {}
                                content = delta.get("content")
                                if content:
                                    yield content
                        except Exception:
                            continue
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e


    def backend_info(self) -> dict[str, object]:
        return {
            "role": "agentic_llm_backend",
            "base_url": self.config.base_url,
            "model": self.config.model,
            "server_node": "node1",
            "inference_nodes": ["node2", "node3", "node4"],
            "routing": "node1_api_io_with_worker_inference",
        }

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("LLM response has no message.content")
        return content

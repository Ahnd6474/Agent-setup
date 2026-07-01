# type: ignore
"""OpenAI-compatible local LLM client."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator, Literal

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
REASONING_BUDGET_TOKENS: dict[ReasoningEffort, int] = {
    "none": 0,
    "minimal": 512,
    "low": 1_024,
    "medium": 4_096,
    "high": 8_192,
    "xhigh": -1,
}


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

    @classmethod
    def from_title_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get(
                "AGENTIC_TITLE_LLM_BASE_URL",
                os.environ.get("AGENTIC_LLM_BASE_URL", "http://127.0.0.1:52415/v1"),
            ),
            model=os.environ.get(
                "AGENTIC_TITLE_LLM_MODEL",
                "mlx-community/Llama-3.2-1B-Instruct-4bit",
            ),
            api_key=os.environ.get(
                "AGENTIC_TITLE_LLM_API_KEY",
                os.environ.get("AGENTIC_LLM_API_KEY", "x"),
            ),
            timeout_s=float(os.environ.get("AGENTIC_TITLE_LLM_TIMEOUT_S", "10")),
        )


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model or self.config.model,
            "messages": messages,
            "stream": False,
            **({"temperature": temperature} if temperature is not None else {}),
            **({"tools": tools} if tools else {}),
            **self._reasoning_payload(reasoning_effort),
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

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str]:
        for event in self.complete_stream_events(
            messages,
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tools=tools,
        ):
            if event["type"] == "content":
                yield event["delta"]

    def complete_stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model or self.config.model,
            "messages": messages,
            "stream": True,
            **({"temperature": temperature} if temperature is not None else {}),
            **({"tools": tools} if tools else {}),
            **self._reasoning_payload(reasoning_effort),
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
            tool_calls: dict[int, dict[str, Any]] = {}
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
                                reasoning = delta.get("reasoning_content")
                                if reasoning:
                                    yield {
                                        "type": "reasoning",
                                        "delta": reasoning,
                                    }
                                content = delta.get("content")
                                if content:
                                    yield {"type": "content", "delta": content}
                                for raw_tool in delta.get("tool_calls") or []:
                                    index = int(raw_tool.get("index") or 0)
                                    current = tool_calls.setdefault(
                                        index,
                                        {
                                            "id": raw_tool.get("id") or f"call_{index}",
                                            "type": raw_tool.get("type") or "function",
                                            "function": {
                                                "name": "",
                                                "arguments": "",
                                            },
                                        },
                                    )
                                    if raw_tool.get("id"):
                                        current["id"] = raw_tool["id"]
                                    if raw_tool.get("type"):
                                        current["type"] = raw_tool["type"]
                                    function = raw_tool.get("function") or {}
                                    if function.get("name"):
                                        current["function"]["name"] += function["name"]
                                    if function.get("arguments"):
                                        current["function"]["arguments"] += function[
                                            "arguments"
                                        ]
                        except Exception:
                            continue
            for index in sorted(tool_calls):
                tool_call = tool_calls[index]
                if tool_call.get("function", {}).get("name"):
                    yield {"type": "tool_call", "tool_call": tool_call}
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
    def _reasoning_payload(
        reasoning_effort: ReasoningEffort | None,
    ) -> dict[str, object]:
        if reasoning_effort is None:
            return {}
        enable_thinking = reasoning_effort != "none"
        return {
            "reasoning_effort": reasoning_effort,
            "enable_thinking": enable_thinking,
            "thinking_budget_tokens": REASONING_BUDGET_TOKENS[reasoning_effort],
            "chat_template_kwargs": {
                "enable_thinking": enable_thinking,
                "reasoning_effort": reasoning_effort,
            },
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

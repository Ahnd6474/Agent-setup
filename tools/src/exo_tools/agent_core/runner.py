# type: ignore
"""Agentic chat loop built on the shared core."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterator

from .llm import LLMClient, LLMConfig, ReasoningEffort
from .resources import ResourceManager
from .schemas import Message
from .store import AgentStore
from .tools import ToolExecutor, tool_result_json

CHAT_SYSTEM_PROMPT = """You are an agentic local assistant.
Use the provided resource and environment context when answering.
You may explain coding plans, disk/compute allocation, and local LLM operation.
Default topology: node1 owns server/API/IO/session storage, node2-node4 perform LLM inference through exo.
When tools are available, use them to inspect files, search, edit workspace files, run short Python code, and verify results. Do not assume tool results; call tools when local state, current web facts, or file contents matter.

[CRITICAL HALLUCINATION PREVENTION DIRECTIVES]
1. No Unfounded Answers (근거 없는 답변 금지): Never make claims or state facts that cannot be supported by either the provided context, local repository files, or search tool results.
2. Honest Declinature (모르면 모른다고 대답하기): If you lack direct evidence, sources, or context to answer a question, explicitly state that you do not know or do not have verified information. Do not fabricate answers.
3. Source & Context Constraints (출처/컨텍스트 기반 답변 강제): Base your responses strictly on the verified sources, documents, or context available. Cite or refer to the evidence you are using.
4. Sufficiency Verification (답변 전후 근거 충족성 검사): Before writing the answer, verify if the available sources are sufficient to answer the prompt. After drafting your response, double-check that every claim made is fully backed by the referenced sources. If any claim lacks evidence, remove it.
"""


@dataclass(frozen=True)
class PreparedChatTurn:
    session_id: str
    user_message_id: str
    assistant_message_id: str
    context: dict[str, Any] | None
    reasoning_effort: ReasoningEffort | None
    images: list[str] | None


class AgentRunner:
    def __init__(
        self,
        *,
        store: AgentStore | None = None,
        llm: LLMClient | None = None,
        resource_manager: ResourceManager | None = None,
        tool_executor: ToolExecutor | None = None,
        title_llm: LLMClient | None = None,
    ) -> None:
        self.store = store or AgentStore()
        self.llm = llm or LLMClient()
        self.title_llm = title_llm
        self.resources = resource_manager or ResourceManager(self.store)
        self.tools = tool_executor or ToolExecutor()
        self.max_tool_rounds = int(os.environ.get("AGENTIC_MAX_TOOL_ROUNDS", "4"))

    def chat(
        self,
        session_id: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        images: list[str] | None = None,
    ) -> str:
        self.resources.ensure_disk_available(session_id, len(message.encode("utf-8")))
        with self.store.transaction():
            self.store.append_message(session_id, Message(role="user", content=message), commit=False)
        self._maybe_auto_title_session(session_id, message)
        messages = self._chat_messages(session_id, context, images=images)
        with self.resources.compute_slot(session_id):
            response = self.llm.complete(messages, reasoning_effort=reasoning_effort)
        with self.store.transaction():
            self.store.append_message(session_id, Message(role="assistant", content=response), commit=False)
            self.store.commit(f"chat turn {session_id}")
        return response

    def chat_stream(
        self,
        session_id: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        images: list[str] | None = None,
    ) -> Iterator[str]:
        self.resources.ensure_disk_available(session_id, len(message.encode("utf-8")))
        with self.store.transaction():
            self.store.append_message(session_id, Message(role="user", content=message), commit=False)
        self._maybe_auto_title_session(session_id, message)
        messages = self._chat_messages(session_id, context, images=images)
        full_response = []
        with self.resources.compute_slot(session_id):
            for chunk in self.llm.complete_stream(
                messages, reasoning_effort=reasoning_effort
            ):
                full_response.append(chunk)
                yield chunk
        response_str = "".join(full_response)
        with self.store.transaction():
            self.store.append_message(session_id, Message(role="assistant", content=response_str), commit=False)
            self.store.commit(f"chat turn {session_id}")

    def chat_stream_events(
        self,
        session_id: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        images: list[str] | None = None,
    ) -> Iterator[dict[str, str]]:
        turn = self.prepare_chat_turn(
            session_id,
            message,
            context=context,
            reasoning_effort=reasoning_effort,
            images=images,
        )
        yield from self.stream_prepared_turn(turn)

    def prepare_chat_turn(
        self,
        session_id: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        images: list[str] | None = None,
    ) -> PreparedChatTurn:
        self.resources.ensure_disk_available(session_id, len(message.encode("utf-8")))
        user = Message(role="user", content=message)
        assistant = Message(role="assistant", content="")
        with self.store.transaction():
            self.store.append_message(session_id, user, commit=False)
            self.store.append_message(session_id, assistant, commit=False)
            self.store.commit(f"queue chat turn {session_id}")
        self._maybe_auto_title_session(session_id, message)
        return PreparedChatTurn(
            session_id=session_id,
            user_message_id=user.message_id,
            assistant_message_id=assistant.message_id,
            context=context,
            reasoning_effort=reasoning_effort,
            images=images,
        )

    def stream_prepared_turn(
        self,
        turn: PreparedChatTurn,
    ) -> Iterator[dict[str, str]]:
        session = self.store.get_session(turn.session_id)
        model = session.metadata.get("model")
        messages = self._chat_messages(
            turn.session_id,
            turn.context,
            images=turn.images,
            through_message_id=turn.user_message_id,
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        last_persisted_at = 0.0

        def persist() -> None:
            self.store.update_message(
                turn.session_id,
                turn.assistant_message_id,
                content="".join(content_parts),
                reasoning="".join(reasoning_parts) or None,
            )

        try:
            with self.resources.compute_slot(turn.session_id):
                tool_specs = self.tools.specs()
                for round_index in range(self.max_tool_rounds + 1):
                    round_content_parts: list[str] = []
                    round_reasoning_parts: list[str] = []
                    tool_calls: list[dict[str, Any]] = []
                    kwargs = {
                        "reasoning_effort": turn.reasoning_effort,
                        "tools": tool_specs,
                    }
                    if model is not None:
                        kwargs["model"] = model
                    for event in self.llm.complete_stream_events(messages, **kwargs):
                        if event["type"] == "reasoning":
                            delta = str(event.get("delta") or "")
                            reasoning_parts.append(delta)
                            round_reasoning_parts.append(delta)
                            yield {"type": "reasoning", "delta": delta}
                        elif event["type"] == "content":
                            delta = str(event.get("delta") or "")
                            content_parts.append(delta)
                            round_content_parts.append(delta)
                            yield {"type": "content", "delta": delta}
                        elif event["type"] == "tool_call":
                            tool_calls.append(event["tool_call"])
                        now = time.monotonic()
                        if now - last_persisted_at >= 0.25:
                            persist()
                            last_persisted_at = now

                    if not tool_calls:
                        break
                    if round_index >= self.max_tool_rounds:
                        content_parts.append(
                            "\n\n도구 호출 한도에 도달해 작업을 중단했습니다."
                        )
                        break

                    assistant_tool_message: dict[str, Any] = {
                        "role": "assistant",
                        "content": "".join(round_content_parts),
                        "tool_calls": tool_calls,
                    }
                    if round_reasoning_parts:
                        assistant_tool_message["reasoning_content"] = "".join(
                            round_reasoning_parts
                        )
                    messages.append(assistant_tool_message)

                    for tool_call in tool_calls:
                        name = str(
                            tool_call.get("function", {}).get("name") or ""
                        )
                        arguments = self._tool_arguments(tool_call)
                        if name == "python_shell":
                            arguments.setdefault("sandbox_id", turn.session_id)
                        result = self.tools.execute(name, arguments)
                        yield {
                            "type": "tool",
                            "delta": f"{name}: {'ok' if result.get('ok') else 'error'}",
                        }
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id"),
                                "name": name,
                                "content": tool_result_json(result),
                            }
                        )
        except Exception as error:
            error_message = str(error)[:1_000] or type(error).__name__
            if not content_parts:
                content_parts.append(f"오류: {error_message}")
            persist()
            yield {"type": "error", "delta": error_message}
        finally:
            persist()
            self.store.commit(f"chat turn {turn.session_id}")

    @staticmethod
    def _tool_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
        raw = tool_call.get("function", {}).get("arguments") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            return {"raw": str(raw)}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def _maybe_auto_title_session(
        self,
        session_id: str,
        message: str,
        *,
        commit: bool = True,
    ) -> None:
        try:
            session = self.store.get_session(session_id)
        except FileNotFoundError:
            return
        if session.metadata.get("title_user_edited"):
            return
        if session.title.strip() not in {"Local session", "Untitled session", "새 채팅"}:
            return
        title = self._generate_session_title(message)
        if not title:
            return
        self.store.rename_session(
            session_id,
            title,
            user_edited=False,
            commit=commit,
        )

    def _generate_session_title(self, message: str) -> str:
        clean_message = self._plain_title_text(message)
        if not clean_message:
            return "새 채팅"
        if os.environ.get("AGENTIC_TITLE_LLM_DISABLED", "").lower() in {"1", "true", "yes"}:
            return self._fallback_title(clean_message)
        try:
            llm = self.title_llm or LLMClient(LLMConfig.from_title_env())
            generated = llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "You create concise Korean chat session titles. "
                            "Return only the title. No quotes, no markdown, no explanation. "
                            "Maximum 24 Korean characters or 6 English words. "
                            "Never return generic placeholders such as Local session, "
                            "Untitled session, 새 채팅, or Chat."
                        ),
                    },
                    {
                        "role": "user",
                        "content": clean_message[:1_500],
                    },
                ],
                temperature=0.2,
                reasoning_effort="none",
            )
            title = self._sanitize_title(generated)
            if self._is_placeholder_title(title):
                return self._fallback_title(clean_message)
            return title or self._fallback_title(clean_message)
        except Exception:
            return self._fallback_title(clean_message)

    @staticmethod
    def _plain_title_text(text: str) -> str:
        lines = []
        for line in text.replace("\r", "\n").split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("--- File Attachment:") or stripped.startswith("--- End File"):
                continue
            if stripped.startswith("[Attached Image:"):
                continue
            lines.append(stripped)
            if len(" ".join(lines)) >= 1_500:
                break
        return " ".join(lines).strip()

    @classmethod
    def _fallback_title(cls, text: str) -> str:
        return cls._sanitize_title(text) or "새 채팅"

    @staticmethod
    def _sanitize_title(text: str) -> str:
        title = " ".join(str(text).strip().split())
        title = title.strip("\"'`“”‘’[](){}")
        title = title.replace("#", "").replace("*", "").strip()
        if not title:
            return ""
        return title[:60]

    @staticmethod
    def _is_placeholder_title(title: str) -> bool:
        normalized = " ".join(title.strip().lower().split())
        return normalized in {
            "local session",
            "untitled session",
            "new chat",
            "chat",
            "새 채팅",
            "채팅",
        }

    def _chat_messages(
        self,
        session_id: str,
        context: dict[str, Any] | None,
        *,
        images: list[str] | None = None,
        through_message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        allocation = self.resources.get(session_id)
        system_content = (
            CHAT_SYSTEM_PROMPT
            + "\nContext:\n"
            + json.dumps(context or {}, ensure_ascii=False)
            + "\nResource allocation:\n"
            + json.dumps(allocation.to_dict(), ensure_ascii=False)
        )
        available = self.store.list_messages(session_id)
        if through_message_id is not None:
            for index, item in enumerate(available):
                if item.message_id == through_message_id:
                    available = available[: index + 1]
                    break
            else:
                raise FileNotFoundError(
                    f"message not found: {through_message_id}"
                )
        selected: list[Message] = []
        chars = 0
        for item in reversed(available):
            size = len(item.content)
            if selected and (
                len(selected) >= allocation.memory_message_limit
                or chars + size > allocation.memory_char_limit
            ):
                break
            selected.append(item)
            chars += size
        selected.reverse()
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        for item in selected:
            if item.role not in ("user", "assistant", "tool"):
                continue
            api_message: dict[str, Any] = {
                "role": item.role,
                "content": item.content,
            }
            if item.role == "assistant" and item.reasoning:
                api_message["reasoning_content"] = item.reasoning
            messages.append(api_message)
        if images:
            for message in reversed(messages):
                if message["role"] != "user":
                    continue
                text = str(message["content"])
                message["content"] = [
                    *[
                        {
                            "type": "image_url",
                            "image_url": {"url": image},
                        }
                        for image in images
                    ],
                    {"type": "text", "text": text},
                ]
                break
        return messages

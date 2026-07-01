# type: ignore
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from exo_tools.agent_core.jobs import ChatJobManager
from exo_tools.agent_core.llm import LLMClient, LLMConfig
from exo_tools.agent_core.runner import AgentRunner
from exo_tools.agent_core.schemas import Message
from exo_tools.agent_core.store import AgentStore
from exo_tools.agent_core.tools import ToolExecutor


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0
        self.messages: list[list[dict[str, object]]] = []
        self.reasoning_efforts: list[str | None] = []

    def complete(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> str:
        del temperature, tools
        self.messages.append(messages)
        self.reasoning_efforts.append(reasoning_effort)
        response = self.responses[self.calls]
        self.calls += 1
        return response

    def complete_stream(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ):
        del temperature, tools
        self.messages.append(messages)
        self.reasoning_efforts.append(reasoning_effort)
        response = self.responses[self.calls]
        self.calls += 1
        yield response

    def complete_stream_events(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ):
        del temperature, tools
        self.messages.append(messages)
        self.reasoning_efforts.append(reasoning_effort)
        response = self.responses[self.calls]
        self.calls += 1
        yield {"type": "reasoning", "delta": "step one"}
        yield {"type": "content", "delta": response}


class QueuedLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []
        self.active = 0
        self.max_active = 0
        self.release = threading.Event()

    def complete_stream_events(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ):
        del temperature, reasoning_effort, tools
        self.calls.append(messages)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.release.wait(timeout=2)
        try:
            user_content = str(messages[-1]["content"])
            yield {"type": "content", "delta": f"answer:{user_content}"}
        finally:
            self.active -= 1


class ToolLoopLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []
        self.tools_seen: list[list[dict[str, object]] | None] = []

    def complete_stream_events(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ):
        del temperature, reasoning_effort
        self.calls.append(messages)
        self.tools_seen.append(tools)
        if len(self.calls) == 1:
            yield {
                "type": "tool_call",
                "tool_call": {
                    "id": "call_read",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "note.txt"}),
                    },
                },
            }
        else:
            tool_message = messages[-1]
            yield {
                "type": "content",
                "delta": "final from " + str(tool_message["content"]),
            }


def test_llm_client_sends_llama_reasoning_controls(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        del timeout
        captured.update(json.loads(request.data or b"{}"))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LLMClient(
        LLMConfig(base_url="http://127.0.0.1:8080/v1", model="llama-cpp")
    )

    assert (
        client.complete(
            [{"role": "user", "content": "think"}],
            reasoning_effort="high",
        )
        == "ok"
    )
    assert captured["reasoning_effort"] == "high"
    assert captured["enable_thinking"] is True
    assert captured["thinking_budget_tokens"] == 8_192
    assert captured["chat_template_kwargs"] == {
        "enable_thinking": True,
        "reasoning_effort": "high",
    }


def test_llm_client_stream_sends_tools_and_parses_tool_calls(monkeypatch) -> None:
    captured: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            chunks = [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path"',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": ':"README.md"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
            ]
            for chunk in chunks:
                yield ("data: " + json.dumps(chunk) + "\n").encode()
            yield b"data: [DONE]\n"

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        del timeout
        captured.update(json.loads(request.data or b"{}"))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LLMClient(
        LLMConfig(base_url="http://127.0.0.1:8080/v1", model="local")
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    events = list(client.complete_stream_events([{"role": "user", "content": "x"}], tools=tools))

    assert captured["tools"] == tools
    assert events == [
        {
            "type": "tool_call",
            "tool_call": {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
            },
        }
    ]


def test_store_chat_markdown_and_git_commit(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("test session")

    store.append_message(session.session_id, Message(role="user", content="hello"))

    chat = (store.session_dir(session.session_id) / "chat.md").read_text()
    assert "# test session" in chat
    assert "hello" in chat
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=store.store_dir,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert "append user message" in log


def test_store_renames_session_and_rebuilds_markdown(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("Local session")
    store.append_message(session.session_id, Message(role="user", content="hello"), commit=False)

    renamed = store.rename_session(session.session_id, "  New title  ")

    assert renamed.title == "New title"
    assert renamed.metadata["title_user_edited"] is True
    chat = (store.session_dir(session.session_id) / "chat.md").read_text()
    assert chat.startswith("# New title")
    assert "hello" in chat


def test_runner_auto_titles_placeholder_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTIC_TITLE_LLM_DISABLED", "1")
    fake_llm = FakeLLM(["answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("새 채팅")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    runner.chat(session.session_id, "분산 노드 topology 고정 이름 문제 확인")

    updated = store.get_session(session.session_id)
    assert updated.title == "분산 노드 topology 고정 이름 문제 확인"
    assert updated.metadata["title_auto_generated"] is True


def test_runner_does_not_overwrite_user_edited_title(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTIC_TITLE_LLM_DISABLED", "1")
    fake_llm = FakeLLM(["answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("새 채팅")
    store.rename_session(session.session_id, "내 제목")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    runner.chat(session.session_id, "이 내용으로 자동 제목을 만들면 안 됨")

    assert store.get_session(session.session_id).title == "내 제목"


def test_runner_rejects_generic_llm_title(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["answer"])
    title_llm = FakeLLM(["Local Session"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("새 채팅")
    runner = AgentRunner(
        store=store,
        llm=fake_llm,  # type: ignore[arg-type]
        title_llm=title_llm,  # type: ignore[arg-type]
    )

    runner.chat(session.session_id, "채팅 세션 이름 자동 생성 문제")

    assert store.get_session(session.session_id).title == "채팅 세션 이름 자동 생성 문제"


def test_chat_includes_session_memory_and_resource_allocation(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["first answer", "second answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("memory")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    runner.chat(session.session_id, "first question")
    runner.chat(session.session_id, "second question")

    second_call = fake_llm.messages[1]
    assert [item["content"] for item in second_call[1:]] == [
        "first question",
        "first answer",
        "second question",
    ]
    assert "compute_slots" in second_call[0]["content"]


def test_chat_stream_forwards_reasoning_effort(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["reasoned answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("reasoning")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    response = "".join(
        runner.chat_stream(
            session.session_id,
            "think carefully",
            reasoning_effort="high",
        )
    )

    assert response == "reasoned answer"
    assert fake_llm.reasoning_efforts == ["high"]


def test_chat_stream_forwards_images_as_multimodal_content(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["image answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("vision")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]
    image = "data:image/jpeg;base64,ZmFrZQ=="

    response = "".join(
        runner.chat_stream(
            session.session_id,
            "describe this",
            images=[image],
        )
    )

    assert response == "image answer"
    user_message = fake_llm.messages[0][-1]
    assert user_message["role"] == "user"
    assert user_message["content"] == [
        {"type": "image_url", "image_url": {"url": image}},
        {"type": "text", "text": "describe this"},
    ]


def test_chat_stream_events_persist_reasoning_separately(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["final answer"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("reasoning stream")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    events = list(
        runner.chat_stream_events(
            session.session_id,
            "solve it",
            reasoning_effort="high",
        )
    )

    assert events == [
        {"type": "reasoning", "delta": "step one"},
        {"type": "content", "delta": "final answer"},
    ]
    assistant = store.list_messages(session.session_id)[-1]
    assert assistant.content == "final answer"
    assert assistant.reasoning == "step one"


def test_agent_runner_executes_read_tool_loop(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENTIC_ALLOWED_SOURCE_ROOTS", str(tmp_path))
    (tmp_path / "note.txt").write_text("tool-visible content", encoding="utf-8")
    fake_llm = ToolLoopLLM()
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("tools")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]

    events = list(runner.chat_stream_events(session.session_id, "read note"))

    assert events[0] == {"type": "tool", "delta": "read_file: ok"}
    assert events[-1]["type"] == "content"
    assert "tool-visible content" in events[-1]["delta"]
    assert fake_llm.tools_seen[0]
    assert any(
        tool["function"]["name"] == "read_file"
        for tool in fake_llm.tools_seen[0] or []
    )
    second_call = fake_llm.calls[1]
    assert second_call[-2]["role"] == "assistant"
    assert second_call[-2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert second_call[-1]["role"] == "tool"
    assistant = store.list_messages(session.session_id)[-1]
    assert assistant.content.startswith("final from ")


def test_python_shell_runs_in_session_sandbox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTIC_ALLOWED_SOURCE_ROOTS", str(tmp_path / "source"))
    monkeypatch.setenv("AGENTIC_SANDBOX_ROOT", str(tmp_path / "sandboxes"))
    executor = ToolExecutor()

    result = executor.execute(
        "python_shell",
        {
            "sandbox_id": "session-1",
            "code": (
                "from pathlib import Path\n"
                "Path('artifact.txt').write_text('ok')\n"
                "print(Path('artifact.txt').read_text())"
            ),
        },
    )

    assert result["ok"] is True
    assert result["stdout"] == "ok\n"
    assert result["sandbox"].endswith("session-1/python")
    assert (tmp_path / "sandboxes" / "session-1" / "python" / "artifact.txt").read_text() == "ok"
    outside = executor.execute(
        "python_shell",
        {
            "sandbox_id": "session-1",
            "cwd": str(tmp_path),
            "code": "print('outside')",
        },
    )
    assert outside["ok"] is False
    assert "outside python sandbox" in outside["error"]


def test_agent_runner_assigns_python_shell_to_session_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENTIC_ALLOWED_SOURCE_ROOTS", str(tmp_path / "source"))
    monkeypatch.setenv("AGENTIC_SANDBOX_ROOT", str(tmp_path / "sandboxes"))

    class PythonToolLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_stream_events(self, messages, *, temperature=None, reasoning_effort=None, tools=None):
            del messages, temperature, reasoning_effort, tools
            self.calls += 1
            if self.calls == 1:
                yield {
                    "type": "tool_call",
                    "tool_call": {
                        "id": "call_py",
                        "type": "function",
                        "function": {
                            "name": "python_shell",
                            "arguments": json.dumps(
                                {
                                    "code": "from pathlib import Path\nPath('run.txt').write_text('ok')"
                                }
                            ),
                        },
                    },
                }
            else:
                yield {"type": "content", "delta": "done"}

    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("python sandbox")
    runner = AgentRunner(store=store, llm=PythonToolLLM())  # type: ignore[arg-type]

    events = list(runner.chat_stream_events(session.session_id, "run python"))

    assert {"type": "tool", "delta": "python_shell: ok"} in events
    assert (
        tmp_path / "sandboxes" / session.session_id / "python" / "run.txt"
    ).read_text() == "ok"


def test_interrupted_event_stream_keeps_partial_assistant(tmp_path: Path) -> None:
    fake_llm = FakeLLM(["unused"])
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("interrupted stream")
    runner = AgentRunner(store=store, llm=fake_llm)  # type: ignore[arg-type]
    stream = runner.chat_stream_events(session.session_id, "solve it")

    assert next(stream) == {"type": "reasoning", "delta": "step one"}
    stream.close()

    assistant = store.list_messages(session.session_id)[-1]
    assert assistant.role == "assistant"
    assert assistant.content == ""
    assert assistant.reasoning == "step one"


def test_background_job_continues_without_stream_subscriber(tmp_path: Path) -> None:
    queued_llm = QueuedLLM()
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("background")
    runner = AgentRunner(store=store, llm=queued_llm)  # type: ignore[arg-type]
    jobs = ChatJobManager(runner)

    job = jobs.submit(session.session_id, "keep running")
    queued_llm.release.set()
    for _ in range(100):
        if job.done:
            break
        time.sleep(0.01)

    assert job.done is True
    assistant = store.list_messages(session.session_id)[-1]
    assert assistant.message_id == job.turn.assistant_message_id
    assert assistant.content == "answer:keep running"
    assert jobs.pending_message_ids(session.session_id) == []


def test_same_session_background_jobs_are_ordered_and_persisted_immediately(
    tmp_path: Path,
) -> None:
    queued_llm = QueuedLLM()
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("queue")
    runner = AgentRunner(store=store, llm=queued_llm)  # type: ignore[arg-type]
    jobs = ChatJobManager(runner)

    first = jobs.submit(session.session_id, "first")
    second = jobs.submit(session.session_id, "second")
    queued = store.list_messages(session.session_id)
    assert [message.content for message in queued] == ["first", "", "second", ""]
    assert set(jobs.pending_message_ids(session.session_id)) == {
        first.turn.assistant_message_id,
        second.turn.assistant_message_id,
    }

    queued_llm.release.set()
    for _ in range(200):
        if first.done and second.done:
            break
        time.sleep(0.01)

    assert first.done and second.done
    assert queued_llm.max_active == 1
    assert [call[-1]["content"] for call in queued_llm.calls] == ["first", "second"]
    messages = store.list_messages(session.session_id)
    assert [message.content for message in messages] == [
        "first",
        "answer:first",
        "second",
        "answer:second",
    ]


def test_delete_session_removes_store(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("delete me")

    store.delete_session(session.session_id)

    assert not store.session_dir(session.session_id).exists()


def test_session_resource_allocation_is_persisted(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("resources")
    runner = AgentRunner(store=store, llm=FakeLLM([]))  # type: ignore[arg-type]

    allocation = runner.resources.allocate(
        session.session_id,
        {
            "compute_slots": 2,
            "disk_quota_bytes": 1_000_000,
            "memory_message_limit": 8,
        },
    )

    restored = runner.resources.get(session.session_id)
    assert allocation.compute_slots == 2
    assert restored.compute_slots == 2


def test_existing_markdown_history_is_migrated_before_new_message(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    session = store.create_session("legacy")
    chat_path = store.session_dir(session.session_id) / "chat.md"
    chat_path.write_text(
        "# legacy\n\n"
        "## user - 2026-01-01T00:00:00+00:00\n\nold question\n\n"
        "## assistant - 2026-01-01T00:00:01+00:00\n\nold answer\n\n",
        encoding="utf-8",
    )

    store.append_message(session.session_id, Message(role="user", content="new question"), commit=False)

    assert [message.content for message in store.list_messages(session.session_id)] == [
        "old question",
        "old answer",
        "new question",
    ]


def test_message_and_git_retention_compacts_original_history(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agentic")
    store.max_messages = 3
    store.max_git_commits = 4
    session = store.create_session("compact")

    for index in range(6):
        store.append_message(session.session_id, Message(role="user", content=f"message-{index}"))

    messages = store.list_messages(session.session_id)
    assert len(messages) == 3
    assert messages[0].role == "system"
    assert messages[-1].content == "message-5"
    commit_count = int(
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=store.store_dir,
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    )
    assert commit_count < store.max_git_commits

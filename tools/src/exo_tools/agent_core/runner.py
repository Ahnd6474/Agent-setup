# type: ignore
"""Agentic chat and coding loop built on the shared core."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator

from .environment import EnvironmentManager
from .llm import LLMClient
from .schemas import Message, RunRecord
from .store import AgentStore
from .tools import AgentTools
from .workspace import WorkspaceManager

CODING_SYSTEM_PROMPT = """You are a local coding agent.
You must respond with exactly one JSON object and no prose.
Available tools:
- {"tool":"list_files","args":{"path":"."}}
- {"tool":"read_file","args":{"path":"relative/path","max_bytes":64000}}
- {"tool":"search","args":{"query":"text or regex","path":"."}}
- {"tool":"run_command","args":{"command":"shell command","timeout_seconds":120}}
- {"tool":"apply_patch","args":{"patch":"git apply compatible patch"}}
- {"tool":"finish","args":{"message":"summary"}}
Use tools until the task is complete. Do not modify files except with apply_patch or run_command.
The LLM runs through node1's API, while node2-node4 are reserved for model inference.
The sandbox workspace is the only writable project area. Do not assume access to model files or caches.
The sandbox environment has predefined profiles: coding, document, research, full.
"""


CHAT_SYSTEM_PROMPT = """You are an agentic local assistant.
Use the provided resource and environment context when answering.
You may explain coding plans, disk/compute allocation, and local LLM operation.
Default topology: node1 owns server/API/IO/session storage, node2-node4 perform LLM inference through exo.
"""


class AgentRunner:
    def __init__(
        self,
        *,
        store: AgentStore | None = None,
        llm: LLMClient | None = None,
        workspace_manager: WorkspaceManager | None = None,
        environment_manager: EnvironmentManager | None = None,
    ) -> None:
        self.store = store or AgentStore()
        self.llm = llm or LLMClient()
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.environment_manager = environment_manager or EnvironmentManager(self.store.root)

    def chat(self, session_id: str, message: str, *, context: dict[str, Any] | None = None) -> str:
        self.store.append_message(session_id, Message(role="user", content=message), commit=False)
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT + "\nContext:\n" + json.dumps(context or {}, ensure_ascii=False)},
            {"role": "user", "content": message},
        ]
        response = self.llm.complete(messages)
        self.store.append_message(session_id, Message(role="assistant", content=response), commit=False)
        self.store.commit(f"chat turn {session_id}")
        return response

    def chat_stream(self, session_id: str, message: str, *, context: dict[str, Any] | None = None) -> Iterator[str]:
        self.store.append_message(session_id, Message(role="user", content=message), commit=False)
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT + "\nContext:\n" + json.dumps(context or {}, ensure_ascii=False)},
            {"role": "user", "content": message},
        ]
        full_response = []
        for chunk in self.llm.complete_stream(messages):
            full_response.append(chunk)
            yield chunk
        response_str = "".join(full_response)
        self.store.append_message(session_id, Message(role="assistant", content=response_str), commit=False)
        self.store.commit(f"chat turn {session_id}")


    def run_coding(
        self,
        *,
        session_id: str,
        prompt: str,
        source_dir: Path,
        target: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> RunRecord:
        self.store.init()
        max_iterations = int((limits or {}).get("max_tool_iterations", 40))
        command_timeout = int((limits or {}).get("timeout_seconds", 1200))
        sandbox = self.workspace_manager.sandbox_profile((target or {}).get("sandbox"))
        workspace_dir = self.store.new_workspace_dir(session_id)
        workspace = self.workspace_manager.prepare(
            source_dir,
            workspace_dir,
            max_workspace_bytes=int(sandbox["max_workspace_bytes"]),
        )
        target_info = {
            "server_node": "node1",
            "io_node": "node1",
            "llm_inference_nodes": ["node2", "node3", "node4"],
            "connect_type": (target or {}).get("connect_type", "line"),
            **(target or {}),
        }
        run = self.store.create_run(
            session_id=session_id,
            mode="coding",
            prompt=prompt,
            source_dir=source_dir,
            workspace_dir=workspace,
            target=target_info,
            limits=limits,
            sandbox=sandbox,
            llm_backend=self._llm_backend_info(),
        )
        environment = self.environment_manager.prepare(
            profile_name=str(sandbox["environment_profile"]),
            workspace=workspace,
            install_packages=bool(sandbox["install_packages"]),
            create_venv=bool(sandbox["create_venv"]),
        )
        sandbox["environment"] = environment
        run.sandbox = sandbox
        self.store.update_run(run)
        tools = AgentTools(
            workspace,
            command_timeout_s=min(command_timeout, 600),
            env=environment["env"],
        )
        messages = [
            {"role": "system", "content": CODING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            for iteration in range(max_iterations):
                content = self.llm.complete(messages)
                action = self._parse_action(content)
                self.store.append_transcript(run, {"type": "tool_call", "iteration": iteration, "action": action})
                tool_name = action["tool"]
                result = tools.execute(tool_name, action.get("args", {}))
                self.store.append_transcript(run, {"type": "tool_result", "iteration": iteration, "result": result})
                if tool_name == "finish":
                    patch = self.workspace_manager.diff(workspace)
                    self.store.complete_run(run, patch=patch)
                    return run
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool result:\n" + json.dumps(result, ensure_ascii=False),
                    }
                )
            raise RuntimeError(f"agent exceeded max_tool_iterations={max_iterations}")
        except Exception as e:
            patch = ""
            with contextlib.suppress(Exception):
                patch = self.workspace_manager.diff(workspace)
            self.store.complete_run(run, patch=patch, error=str(e))
            return run

    def restore_run(self, session_id: str, run_id: str, target_dir: Path) -> None:
        patch = self.store.read_run_artifact(session_id, run_id, "result.patch")
        self.workspace_manager.restore(target_dir, patch)

    def cleanup_run_workspace(self, session_id: str, run_id: str) -> None:
        run = RunRecord.from_dict(
            json.loads((self.store.run_dir(session_id, run_id) / "manifest.json").read_text(encoding="utf-8"))
        )
        if run.workspace_dir:
            self.workspace_manager.cleanup(run.workspace_dir)

    def _llm_backend_info(self) -> dict[str, Any]:
        backend_info = getattr(self.llm, "backend_info", None)
        if callable(backend_info):
            return backend_info()
        return {
            "role": "agentic_llm_backend",
            "server_node": "node1",
            "inference_nodes": ["node2", "node3", "node4"],
            "routing": "node1_api_io_with_worker_inference",
        }

    @staticmethod
    def _parse_action(content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
        data = json.loads(stripped)
        if not isinstance(data, dict) or "tool" not in data:
            raise ValueError("LLM must return a JSON object with a tool field")
        if "args" in data and not isinstance(data["args"], dict):
            raise ValueError("LLM tool args must be an object")
        return data

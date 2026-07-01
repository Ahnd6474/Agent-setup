# type: ignore
"""Connection-independent background chat generation jobs."""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator

from .llm import ReasoningEffort
from .runner import AgentRunner, PreparedChatTurn
from .schemas import new_id


@dataclass
class ChatJob:
    job_id: str
    turn: PreparedChatTurn
    events: list[dict[str, str]] = field(default_factory=list)
    done: bool = False
    _condition: threading.Condition = field(
        default_factory=threading.Condition,
        repr=False,
    )

    def publish(self, event: dict[str, str]) -> None:
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def finish(self) -> None:
        with self._condition:
            self.done = True
            self._condition.notify_all()

    def stream(self) -> Iterator[dict[str, str]]:
        index = 0
        while True:
            heartbeat = False
            with self._condition:
                while index >= len(self.events) and not self.done:
                    if not self._condition.wait(timeout=15):
                        heartbeat = True
                        break
                buffered = self.events[index:]
                index = len(self.events)
                done = self.done
            if heartbeat and not buffered and not done:
                yield {"type": "heartbeat", "delta": ""}
            yield from buffered
            if done and index >= len(self.events):
                return


class ChatJobManager:
    """Accept turns immediately and process each session in submission order."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner
        self._lock = threading.RLock()
        self._queues: dict[str, queue.Queue[ChatJob]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._pending: dict[str, set[str]] = {}
        max_inference = int(os.environ.get("AGENTIC_MAX_CONCURRENT_INFERENCE", "1"))
        self._inference_sem = threading.Semaphore(max_inference)

    def submit(
        self,
        session_id: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
        images: list[str] | None = None,
    ) -> ChatJob:
        turn = self.runner.prepare_chat_turn(
            session_id,
            message,
            context=context,
            reasoning_effort=reasoning_effort,
            images=images,
        )
        job = ChatJob(job_id=new_id("job"), turn=turn)
        with self._lock:
            work_queue = self._queues.setdefault(session_id, queue.Queue())
            self._pending.setdefault(session_id, set()).add(
                turn.assistant_message_id
            )
            work_queue.put(job)
            worker = self._workers.get(session_id)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._work_session,
                    args=(session_id, work_queue),
                    name=f"chat-{session_id}",
                    daemon=True,
                )
                self._workers[session_id] = worker
                worker.start()
        return job

    def pending_message_ids(self, session_id: str) -> list[str]:
        with self._lock:
            return sorted(self._pending.get(session_id, set()))

    def has_pending(self, session_id: str) -> bool:
        with self._lock:
            return bool(self._pending.get(session_id))

    def _work_session(
        self,
        session_id: str,
        work_queue: queue.Queue[ChatJob],
    ) -> None:
        while True:
            job = work_queue.get()
            try:
                with self._inference_sem:
                    for event in self.runner.stream_prepared_turn(job.turn):
                        job.publish(event)
            except Exception as error:
                job.publish(
                    {
                        "type": "error",
                        "delta": str(error)[:1_000] or type(error).__name__,
                    }
                )
            finally:
                job.finish()
                with self._lock:
                    pending = self._pending.get(session_id)
                    if pending is not None:
                        pending.discard(job.turn.assistant_message_id)
                        if not pending:
                            self._pending.pop(session_id, None)
                work_queue.task_done()

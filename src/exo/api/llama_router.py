"""Request-level routing for independent llama.cpp replicas.

The exo API remains the public control plane.  A replica performs an entire
request on one device, so token generation never requires cross-node
synchronisation.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from exo.api.types import ChatCompletionRequest
from exo.shared.types.common import ModelId


class LlamaReplica(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, populate_by_name=True)

    replica_id: str = Field(alias="id", min_length=1)
    node_name: str = Field(alias="node", min_length=1)
    base_url: str = Field(min_length=1)
    models: dict[str, str] = Field(min_length=1)
    max_concurrency: int = Field(default=1, gt=0)


class LlamaRouterConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    replicas: list[LlamaReplica] = Field(min_length=1)
    maximum_queue_size: int = Field(default=64, gt=0)
    queue_timeout_seconds: float = Field(default=300.0, gt=0)
    failure_cooldown_seconds: float = Field(default=10.0, ge=0)


@dataclass
class _ReplicaRuntime:
    replica: LlamaReplica
    active_requests: int = 0
    completed_requests: int = 0
    failed_requests: int = 0
    unavailable_until: float = 0.0


class LlamaQueueFullError(Exception):
    pass


class LlamaQueueTimeoutError(Exception):
    pass


class _ReplicaLease:
    def __init__(
        self,
        router: "LlamaRequestRouter",
        runtime: _ReplicaRuntime,
        waited_seconds: float,
    ) -> None:
        self.router = router
        self.runtime = runtime
        self.waited_seconds = waited_seconds
        self._released = False

    async def release(self, *, failed: bool = False) -> None:
        if self._released:
            return
        self._released = True
        await self.router.release_runtime(self.runtime, failed=failed)


class LlamaRequestRouter:
    def __init__(
        self,
        replicas: list[LlamaReplica],
        *,
        maximum_queue_size: int = 64,
        queue_timeout_seconds: float = 300.0,
        failure_cooldown_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not replicas:
            raise ValueError("at least one llama replica is required")
        if maximum_queue_size < 1:
            raise ValueError("maximum_queue_size must be positive")
        self._runtimes = [_ReplicaRuntime(replica=replica) for replica in replicas]
        self._maximum_queue_size = maximum_queue_size
        self._queue_timeout_seconds = queue_timeout_seconds
        self._failure_cooldown_seconds = failure_cooldown_seconds
        self._condition = asyncio.Condition()
        self._waiting_requests = 0
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(os.environ.get("EXO_LLAMA_REQUEST_TIMEOUT_SECONDS", "900")),
                write=60.0,
                pool=10.0,
            )
        )

    @classmethod
    def from_env(cls) -> "LlamaRequestRouter | None":
        inline_config = os.environ.get("EXO_LLAMA_REPLICAS")
        config_path = os.environ.get("EXO_LLAMA_REPLICAS_FILE")
        if inline_config is None and config_path is None:
            return None
        raw_config = (
            inline_config
            if inline_config is not None
            else Path(str(config_path)).expanduser().read_text(encoding="utf-8")
        )
        config = LlamaRouterConfig.model_validate_json(raw_config)
        return cls(
            config.replicas,
            maximum_queue_size=config.maximum_queue_size,
            queue_timeout_seconds=config.queue_timeout_seconds,
            failure_cooldown_seconds=config.failure_cooldown_seconds,
        )

    def supports(self, model: str) -> bool:
        return any(model in runtime.replica.models for runtime in self._runtimes)

    async def proxy_chat_completion(
        self, payload: ChatCompletionRequest
    ) -> Response | StreamingResponse:
        model = str(payload.model)
        lease = await self._acquire(model)
        replica = lease.runtime.replica
        upstream_payload = payload.model_copy(
            update={"model": ModelId(replica.models[model])}
        ).model_dump(mode="json", exclude_none=True)
        url = f"{replica.base_url}/chat/completions"
        request = self._client.build_request("POST", url, json=upstream_payload)

        try:
            upstream = await self._client.send(request, stream=payload.stream)
        except httpx.HTTPError as exc:
            await lease.release(failed=True)
            logger.warning(f"llama replica {replica.replica_id} request failed: {exc}")
            raise HTTPException(
                status_code=502,
                detail=f"llama replica unavailable: {replica.replica_id}",
            ) from exc

        response_headers = {
            "X-Exo-Replica": replica.replica_id,
            "X-Exo-Node": replica.node_name,
            "X-Exo-Queue-Wait-Ms": str(round(lease.waited_seconds * 1000)),
        }
        content_type = "text/event-stream" if payload.stream else "application/json"
        if not payload.stream:
            body = await upstream.aread()
            failed = upstream.status_code >= 500
            await upstream.aclose()
            await lease.release(failed=failed)
            return Response(
                content=body,
                status_code=upstream.status_code,
                media_type=content_type,
                headers=response_headers,
            )

        async def generate() -> AsyncIterator[bytes]:
            failed = upstream.status_code >= 500
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except httpx.HTTPError:
                failed = True
                raise
            finally:
                await upstream.aclose()
                await lease.release(failed=failed)

        response_headers.update(
            {
                "Cache-Control": "no-cache",
                "Connection": "close",
                "X-Accel-Buffering": "no",
            }
        )
        return StreamingResponse(
            generate(),
            status_code=upstream.status_code,
            media_type=content_type,
            headers=response_headers,
        )

    async def _acquire(self, model: str) -> _ReplicaLease:
        started = time.monotonic()
        deadline = started + self._queue_timeout_seconds
        async with self._condition:
            if self._waiting_requests >= self._maximum_queue_size:
                raise LlamaQueueFullError
            self._waiting_requests += 1
            try:
                while True:
                    now = time.monotonic()
                    candidates = [
                        runtime
                        for runtime in self._runtimes
                        if model in runtime.replica.models
                        and runtime.unavailable_until <= now
                        and runtime.active_requests < runtime.replica.max_concurrency
                    ]
                    if candidates:
                        selected = min(
                            candidates,
                            key=lambda runtime: (
                                runtime.active_requests
                                / runtime.replica.max_concurrency,
                                runtime.completed_requests,
                                runtime.replica.replica_id,
                            ),
                        )
                        selected.active_requests += 1
                        return _ReplicaLease(self, selected, time.monotonic() - started)
                    remaining = deadline - now
                    if remaining <= 0:
                        raise LlamaQueueTimeoutError
                    cooldowns = [
                        runtime.unavailable_until - now
                        for runtime in self._runtimes
                        if model in runtime.replica.models
                        and runtime.unavailable_until > now
                    ]
                    wait_seconds = min(
                        remaining,
                        min(cooldowns) if cooldowns else remaining,
                    )
                    try:
                        await asyncio.wait_for(self._condition.wait(), wait_seconds)
                    except TimeoutError:
                        continue
            finally:
                self._waiting_requests -= 1

    async def release_runtime(
        self, runtime: _ReplicaRuntime, *, failed: bool = False
    ) -> None:
        async with self._condition:
            runtime.active_requests -= 1
            if failed:
                runtime.failed_requests += 1
                runtime.unavailable_until = (
                    time.monotonic() + self._failure_cooldown_seconds
                )
            else:
                runtime.completed_requests += 1
            self._condition.notify_all()

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "enabled": True,
            "waiting_requests": self._waiting_requests,
            "maximum_queue_size": self._maximum_queue_size,
            "replicas": [
                {
                    "id": runtime.replica.replica_id,
                    "node": runtime.replica.node_name,
                    "base_url": runtime.replica.base_url,
                    "models": list(runtime.replica.models),
                    "active_requests": runtime.active_requests,
                    "max_concurrency": runtime.replica.max_concurrency,
                    "completed_requests": runtime.completed_requests,
                    "failed_requests": runtime.failed_requests,
                    "available": runtime.unavailable_until <= now,
                }
                for runtime in self._runtimes
            ],
        }

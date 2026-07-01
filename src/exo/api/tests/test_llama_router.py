import asyncio
import json

import httpx
import pytest

from exo.api.llama_router import (
    LlamaQueueFullError,
    LlamaReplica,
    LlamaRequestRouter,
)
from exo.api.types import ChatCompletionMessage, ChatCompletionRequest


def replica(replica_id: str, node: str, model: str = "model-a") -> LlamaReplica:
    return LlamaReplica(
        replica_id=replica_id,
        node_name=node,
        base_url=f"http://{node}:8080/v1",
        models={model: f"{model}-upstream"},
    )


@pytest.mark.asyncio
async def test_routes_concurrent_requests_to_different_replicas() -> None:
    arrivals: list[str] = []
    release_requests = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        arrivals.append(request.url.host or "")
        await release_requests.wait()
        return httpx.Response(
            200,
            json={"model": json.loads(request.content)["model"]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    router = LlamaRequestRouter(
        [replica("replica-2", "node2"), replica("replica-3", "node3")],
        client=client,
    )
    payload = ChatCompletionRequest(
        model="model-a",
        messages=[ChatCompletionMessage(role="user", content="hello")],
    )
    first = asyncio.create_task(router.proxy_chat_completion(payload))
    second = asyncio.create_task(router.proxy_chat_completion(payload))
    await asyncio.sleep(0)
    release_requests.set()
    first_response, second_response = await asyncio.gather(first, second)

    assert set(arrivals) == {"node2", "node3"}
    assert first_response.headers["x-exo-node"] != second_response.headers["x-exo-node"]
    assert json.loads(first_response.body)["model"] == "model-a-upstream"
    await client.aclose()


@pytest.mark.asyncio
async def test_waits_until_replica_capacity_is_released() -> None:
    router = LlamaRequestRouter([replica("replica-2", "node2")])
    first = await router._acquire("model-a")
    waiting = asyncio.create_task(router._acquire("model-a"))
    await asyncio.sleep(0.01)

    assert not waiting.done()
    assert router.status()["waiting_requests"] == 1

    await first.release()
    second = await asyncio.wait_for(waiting, 0.5)
    await second.release()
    await router._client.aclose()


@pytest.mark.asyncio
async def test_rejects_when_queue_limit_is_reached() -> None:
    router = LlamaRequestRouter(
        [replica("replica-2", "node2")],
        maximum_queue_size=1,
    )
    active = await router._acquire("model-a")
    queued = asyncio.create_task(router._acquire("model-a"))
    await asyncio.sleep(0.01)

    with pytest.raises(LlamaQueueFullError):
        await router._acquire("model-a")

    await active.release()
    queued_lease = await queued
    await queued_lease.release()
    await router._client.aclose()


def test_supports_more_than_three_replicas() -> None:
    router = LlamaRequestRouter(
        [replica(f"replica-{index}", f"node{index}") for index in range(2, 7)]
    )

    assert len(router.status()["replicas"]) == 5
    assert router.supports("model-a")

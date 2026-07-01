from __future__ import annotations

from typing import Any

import pytest

from exo.shared.types.thunderbolt import (
    ThunderboltConnection,
    ThunderboltIdentifier,
)
from exo.utils.info_gatherer import info_gatherer as module
from exo.utils.info_gatherer.info_gatherer import (
    InfoGatherer,
    MacThunderboltConnections,
    MacThunderboltIdentifiers,
)


class StopPollingError(Exception):
    pass


class RecordingSender:
    def __init__(self) -> None:
        self.items: list[Any] = []

    async def send(self, item: Any) -> None:
        self.items.append(item)


class ConnectivityDatum:
    def ident(self, ifaces: dict[str, str]) -> ThunderboltIdentifier:
        assert ifaces == {"Thunderbolt 1": "en2"}
        return ThunderboltIdentifier(
            rdma_interface="rdma_en2",
            domain_uuid="source",
            link_speed="80 Gb/s",
        )

    def conn(self) -> ThunderboltConnection:
        return ThunderboltConnection(source_uuid="source", sink_uuid="sink")


@pytest.mark.anyio
async def test_thunderbolt_monitor_sends_identifiers_and_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = RecordingSender()

    async def gather_iface_map() -> dict[str, str]:
        return {"Thunderbolt 1": "en2"}

    async def gather_connectivity() -> list[ConnectivityDatum]:
        return [ConnectivityDatum()]

    async def stop_after_first_poll(_interval: float) -> None:
        raise StopPollingError

    monkeypatch.setattr(module, "_gather_iface_map", gather_iface_map)
    monkeypatch.setattr(
        module.ThunderboltConnectivity,
        "gather",
        gather_connectivity,
    )
    monkeypatch.setattr(module.anyio, "sleep", stop_after_first_poll)

    with pytest.raises(StopPollingError):
        await InfoGatherer(sender)._monitor_system_profiler_thunderbolt_data(5)  # type: ignore[arg-type]

    assert len(sender.items) == 2
    assert isinstance(sender.items[0], MacThunderboltIdentifiers)
    assert sender.items[0].idents[0].rdma_interface == "rdma_en2"
    assert isinstance(sender.items[1], MacThunderboltConnections)
    assert sender.items[1].conns[0].sink_uuid == "sink"


@pytest.mark.anyio
async def test_thunderbolt_monitor_skips_events_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = RecordingSender()

    class SimulatedTimeoutError(Exception):
        pass

    class TimeoutScope:
        cancel_called = False

        def __enter__(self) -> TimeoutScope:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: Any,
        ) -> bool:
            if exc_type is SimulatedTimeoutError:
                self.cancel_called = True
                return True
            return False

    async def gather_iface_map() -> dict[str, str]:
        raise SimulatedTimeoutError

    async def stop_after_first_poll(_interval: float) -> None:
        raise StopPollingError

    monkeypatch.setattr(module, "_gather_iface_map", gather_iface_map)
    monkeypatch.setattr(module, "move_on_after", lambda _seconds: TimeoutScope())
    monkeypatch.setattr(module.anyio, "sleep", stop_after_first_poll)

    with pytest.raises(StopPollingError):
        await InfoGatherer(sender)._monitor_system_profiler_thunderbolt_data(5)  # type: ignore[arg-type]

    assert sender.items == []

import pytest

from mcp.server.streamable_http import EventMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest

from core.event_store import InMemoryEventStore


def _message(method: str, request_id: str | None = None) -> JSONRPCMessage:
    return JSONRPCMessage(
        root=JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id or method,
            method=method,
        )
    )


@pytest.fixture
def store() -> InMemoryEventStore:
    return InMemoryEventStore(max_events_per_stream=5)


@pytest.mark.asyncio
async def test_store_event_returns_unique_ids(store: InMemoryEventStore) -> None:
    id1 = await store.store_event("stream-1", _message("msg1"))
    id2 = await store.store_event("stream-1", _message("msg2"))
    assert id1 != id2
    assert isinstance(id1, str)
    assert isinstance(id2, str)


@pytest.mark.asyncio
async def test_store_event_none_message(store: InMemoryEventStore) -> None:
    event_id = await store.store_event("stream-1", None)
    assert isinstance(event_id, str)


@pytest.mark.asyncio
async def test_replay_events_after_returns_stream_id_and_event_messages(
    store: InMemoryEventStore,
) -> None:
    id1 = await store.store_event("stream-1", _message("msg1"))
    id2 = await store.store_event("stream-1", _message("msg2"))
    id3 = await store.store_event("stream-1", _message("msg3"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == "stream-1"
    assert [message.message.root.method for message in replayed] == ["msg2", "msg3"]
    assert [message.event_id for message in replayed] == [id2, id3]


@pytest.mark.asyncio
async def test_replay_skips_none_messages(store: InMemoryEventStore) -> None:
    id1 = await store.store_event("stream-1", _message("msg1"))
    await store.store_event("stream-1", None)
    id3 = await store.store_event("stream-1", _message("msg3"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == "stream-1"
    assert [message.message.root.method for message in replayed] == ["msg3"]
    assert [message.event_id for message in replayed] == [id3]


@pytest.mark.asyncio
async def test_replay_unknown_event_id_returns_none(store: InMemoryEventStore) -> None:
    await store.store_event("stream-1", _message("msg1"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    result = await store.replay_events_after("nonexistent-id", callback)
    assert result is None
    assert replayed == []


@pytest.mark.asyncio
async def test_replay_last_event_returns_stream_id_with_no_replay(
    store: InMemoryEventStore,
) -> None:
    id1 = await store.store_event("stream-1", _message("msg1"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == "stream-1"
    assert replayed == []


@pytest.mark.asyncio
async def test_max_events_per_stream_eviction(store: InMemoryEventStore) -> None:
    """Events beyond max_events_per_stream are evicted (oldest first)."""
    ids = []
    for i in range(7):
        event_id = await store.store_event("stream-1", _message(f"msg{i}"))
        ids.append(event_id)

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    # Both ids[0] (msg0) and ids[1] (msg1) were evicted.
    result = await store.replay_events_after(ids[1], callback)
    assert result is None

    # ids[2] (msg2) is now the oldest remaining event, so replay yields msg3..msg6.
    result = await store.replay_events_after(ids[2], callback)
    assert result == "stream-1"
    assert [message.event_id for message in replayed] == ids[3:7]
    assert [message.message.root.method for message in replayed] == [
        "msg3",
        "msg4",
        "msg5",
        "msg6",
    ]


@pytest.mark.asyncio
async def test_multiple_streams_independent(store: InMemoryEventStore) -> None:
    id_a = await store.store_event("stream-a", _message("a1"))
    await store.store_event("stream-a", _message("a2"))
    id_b = await store.store_event("stream-b", _message("b1"))
    await store.store_event("stream-b", _message("b2"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)

    result = await store.replay_events_after(id_a, callback)
    assert result == "stream-a"
    assert [message.message.root.method for message in replayed] == ["a2"]

    replayed.clear()
    result = await store.replay_events_after(id_b, callback)
    assert result == "stream-b"
    assert [message.message.root.method for message in replayed] == ["b2"]


@pytest.mark.asyncio
async def test_replay_uses_snapshot_when_callback_mutates_store(
    store: InMemoryEventStore,
) -> None:
    first_id = await store.store_event("stream-1", _message("msg1"))
    second_id = await store.store_event("stream-1", _message("msg2"))
    third_id = await store.store_event("stream-1", _message("msg3"))

    replayed: list[EventMessage] = []

    async def callback(message: EventMessage) -> None:
        replayed.append(message)
        if message.event_id == second_id:
            await store.store_event("stream-1", _message("msg4"))

    result = await store.replay_events_after(first_id, callback)
    assert result == "stream-1"
    assert [message.event_id for message in replayed] == [second_id, third_id]
    assert [message.message.root.method for message in replayed] == [
        "msg2",
        "msg3",
    ]


def test_rejects_non_positive_max_events_per_stream() -> None:
    with pytest.raises(ValueError, match="max_events_per_stream must be at least 1"):
        InMemoryEventStore(max_events_per_stream=0)

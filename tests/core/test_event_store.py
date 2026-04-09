import pytest

from core.event_store import InMemoryEventStore


@pytest.fixture
def store():
    return InMemoryEventStore(max_events_per_stream=5)


@pytest.mark.asyncio
async def test_store_event_returns_unique_ids(store):
    id1 = await store.store_event("stream-1", {"type": "msg1"})
    id2 = await store.store_event("stream-1", {"type": "msg2"})
    assert id1 != id2
    assert isinstance(id1, str)
    assert isinstance(id2, str)


@pytest.mark.asyncio
async def test_store_event_none_message(store):
    event_id = await store.store_event("stream-1", None)
    assert isinstance(event_id, str)


@pytest.mark.asyncio
async def test_replay_events_after(store):
    id1 = await store.store_event("stream-1", {"type": "msg1"})
    await store.store_event("stream-1", {"type": "msg2"})
    id3 = await store.store_event("stream-1", {"type": "msg3"})

    replayed = []

    async def callback(message):
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == id3
    assert replayed == [{"type": "msg2"}, {"type": "msg3"}]


@pytest.mark.asyncio
async def test_replay_skips_none_messages(store):
    id1 = await store.store_event("stream-1", {"type": "msg1"})
    await store.store_event("stream-1", None)
    id3 = await store.store_event("stream-1", {"type": "msg3"})

    replayed = []

    async def callback(message):
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == id3
    assert replayed == [{"type": "msg3"}]


@pytest.mark.asyncio
async def test_replay_unknown_event_id_returns_none(store):
    await store.store_event("stream-1", {"type": "msg1"})

    replayed = []

    async def callback(message):
        replayed.append(message)

    result = await store.replay_events_after("nonexistent-id", callback)
    assert result is None
    assert replayed == []


@pytest.mark.asyncio
async def test_replay_last_event_returns_id_with_no_replay(store):
    id1 = await store.store_event("stream-1", {"type": "msg1"})

    replayed = []

    async def callback(message):
        replayed.append(message)

    result = await store.replay_events_after(id1, callback)
    assert result == id1
    assert replayed == []


@pytest.mark.asyncio
async def test_max_events_per_stream_eviction(store):
    """Events beyond max_events_per_stream are evicted (oldest first)."""
    ids = []
    for i in range(7):
        eid = await store.store_event("stream-1", {"type": f"msg{i}"})
        ids.append(eid)

    # Only last 5 should remain (max_events_per_stream=5)
    replayed = []

    async def callback(message):
        replayed.append(message)

    # id[1] (msg1) was evicted, so replay from it returns None
    result = await store.replay_events_after(ids[1], callback)
    assert result is None

    # id[2] (msg2) is the oldest remaining
    result = await store.replay_events_after(ids[2], callback)
    assert result == ids[6]
    assert len(replayed) == 4  # msg3, msg4, msg5, msg6


@pytest.mark.asyncio
async def test_multiple_streams_independent(store):
    id_a = await store.store_event("stream-a", {"type": "a1"})
    await store.store_event("stream-a", {"type": "a2"})
    id_b = await store.store_event("stream-b", {"type": "b1"})
    await store.store_event("stream-b", {"type": "b2"})

    replayed = []

    async def callback(message):
        replayed.append(message)

    await store.replay_events_after(id_a, callback)
    assert replayed == [{"type": "a2"}]

    replayed.clear()
    await store.replay_events_after(id_b, callback)
    assert replayed == [{"type": "b2"}]

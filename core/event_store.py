"""In-memory EventStore for MCP session resumability."""

import logging
import uuid

from mcp.server.streamable_http import EventStore, EventCallback

logger = logging.getLogger(__name__)


class InMemoryEventStore(EventStore):
    """In-memory EventStore for single-instance deployments.

    Enables clients to reconnect and resume receiving events
    after brief disconnections using the Last-Event-ID header.

    Note: Events are lost on process restart. For distributed
    deployments, a Valkey-backed implementation would be needed.
    """

    def __init__(self, max_events_per_stream: int = 100):
        self._streams: dict[str, list[tuple[str, object]]] = {}
        self._max_events = max_events_per_stream

    async def store_event(
        self, stream_id: str, message: object | None
    ) -> str:
        event_id = str(uuid.uuid4())
        if stream_id not in self._streams:
            self._streams[stream_id] = []
        events = self._streams[stream_id]
        events.append((event_id, message))
        if len(events) > self._max_events:
            self._streams[stream_id] = events[-self._max_events :]
        return event_id

    async def replay_events_after(
        self,
        last_event_id: str,
        send_callback: EventCallback,
    ) -> str | None:
        for stream_id, events in self._streams.items():
            found = False
            for event_id, message in events:
                if found and message is not None:
                    await send_callback(message)
                if event_id == last_event_id:
                    found = True
            if found and events:
                return events[-1][0]
        return None

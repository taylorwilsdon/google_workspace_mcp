"""In-memory EventStore for MCP session resumability."""

import uuid

from mcp.server.streamable_http import EventCallback, EventMessage, EventStore
from mcp.types import JSONRPCMessage


class InMemoryEventStore(EventStore):
    """In-memory EventStore for single-instance deployments.

    Enables clients to reconnect and resume receiving events
    after brief disconnections using the Last-Event-ID header.

    Note: Events are lost on process restart. For distributed
    deployments, a Valkey-backed implementation would be needed.
    """

    def __init__(self, max_events_per_stream: int = 100):
        if max_events_per_stream < 1:
            raise ValueError("max_events_per_stream must be at least 1")

        self._streams: dict[str, list[tuple[str, JSONRPCMessage | None]]] = {}
        self._max_events = max_events_per_stream

    async def store_event(
        self, stream_id: str, message: JSONRPCMessage | None
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
        for stream_id, events in tuple(self._streams.items()):
            events_snapshot = list(events)
            replay_start_index = None

            for index, (event_id, _) in enumerate(events_snapshot):
                if event_id == last_event_id:
                    replay_start_index = index + 1
                    break

            if replay_start_index is None:
                continue

            replay_messages = [
                EventMessage(message=message, event_id=event_id)
                for event_id, message in events_snapshot[replay_start_index:]
                if message is not None
            ]
            for replay_message in replay_messages:
                await send_callback(replay_message)

            return stream_id

        return None

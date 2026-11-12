"""In-memory event publisher for unit and contract testing."""

from app.events.envelope import EventEnvelope
from app.events.publisher import EventPublisher


class FakeEventPublisher(EventPublisher):
    """Captures published events in memory."""

    def __init__(self) -> None:
        self.published_events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.published_events.append(event)

    def clear(self) -> None:
        self.published_events.clear()

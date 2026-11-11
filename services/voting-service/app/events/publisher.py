"""RabbitMQ asynchronous event publisher using aio-pika."""

import json
import logging

from aio_pika import DeliveryMode, Message, connect_robust
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractExchange

from app.config import get_settings
from app.events.envelope import EventEnvelope

logger = logging.getLogger("voting_service.events")
settings = get_settings()


class EventPublisher:
    """Publishes domain event envelopes to RabbitMQ exchange."""

    def __init__(self, connection_url: str | None = None) -> None:
        self.url = connection_url or settings.RABBITMQ_URL
        self.exchange_name = settings.RABBITMQ_EXCHANGE
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        """Establish robust connection, channel, and topic exchange."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await connect_robust(self.url)
            self._channel = await self._connection.channel()
            # Declare durable topic exchange for event fan-out
            self._exchange = await self._channel.declare_exchange(
                name=self.exchange_name,
                type="topic",
                durable=True,
            )
            logger.info("Connected to RabbitMQ exchange '%s'", self.exchange_name)

    async def publish(self, event: EventEnvelope, routing_key: str | None = None) -> None:
        """Publish an event envelope as a durable JSON message.

        Routing key defaults to 'voting.<event_type.lower()>'.
        """
        await self.connect()
        assert self._exchange is not None

        key = routing_key or f"voting.{event.event_type.lower()}"
        body = json.dumps(event.model_dump(mode="json"), default=str).encode("utf-8")

        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            correlation_id=event.event_id,
            headers={
                "event_type": event.event_type,
                "game_id": event.game_id,
                "version": event.version,
            },
        )

        await self._exchange.publish(message, routing_key=key)
        logger.info(
            "Published event %s [%s] with routing_key '%s'",
            event.event_id,
            event.event_type,
            key,
        )

    async def close(self) -> None:
        """Close RabbitMQ channel and connection gracefully."""
        if self._channel is not None and not self._channel.is_closed:
            await self._channel.close()
            self._channel = None
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None


_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Retrieve or create singleton event publisher."""
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher

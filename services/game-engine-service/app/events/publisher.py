"""Event publisher interface and RabbitMQ implementation."""

from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection

from app.events.envelope import EventEnvelope


class EventPublisher(Protocol):
    """Protocol for event publishing."""

    async def publish(self, event: EventEnvelope) -> None:
        """Publish an event to the message broker."""
        ...


class RabbitMQEventPublisher:
    """RabbitMQ publisher sending JSON envelopes to an exchange."""

    def __init__(self, rabbitmq_url: str, exchange_name: str) -> None:
        self.rabbitmq_url = rabbitmq_url
        self.exchange_name = exchange_name
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(self.rabbitmq_url)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                self.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
            )

    async def publish(self, event: EventEnvelope) -> None:
        await self.connect()
        assert self._exchange is not None
        routing_key = f"game.{event.event_type.lower()}"
        message = aio_pika.Message(
            body=event.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self._exchange.publish(message, routing_key=routing_key)

    async def close(self) -> None:
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

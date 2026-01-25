"""Event emitter for publishing domain events.

The emitter provides:
- Synchronous and asynchronous publishing
- Handler registration with type filtering
- Category-based routing
- Error isolation (handler failures don't break other handlers)
- Event batching for transactions
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

from payroll_engine.psp.events.types import DomainEvent, EventCategory

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=DomainEvent)


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for synchronous event handlers."""

    def __call__(self, event: DomainEvent) -> None:
        """Handle a domain event."""
        ...


@runtime_checkable
class AsyncEventHandler(Protocol):
    """Protocol for asynchronous event handlers."""

    async def __call__(self, event: DomainEvent) -> None:
        """Handle a domain event asynchronously."""
        ...


@dataclass
class HandlerRegistration:
    """Registration of an event handler."""

    handler: EventHandler | AsyncEventHandler
    event_types: set[str] | None  # None = all events
    categories: set[EventCategory] | None  # None = all categories
    is_async: bool


class EventEmitter:
    """Synchronous event emitter.

    Publishes events to registered handlers. Handlers are isolated -
    if one fails, others still receive the event.

    Usage:
        emitter = EventEmitter()

        # Register handler for specific event type
        emitter.on(PaymentSettled, handle_settlement)

        # Register handler for category
        emitter.on_category(EventCategory.PAYMENT, log_payment_events)

        # Emit event
        emitter.emit(payment_settled_event)

        # Batch events (for transactions)
        with emitter.batch() as batch:
            batch.add(event1)
            batch.add(event2)
        # All events emitted when context exits
    """

    def __init__(self) -> None:
        self._handlers: list[HandlerRegistration] = []
        self._batching = False
        self._batch: list[DomainEvent] = []

    def on(
        self,
        event_type: type[T] | list[type[T]],
        handler: EventHandler,
    ) -> None:
        """Register handler for specific event type(s)."""
        if isinstance(event_type, list):
            types = {t.__name__ for t in event_type}
        else:
            types = {event_type.__name__}

        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=types,
                categories=None,
                is_async=False,
            )
        )

    def on_category(
        self,
        category: EventCategory | list[EventCategory],
        handler: EventHandler,
    ) -> None:
        """Register handler for event category(ies)."""
        if isinstance(category, list):
            cats = set(category)
        else:
            cats = {category}

        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=None,
                categories=cats,
                is_async=False,
            )
        )

    def on_all(self, handler: EventHandler) -> None:
        """Register handler for all events."""
        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=None,
                categories=None,
                is_async=False,
            )
        )

    def off(self, handler: EventHandler) -> None:
        """Unregister a handler."""
        self._handlers = [
            reg for reg in self._handlers if reg.handler is not handler
        ]

    def emit(self, event: DomainEvent) -> list[Exception]:
        """Emit an event to all matching handlers.

        Returns list of any exceptions raised by handlers.
        Handlers are isolated - failures don't stop other handlers.
        """
        if self._batching:
            self._batch.append(event)
            return []

        return self._dispatch(event)

    def _dispatch(self, event: DomainEvent) -> list[Exception]:
        """Dispatch event to matching handlers."""
        errors: list[Exception] = []
        event_type = event.event_type
        event_category = event.category

        for reg in self._handlers:
            if reg.is_async:
                continue  # Skip async handlers in sync emitter

            # Check type filter
            if reg.event_types and event_type not in reg.event_types:
                continue

            # Check category filter
            if reg.categories and event_category not in reg.categories:
                continue

            try:
                reg.handler(event)
            except Exception as e:
                logger.exception(
                    "Handler %s failed for event %s",
                    reg.handler,
                    event_type,
                )
                errors.append(e)

        return errors

    def batch(self) -> EventBatch:
        """Create a batch context for collecting events.

        Events are held until the context exits, then emitted together.
        Useful for transactional boundaries.
        """
        return EventBatch(self)

    def _start_batch(self) -> None:
        """Start batching mode."""
        self._batching = True
        self._batch = []

    def _end_batch(self) -> list[Exception]:
        """End batching and emit all collected events."""
        self._batching = False
        events = self._batch
        self._batch = []

        errors: list[Exception] = []
        for event in events:
            errors.extend(self._dispatch(event))
        return errors


class EventBatch:
    """Context manager for batching events."""

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter
        self._errors: list[Exception] = []

    def __enter__(self) -> EventBatch:
        self._emitter._start_batch()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            # No exception - emit all events
            self._errors = self._emitter._end_batch()
        else:
            # Exception occurred - discard batch
            self._emitter._batching = False
            self._emitter._batch = []

    def add(self, event: DomainEvent) -> None:
        """Add event to batch."""
        self._emitter.emit(event)

    @property
    def errors(self) -> list[Exception]:
        """Errors from handler execution (available after context exits)."""
        return self._errors


class AsyncEventEmitter:
    """Asynchronous event emitter.

    Like EventEmitter but supports async handlers.

    Usage:
        emitter = AsyncEventEmitter()

        async def handle_settlement(event: PaymentSettled) -> None:
            await notify_client(event)

        emitter.on(PaymentSettled, handle_settlement)
        await emitter.emit(event)
    """

    def __init__(self) -> None:
        self._handlers: list[HandlerRegistration] = []
        self._batching = False
        self._batch: list[DomainEvent] = []

    def on(
        self,
        event_type: type[T] | list[type[T]],
        handler: AsyncEventHandler,
    ) -> None:
        """Register async handler for specific event type(s)."""
        if isinstance(event_type, list):
            types = {t.__name__ for t in event_type}
        else:
            types = {event_type.__name__}

        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=types,
                categories=None,
                is_async=True,
            )
        )

    def on_sync(
        self,
        event_type: type[T] | list[type[T]],
        handler: EventHandler,
    ) -> None:
        """Register sync handler (will be run in executor)."""
        if isinstance(event_type, list):
            types = {t.__name__ for t in event_type}
        else:
            types = {event_type.__name__}

        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=types,
                categories=None,
                is_async=False,
            )
        )

    def on_category(
        self,
        category: EventCategory | list[EventCategory],
        handler: AsyncEventHandler,
    ) -> None:
        """Register async handler for event category(ies)."""
        if isinstance(category, list):
            cats = set(category)
        else:
            cats = {category}

        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=None,
                categories=cats,
                is_async=True,
            )
        )

    def on_all(self, handler: AsyncEventHandler) -> None:
        """Register async handler for all events."""
        self._handlers.append(
            HandlerRegistration(
                handler=handler,
                event_types=None,
                categories=None,
                is_async=True,
            )
        )

    def off(self, handler: AsyncEventHandler | EventHandler) -> None:
        """Unregister a handler."""
        self._handlers = [
            reg for reg in self._handlers if reg.handler is not handler
        ]

    async def emit(self, event: DomainEvent) -> list[Exception]:
        """Emit an event to all matching handlers.

        Returns list of any exceptions raised by handlers.
        """
        if self._batching:
            self._batch.append(event)
            return []

        return await self._dispatch(event)

    async def _dispatch(self, event: DomainEvent) -> list[Exception]:
        """Dispatch event to matching handlers."""
        errors: list[Exception] = []
        event_type = event.event_type
        event_category = event.category

        tasks: list[asyncio.Task[None]] = []

        for reg in self._handlers:
            # Check type filter
            if reg.event_types and event_type not in reg.event_types:
                continue

            # Check category filter
            if reg.categories and event_category not in reg.categories:
                continue

            if reg.is_async:
                # Create task for async handler
                task = asyncio.create_task(
                    self._call_async_handler(reg.handler, event)  # type: ignore
                )
                tasks.append(task)
            else:
                # Run sync handler directly
                try:
                    reg.handler(event)  # type: ignore
                except Exception as e:
                    logger.exception(
                        "Handler %s failed for event %s",
                        reg.handler,
                        event_type,
                    )
                    errors.append(e)

        # Gather async results
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    errors.append(result)

        return errors

    async def _call_async_handler(
        self,
        handler: AsyncEventHandler,
        event: DomainEvent,
    ) -> None:
        """Call async handler with error logging."""
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "Async handler %s failed for event %s",
                handler,
                event.event_type,
            )
            raise

    def batch(self) -> AsyncEventBatch:
        """Create a batch context for collecting events."""
        return AsyncEventBatch(self)

    def _start_batch(self) -> None:
        """Start batching mode."""
        self._batching = True
        self._batch = []

    async def _end_batch(self) -> list[Exception]:
        """End batching and emit all collected events."""
        self._batching = False
        events = self._batch
        self._batch = []

        errors: list[Exception] = []
        for event in events:
            errors.extend(await self._dispatch(event))
        return errors


class AsyncEventBatch:
    """Async context manager for batching events."""

    def __init__(self, emitter: AsyncEventEmitter) -> None:
        self._emitter = emitter
        self._errors: list[Exception] = []

    async def __aenter__(self) -> AsyncEventBatch:
        self._emitter._start_batch()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self._errors = await self._emitter._end_batch()
        else:
            self._emitter._batching = False
            self._emitter._batch = []

    async def add(self, event: DomainEvent) -> None:
        """Add event to batch."""
        await self._emitter.emit(event)

    @property
    def errors(self) -> list[Exception]:
        """Errors from handler execution."""
        return self._errors

"""Coalesce rapid writes into one flush after a quiet period, without losing any."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant


class DebouncedWrites[V]:
    """Queue keyed values; `flush(pending)` runs `delay` seconds after the last `queue()`.

    Values queued while a flush is in flight are picked up by the same task
    once it finishes, so nothing is dropped. HA's own Debouncer silently
    ignores calls made during execution, which is why this exists.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        flush: Callable[[dict[str, V]], Awaitable[None]],
        delay: float,
    ) -> None:
        self._hass = hass
        self._flush = flush
        self._delay = delay
        self._pending: dict[str, V] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._task: asyncio.Task[None] | None = None

    def queue(self, **values: V) -> None:
        self._pending.update(values)
        self._cancel_timer()
        self._timer = self._hass.loop.call_later(self._delay, self._launch)

    async def flush_now(self) -> None:
        """Send everything pending, including values queued behind an in-flight flush."""
        while self._pending or self._running():
            self._cancel_timer()
            self._launch()
            if self._task is not None:
                await self._task

    def drop(self) -> None:
        """Forget pending values without sending them."""
        self._cancel_timer()
        self._pending.clear()

    def _running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _launch(self) -> None:
        self._timer = None
        if not self._running():
            self._task = self._hass.async_create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending:
            batch, self._pending = self._pending, {}
            await self._flush(batch)

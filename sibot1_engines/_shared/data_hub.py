from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from .contracts import MarketEvent


class InMemorySharedDataHub:
    """Reference fan-out hub.

    Production transport may later be Unix-socket/Redis/NATS/shared-memory, but engines
    receive the same normalized MarketEvent contract and do not poll CSV for hot data.
    """

    def __init__(self):
        self._subscribers: dict[str, Callable[[MarketEvent], object]] = {}
        self._lock = RLock()

    def subscribe(self, engine_id: str, callback: Callable[[MarketEvent], object]) -> None:
        with self._lock:
            self._subscribers[engine_id] = callback

    def unsubscribe(self, engine_id: str) -> None:
        with self._lock:
            self._subscribers.pop(engine_id, None)

    def publish(self, event: MarketEvent) -> dict[str, object]:
        with self._lock:
            targets = tuple(self._subscribers.items())
        return {engine_id: callback(event) for engine_id, callback in targets}

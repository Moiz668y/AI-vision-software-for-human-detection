"""Thread-safe shared state for realtime backend workers."""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StateChange:
    """Immutable description of a committed state update."""

    revision: int
    timestamp: float
    updates: Dict[str, Any]
    snapshot: Dict[str, Any]


StateSubscriber = Callable[[StateChange], None]


class StateStore:
    """Small thread-safe state container with revision tracking.

    The store is designed for Flask request handlers and background realtime
    workers to share status without sharing mutable dictionaries. All snapshots
    returned by public methods are defensive copies.
    """

    def __init__(self, initial_state: Optional[Mapping[str, Any]] = None) -> None:
        self._state: Dict[str, Any] = dict(initial_state or {})
        self._revision = 0
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._subscribers: Dict[int, StateSubscriber] = {}
        self._next_subscriber_id = 1

    @property
    def revision(self) -> int:
        """Return the current monotonically increasing state revision."""

        with self._lock:
            return self._revision

    def snapshot(self) -> Dict[str, Any]:
        """Return a defensive copy of the full state."""

        with self._lock:
            snapshot = self._copy(self._state)
            snapshot["sequence"] = self._revision
            return snapshot

    def get(self, key: str, default: Any = None) -> Any:
        """Return a defensive copy of a single state value."""

        self._validate_key(key)
        with self._lock:
            if key not in self._state:
                return default
            return self._copy(self._state[key])

    def set(self, key: str, value: Any) -> StateChange:
        """Set one key and notify subscribers."""

        self._validate_key(key)
        return self.update({key: value})

    def merge_dict(self, key: str, values: Mapping[str, Any]) -> StateChange:
        """Atomically merge ``values`` into a dictionary stored at ``key``."""

        self._validate_key(key)
        if not isinstance(values, Mapping):
            raise TypeError("values must be a mapping")

        with self._lock:
            current = self._state.get(key, {})
            if not isinstance(current, dict):
                current = {}
            merged = dict(current)
            merged.update(values)
            self._state[key] = merged
            self._revision += 1
            change = StateChange(
                revision=self._revision,
                timestamp=time.time(),
                updates={key: self._copy(merged)},
                snapshot=self._copy(self._state),
            )
            subscribers = list(self._subscribers.values())
            self._changed.notify_all()

        self._notify(subscribers, change)
        return change

    def update(
        self,
        values: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> StateChange:
        """Merge top-level keys into the store and notify subscribers.

        Args:
            values: Mapping of state keys to replace.
            **kwargs: Additional state keys to replace.

        Returns:
            A :class:`StateChange` describing the committed revision.
        """

        updates: Dict[str, Any] = {}
        if values:
            updates.update(values)
        updates.update(kwargs)

        for key in updates:
            self._validate_key(key)

        with self._lock:
            for key, value in updates.items():
                self._state[key] = value

            if updates:
                self._revision += 1
                timestamp = time.time()
                change = StateChange(
                    revision=self._revision,
                    timestamp=timestamp,
                    updates=self._copy(updates),
                    snapshot=self._copy(self._state),
                )
                subscribers = list(self._subscribers.values())
                self._changed.notify_all()
            else:
                change = StateChange(
                    revision=self._revision,
                    timestamp=time.time(),
                    updates={},
                    snapshot=self._copy(self._state),
                )
                subscribers = []

        self._notify(subscribers, change)
        return change

    def replace(self, values: Mapping[str, Any]) -> StateChange:
        """Replace the full state with ``values`` and notify subscribers."""

        for key in values:
            self._validate_key(key)

        with self._lock:
            self._state = dict(values)
            self._revision += 1
            change = StateChange(
                revision=self._revision,
                timestamp=time.time(),
                updates=self._copy(self._state),
                snapshot=self._copy(self._state),
            )
            subscribers = list(self._subscribers.values())
            self._changed.notify_all()

        self._notify(subscribers, change)
        return change

    def subscribe(self, callback: StateSubscriber) -> Callable[[], None]:
        """Register a callback and return an unsubscribe function."""

        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(subscriber_id, None)

        return unsubscribe

    def wait_for_revision(
        self,
        last_seen_revision: int,
        timeout: Optional[float] = None,
    ) -> Optional[StateChange]:
        """Block until a newer revision exists or timeout expires.

        Returns ``None`` on timeout.
        """

        if last_seen_revision < 0:
            raise ValueError("last_seen_revision must be >= 0")

        with self._changed:
            if self._revision <= last_seen_revision:
                ready = self._changed.wait_for(
                    lambda: self._revision > last_seen_revision,
                    timeout=timeout,
                )
                if not ready:
                    return None

            return StateChange(
                revision=self._revision,
                timestamp=time.time(),
                updates={},
                snapshot=self._copy(self._state),
            )

    def wait_for_update(self, last_sequence: int = 0, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Compatibility helper returning a snapshot with a ``sequence`` key."""

        change = self.wait_for_revision(last_sequence, timeout=timeout)
        if change is None:
            return self.snapshot()
        snapshot = self._copy(change.snapshot)
        snapshot["sequence"] = change.revision
        return snapshot

    def keys(self) -> Iterable[str]:
        """Return a stable list of state keys."""

        with self._lock:
            return list(self._state.keys())

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("state keys must be non-empty strings")

    @staticmethod
    def _copy(value: Any) -> Any:
        try:
            return copy.deepcopy(value)
        except Exception:
            logger.debug("Falling back to shallow state copy", exc_info=True)
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, list):
                return list(value)
            if isinstance(value, tuple):
                return tuple(value)
            return value

    @staticmethod
    def _notify(subscribers: Iterable[StateSubscriber], change: StateChange) -> None:
        for callback in subscribers:
            try:
                callback(change)
            except Exception:
                logger.exception("State subscriber failed")

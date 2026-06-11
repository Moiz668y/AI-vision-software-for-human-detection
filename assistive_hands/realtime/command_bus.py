"""Thread-safe command queue for realtime backend workers."""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Command:
    """A command requested by the UI, API, or another worker."""

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type:
            raise ValueError("command type must be a non-empty string")
        if not isinstance(self.payload, dict):
            raise TypeError("command payload must be a dict")
        object.__setattr__(self, "payload", dict(self.payload))

    @property
    def name(self) -> str:
        """Compatibility alias used by the legacy app integration."""

        return self.type


@dataclass(frozen=True)
class CommandResult:
    """Outcome reported by a command worker."""

    command_id: str
    command_type: str
    ok: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    completed_at: float = field(default_factory=time.time)


CommandSubscriber = Callable[[Command], None]
ResultSubscriber = Callable[[CommandResult], None]


class CommandBus:
    """In-process command bus backed by a standard-library queue."""

    def __init__(self, maxsize: int = 0, state_store=None) -> None:
        if not isinstance(maxsize, int):
            state_store = maxsize
            maxsize = 0
        self.state_store = state_store
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.RLock()
        self._command_subscribers: Dict[int, CommandSubscriber] = {}
        self._result_subscribers: Dict[int, ResultSubscriber] = {}
        self._next_subscriber_id = 1
        self._results: Dict[str, CommandResult] = {}
        self._handlers: Dict[str, Callable[[Command], Any]] = {}
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._last_seen: Dict[tuple[str, str], float] = {}
        self.dedupe_seconds = 0.12

    def register(self, command_type: str, handler: Callable[[Command], Any]) -> None:
        """Register a command handler for the built-in executor thread."""

        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers[command_type] = handler

    def enqueue(
        self,
        command_type: str,
        payload: Optional[Mapping[str, Any]] = None,
        priority: int = 50,
        source: str = "system",
    ) -> bool:
        """Compatibility helper for enqueueing deduped commands."""

        payload_dict = dict(payload or {})
        key = (command_type, repr(sorted(payload_dict.items())))
        now = time.time()
        if now - self._last_seen.get(key, 0.0) < self.dedupe_seconds:
            return False
        self._last_seen[key] = now
        self.submit(command_type, payload_dict, source=source)
        self._publish_queue_state()
        return True

    def start(self) -> None:
        """Start the built-in command executor thread."""

        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._run_handlers, daemon=True, name="command-bus")
        self._worker_thread.start()

    def stop(self) -> None:
        self._running = False
        self.submit("noop", source="shutdown")

    def size(self) -> int:
        return self.qsize()

    def submit(
        self,
        command_type: str,
        payload: Optional[Mapping[str, Any]] = None,
        source: str = "system",
        block: bool = True,
        timeout: Optional[float] = None,
    ) -> Command:
        """Create and enqueue a command."""

        command = Command(
            type=command_type,
            payload=dict(payload or {}),
            source=source,
        )
        self.publish(command, block=block, timeout=timeout)
        return command

    def publish(
        self,
        command: Command,
        block: bool = True,
        timeout: Optional[float] = None,
    ) -> None:
        """Enqueue an existing command instance."""

        if not isinstance(command, Command):
            raise TypeError("command must be a Command")

        if timeout is None:
            self._queue.put(command, block=block)
        else:
            self._queue.put(command, block=block, timeout=timeout)

        self._notify_command(command)

    def get(
        self,
        block: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[Command]:
        """Return the next command, or ``None`` when a non-blocking poll is empty."""

        try:
            if timeout is None:
                return self._queue.get(block=block)
            return self._queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def task_done(self) -> None:
        """Signal that the current queue item is complete."""

        self._queue.task_done()

    def join(self) -> None:
        """Block until all queued commands have been marked done."""

        self._queue.join()

    def qsize(self) -> int:
        """Return an approximate command queue size."""

        return self._queue.qsize()

    def _publish_queue_state(self, command: Optional[Command] = None) -> None:
        if not self.state_store:
            return
        update = {"queue_size": self.qsize()}
        if command:
            update.update({"last": command.type, "last_source": command.source, "last_at": time.time()})
        try:
            self.state_store.merge_dict("command", update)
        except Exception:
            logger.debug("Could not publish command queue state", exc_info=True)

    def _run_handlers(self) -> None:
        while self._running:
            command = self.get(block=True, timeout=0.2)
            if command is None:
                continue
            if command.type == "noop":
                self.task_done()
                continue
            handler = self._handlers.get(command.type)
            if handler:
                try:
                    handler(command)
                    self.report_result(CommandResult(command.command_id, command.type, True))
                except Exception as exc:
                    logger.exception("Command handler failed: %s", command.type)
                    self.report_result(CommandResult(command.command_id, command.type, False, str(exc)))
            else:
                logger.warning("No command handler registered for %s", command.type)
            self._publish_queue_state(command)
            self.task_done()

    def subscribe_commands(self, callback: CommandSubscriber) -> Callable[[], None]:
        """Register a callback invoked after each command is enqueued."""

        return self._subscribe(self._command_subscribers, callback)

    def subscribe_results(self, callback: ResultSubscriber) -> Callable[[], None]:
        """Register a callback invoked when a command result is reported."""

        return self._subscribe(self._result_subscribers, callback)

    def report_result(self, result: CommandResult) -> None:
        """Store and publish a command result."""

        if not isinstance(result, CommandResult):
            raise TypeError("result must be a CommandResult")

        with self._lock:
            self._results[result.command_id] = result
            subscribers = list(self._result_subscribers.values())

        for callback in subscribers:
            try:
                callback(result)
            except Exception:
                logger.exception("Command result subscriber failed")

    def get_result(self, command_id: str) -> Optional[CommandResult]:
        """Return a result by command id if it has been reported."""

        with self._lock:
            return self._results.get(command_id)

    def drain(self, max_items: Optional[int] = None) -> Iterable[Command]:
        """Yield currently queued commands without blocking."""

        count = 0
        while max_items is None or count < max_items:
            command = self.get(block=False)
            if command is None:
                break
            count += 1
            yield command

    def _subscribe(self, registry: Dict[int, Callable], callback: Callable) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            registry[subscriber_id] = callback

        def unsubscribe() -> None:
            with self._lock:
                registry.pop(subscriber_id, None)

        return unsubscribe

    def _notify_command(self, command: Command) -> None:
        with self._lock:
            subscribers = list(self._command_subscribers.values())

        for callback in subscribers:
            try:
                callback(command)
            except Exception:
                logger.exception("Command subscriber failed")

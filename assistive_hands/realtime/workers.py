"""Worker skeletons for the AssistiveHands realtime engine."""

from __future__ import annotations

import importlib
import logging
import threading
import time
from enum import Enum
from types import ModuleType
from typing import Callable, Dict, Optional

from .command_bus import Command, CommandBus, CommandResult
from .state_store import StateStore

logger = logging.getLogger(__name__)


class WorkerStatus(str, Enum):
    """Lifecycle states exposed by realtime workers."""

    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class LazyImportMixin:
    """Helper for workers that need optional hardware libraries later."""

    _module_cache: Dict[str, ModuleType]

    def import_optional(self, module_name: str) -> Optional[ModuleType]:
        """Import ``module_name`` lazily and return ``None`` if unavailable."""

        if not hasattr(self, "_module_cache"):
            self._module_cache = {}

        if module_name in self._module_cache:
            return self._module_cache[module_name]

        try:
            module = importlib.import_module(module_name)
        except ImportError:
            logger.info("Optional module is not available: %s", module_name)
            return None

        self._module_cache[module_name] = module
        return module


class BaseWorker(threading.Thread):
    """Lifecycle-managed base class for realtime background workers."""

    def __init__(
        self,
        name: str,
        state_store: StateStore,
        command_bus: Optional[CommandBus] = None,
        interval: float = 0.03,
        daemon: bool = True,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")

        super().__init__(name=name, daemon=daemon)
        self.state_store = state_store
        self.command_bus = command_bus
        self.interval = interval
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._stop_requested = threading.Event()
        self._status = WorkerStatus.INITIALIZED
        self._status_lock = threading.RLock()
        self._tick_count = 0

        self._publish_status()

    @property
    def status(self) -> WorkerStatus:
        with self._status_lock:
            return self._status

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def stop(self, timeout: Optional[float] = None) -> None:
        """Ask the worker to stop and optionally wait for the thread to exit."""

        self._stop_requested.set()
        self._set_status(WorkerStatus.STOPPING)
        if self.is_alive():
            self.join(timeout=timeout)

    def should_stop(self) -> bool:
        """Return whether shutdown has been requested."""

        return self._stop_requested.is_set()

    def run(self) -> None:
        """Thread entry point."""

        self.started_at = time.time()
        self._set_status(WorkerStatus.STARTING)

        try:
            self.setup()
            self._set_status(WorkerStatus.RUNNING)
            while not self.should_stop():
                started = time.monotonic()
                self.on_tick()
                self._tick_count += 1
                self._publish_status()

                elapsed = time.monotonic() - started
                remaining = max(0.0, self.interval - elapsed)
                self._stop_requested.wait(remaining)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Realtime worker failed: %s", self.name)
            self._set_status(WorkerStatus.ERROR)
        finally:
            try:
                self.teardown()
            except Exception:
                logger.exception("Realtime worker teardown failed: %s", self.name)
            self.stopped_at = time.time()
            if self.status != WorkerStatus.ERROR:
                self._set_status(WorkerStatus.STOPPED)
            self._publish_status()

    def setup(self) -> None:
        """Hook for opening resources after the thread starts."""

    def on_tick(self) -> None:
        """Hook called every loop iteration."""

    def teardown(self) -> None:
        """Hook for closing resources before the thread exits."""

    def _set_status(self, status: WorkerStatus) -> None:
        with self._status_lock:
            self._status = status
        self._publish_status()

    def _publish_status(self) -> None:
        worker_state = {
            "status": self.status.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "last_error": self.last_error,
            "tick_count": self.tick_count,
        }
        self.state_store.merge_dict("workers", {self.name: worker_state})


CommandHandler = Callable[[Command], Optional[CommandResult]]


class CommandWorker(BaseWorker):
    """Worker skeleton that consumes commands and dispatches handlers."""

    def __init__(
        self,
        name: str,
        state_store: StateStore,
        command_bus: CommandBus,
        interval: float = 0.03,
        daemon: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            state_store=state_store,
            command_bus=command_bus,
            interval=interval,
            daemon=daemon,
        )
        self._handlers: Dict[str, CommandHandler] = {}

    def register_handler(self, command_type: str, handler: CommandHandler) -> None:
        """Register a callable for a command type."""

        if not isinstance(command_type, str) or not command_type:
            raise ValueError("command_type must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")

        self._handlers[command_type] = handler

    def on_tick(self) -> None:
        if self.command_bus is None:
            return

        command = self.command_bus.get(block=True, timeout=self.interval)
        if command is None:
            return

        try:
            result = self.handle_command(command)
            if result is None:
                result = CommandResult(
                    command_id=command.command_id,
                    command_type=command.type,
                    ok=True,
                    message="handled",
                )
            self.command_bus.report_result(result)
        except Exception as exc:
            logger.exception("Command handler failed: %s", command.type)
            self.command_bus.report_result(
                CommandResult(
                    command_id=command.command_id,
                    command_type=command.type,
                    ok=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            self.command_bus.task_done()

    def handle_command(self, command: Command) -> Optional[CommandResult]:
        """Dispatch one command. Subclasses may override this method."""

        handler = self._handlers.get(command.type)
        if handler is None:
            return CommandResult(
                command_id=command.command_id,
                command_type=command.type,
                ok=False,
                message="no handler registered",
            )
        return handler(command)


class VisionWorker(BaseWorker, LazyImportMixin):
    """Skeleton for future camera or MediaPipe processing.

    The optional libraries are imported only from ``setup`` in a running worker,
    never when the realtime package is imported.
    """

    def setup(self) -> None:
        self.cv2 = self.import_optional("cv2")
        self.mediapipe = self.import_optional("mediapipe")

    def on_tick(self) -> None:
        self.state_store.set(
            "vision",
            {
                "available": self.cv2 is not None and self.mediapipe is not None,
                "last_frame_at": None,
            },
        )


class InputWorker(CommandWorker, LazyImportMixin):
    """Skeleton for future pyautogui-backed cursor and keyboard commands."""

    def setup(self) -> None:
        self.pyautogui = self.import_optional("pyautogui")

    def handle_command(self, command: Command) -> Optional[CommandResult]:
        if self.pyautogui is None:
            return CommandResult(
                command_id=command.command_id,
                command_type=command.type,
                ok=False,
                message="pyautogui unavailable",
            )
        return super().handle_command(command)

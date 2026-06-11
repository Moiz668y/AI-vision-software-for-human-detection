"""Backend realtime engine primitives for AssistiveHands.

This package is intentionally import-side-effect free. Importing it must not
open cameras, start threads, or initialize input-device libraries.
"""

from .command_bus import Command, CommandBus, CommandResult
from .scroll_worker import ScrollWorker
from .state_store import StateChange, StateStore
from .workers import (
    BaseWorker,
    CommandWorker,
    InputWorker,
    LazyImportMixin,
    VisionWorker,
    WorkerStatus,
)

__all__ = [
    "BaseWorker",
    "Command",
    "CommandBus",
    "CommandResult",
    "CommandWorker",
    "InputWorker",
    "LazyImportMixin",
    "ScrollWorker",
    "StateChange",
    "StateStore",
    "VisionWorker",
    "WorkerStatus",
]

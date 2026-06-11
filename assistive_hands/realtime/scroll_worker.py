import threading
import time


class ScrollWorker:
    """Backend-owned continuous scroll loop."""

    PROFILES = {
        "normal": {"amount": 60, "interval": 0.03},
        "fast": {"amount": 180, "interval": 0.03},
    }

    def __init__(self, scroll_func, state_store=None):
        self.scroll_func = scroll_func
        self.state_store = state_store
        self._lock = threading.Lock()
        self._active = False
        self._direction = 0
        self._speed = "normal"
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="scroll-worker")
        self._thread.start()

    def shutdown(self):
        self.stop_scroll()
        self._running = False

    def start_scroll(self, direction: int, speed="normal"):
        with self._lock:
            self._active = True
            self._direction = 1 if direction > 0 else -1
            self._speed = speed if speed in self.PROFILES else "normal"
        self._publish()

    def stop_scroll(self):
        with self._lock:
            was_active = self._active
            self._active = False
            self._direction = 0
        if was_active:
            self._publish()

    def snapshot(self):
        with self._lock:
            return {"active": self._active, "direction": self._direction, "speed": self._speed}

    def _publish(self):
        if self.state_store:
            self.state_store.update(scroll=self.snapshot())

    def _run(self):
        while self._running:
            with self._lock:
                active = self._active
                direction = self._direction
                speed = self._speed
            if not active:
                time.sleep(0.01)
                continue
            profile = self.PROFILES.get(speed, self.PROFILES["normal"])
            amount = profile["amount"] * (-1 if direction > 0 else 1)
            try:
                self.scroll_func(amount)
            except Exception as exc:
                print(f"[WARN] scroll failed: {exc}")
            time.sleep(profile["interval"])

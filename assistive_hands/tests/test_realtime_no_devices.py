import importlib
import sys
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSISTIVE_HANDS_ROOT = PROJECT_ROOT / "assistive_hands"


class RealtimeImportTest(unittest.TestCase):
    def test_realtime_package_imports_without_device_libraries(self):
        heavy_modules = ("cv2", "mediapipe", "pyautogui")
        saved_modules = {name: sys.modules.pop(name, None) for name in heavy_modules}
        saved_realtime = {
            name: module
            for name, module in list(sys.modules.items())
            if name == "realtime"
            or name.startswith("realtime.")
            or name == "assistive_hands.realtime"
            or name.startswith("assistive_hands.realtime.")
        }
        for name in saved_realtime:
            sys.modules.pop(name, None)

        sys.path.insert(0, str(ASSISTIVE_HANDS_ROOT))
        try:
            package = importlib.import_module("realtime")
            self.assertTrue(hasattr(package, "CommandBus"))
            self.assertTrue(hasattr(package, "StateStore"))
            self.assertTrue(hasattr(package, "ScrollWorker"))
            for name in heavy_modules:
                self.assertNotIn(name, sys.modules)
        finally:
            try:
                sys.path.remove(str(ASSISTIVE_HANDS_ROOT))
            except ValueError:
                pass
            for name in list(sys.modules):
                if (
                    name == "realtime"
                    or name.startswith("realtime.")
                    or name == "assistive_hands.realtime"
                    or name.startswith("assistive_hands.realtime.")
                ):
                    sys.modules.pop(name, None)
            sys.modules.update(saved_realtime)
            for name, module in saved_modules.items():
                if module is not None:
                    sys.modules[name] = module


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        from assistive_hands.realtime import StateStore

        self.store = StateStore({"cursor": {"enabled": True}})

    def test_snapshot_and_get_return_defensive_copies(self):
        snapshot = self.store.snapshot()
        snapshot["cursor"]["enabled"] = False

        cursor = self.store.get("cursor")
        cursor["enabled"] = False

        final_snapshot = self.store.snapshot()
        self.assertEqual({"enabled": True}, final_snapshot["cursor"])
        self.assertEqual(self.store.revision, final_snapshot["sequence"])

    def test_update_merge_and_subscribe_publish_revisions(self):
        changes = []
        unsubscribe = self.store.subscribe(changes.append)

        first = self.store.set("camera", {"running": False})
        second = self.store.merge_dict("camera", {"running": True, "fps": 30})
        unsubscribe()
        third = self.store.set("ignored_by_subscriber", True)

        self.assertEqual(1, first.revision)
        self.assertEqual(2, second.revision)
        self.assertEqual(3, third.revision)
        self.assertEqual(2, len(changes))
        self.assertEqual({"running": True, "fps": 30}, self.store.get("camera"))

    def test_wait_for_revision_returns_current_snapshot_or_timeout(self):
        self.assertIsNone(self.store.wait_for_revision(self.store.revision, timeout=0.01))

        change = self.store.set("engine", {"running": True})
        observed = self.store.wait_for_revision(change.revision - 1, timeout=0.01)

        self.assertIsNotNone(observed)
        self.assertEqual(change.revision, observed.revision)
        self.assertEqual({"running": True}, observed.snapshot["engine"])


class CommandBusTest(unittest.TestCase):
    def setUp(self):
        from assistive_hands.realtime import CommandBus

        self.bus = CommandBus()

    def test_submit_get_result_and_drain_commands(self):
        from assistive_hands.realtime import CommandResult

        seen_commands = []
        seen_results = []
        self.bus.subscribe_commands(seen_commands.append)
        self.bus.subscribe_results(seen_results.append)

        command = self.bus.submit("cursor_move", {"x": 12, "y": 34}, source="test")

        self.assertEqual(1, self.bus.qsize())
        self.assertEqual([command], seen_commands)
        queued = self.bus.get(block=False)
        self.assertEqual("cursor_move", queued.type)
        self.assertEqual({"x": 12, "y": 34}, queued.payload)
        self.assertEqual("test", queued.source)
        self.bus.task_done()

        result = CommandResult(command.command_id, command.type, True, data={"handled": True})
        self.bus.report_result(result)

        self.assertEqual(result, self.bus.get_result(command.command_id))
        self.assertEqual([result], seen_results)

        first = self.bus.submit("one")
        second = self.bus.submit("two")
        self.assertEqual([first, second], list(self.bus.drain()))

    def test_invalid_commands_are_rejected(self):
        from assistive_hands.realtime import Command

        with self.assertRaises(ValueError):
            Command("")
        with self.assertRaises(TypeError):
            Command("bad_payload", payload=[])
        with self.assertRaises(TypeError):
            self.bus.publish(object())


class ScrollWorkerTest(unittest.TestCase):
    def test_scroll_state_changes_without_starting_worker_thread(self):
        from assistive_hands.realtime import StateStore
        from assistive_hands.realtime.scroll_worker import ScrollWorker

        calls = []
        store = StateStore()
        worker = ScrollWorker(calls.append, store)

        worker.start_scroll(direction=1, speed="fast")
        self.assertEqual({"active": True, "direction": 1, "speed": "fast"}, worker.snapshot())
        self.assertEqual(worker.snapshot(), store.get("scroll"))
        self.assertEqual([], calls)

        worker.start_scroll(direction=-1, speed="unknown")
        self.assertEqual({"active": True, "direction": -1, "speed": "normal"}, worker.snapshot())

        worker.stop_scroll()
        self.assertEqual({"active": False, "direction": 0, "speed": "normal"}, worker.snapshot())
        self.assertEqual(worker.snapshot(), store.get("scroll"))
        self.assertEqual([], calls)

    def test_scroll_worker_thread_can_start_and_shutdown_without_devices(self):
        from assistive_hands.realtime import StateStore
        from assistive_hands.realtime.scroll_worker import ScrollWorker

        calls = []
        worker = ScrollWorker(calls.append, StateStore())
        worker.start()
        worker.start_scroll(direction=1)
        time.sleep(0.05)
        worker.shutdown()

        self.assertFalse(worker.snapshot()["active"])
        self.assertGreaterEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

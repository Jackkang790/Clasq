"""Regression contracts for the installed repeated-lifecycle acceptance gate."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Batch31SecondLaunchLifecycleTests(unittest.TestCase):
    def test_startup_ready_requires_server_and_smoke_inference(self):
        source = (ROOT / "src/ai/startup_worker.py").read_text(encoding="utf-8")
        ready_path = source[source.index("if manager.ensure_running()") : source.index("last_error = manager.error")]
        self.assertIn("manager.ensure_running() and manager.smoke_inference()", ready_path)
        self.assertLess(ready_path.index("manager.smoke_inference()"), ready_path.index("self.ready.emit(True"))

    def test_main_window_is_shown_only_after_startup_gate_returns(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        entry = source[source.index("def main():") : source.index('if __name__ == "__main__":')]
        self.assertLess(entry.index("load_llama_with_progress()"), entry.index("window = MainWindow("))
        self.assertLess(entry.index("window = MainWindow("), entry.index("window.show()"))
        self.assertLess(entry.index("window.show()"), entry.index("app.exec()"))

    def test_close_contract_uses_owned_manager_without_global_kill(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        close = source[source.index("def closeEvent") : source.index("def _navigate")]
        self.assertIn("self._server_manager.shutdown()", close)
        self.assertNotIn("taskkill", close.lower())
        self.assertNotIn("Get-Process", close)


if __name__ == "__main__":
    unittest.main()

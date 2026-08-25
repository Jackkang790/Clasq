"""Batch 17 controlled lifecycle and fault-injection coverage."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.ai.config import AIConfig
from src.ai.qwen_client import AITimeoutError, QwenClient, set_runtime_recovery
from src.ai.server_manager import LlamaServerManager


class _Pipe:
    def __init__(self, lines=()):
        self.lines = iter(lines)
        self.closed = False
    def readline(self):
        return next(self.lines, b"")
    def close(self):
        self.closed = True


class _Proc:
    next_pid = 17000
    def __init__(self, polls=None, stderr=()):
        self.pid = _Proc.next_pid
        _Proc.next_pid += 1
        self._polls = list(polls or [None])
        self.returncode = None
        self.stderr = _Pipe(stderr)
        self.terminated = self.killed = self.waited = False
    def poll(self):
        value = self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]
        if value is not None:
            self.returncode = value
        return value
    def terminate(self): self.terminated = True; self.returncode = 0
    def kill(self): self.killed = True; self.returncode = -9
    def wait(self, timeout=None): self.waited = True; return self.returncode


class _Response:
    def __init__(self, status=200, content="OK"):
        self.status_code = status
        self._content = content
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)
    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _HTTP:
    def __init__(self, health=(True,), post=None):
        self.health = list(health)
        self.post_result = post or _Response()
    def get(self, *_a, **_k):
        value = self.health.pop(0) if len(self.health) > 1 else self.health[0]
        if isinstance(value, Exception): raise value
        return _Response(200 if value else 503)
    def post(self, *_a, **_k):
        if isinstance(self.post_result, Exception): raise self.post_result
        return self.post_result


class Batch17ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name in ("server.exe", "model.gguf", "mmproj.gguf"):
            (root / name).write_bytes(b"x")
        self.cfg = replace(
            AIConfig(), llama_server_exe=str(root / "server.exe"),
            llama_model_path=str(root / "model.gguf"),
            llama_mmproj_path=str(root / "mmproj.gguf"),
            llama_startup_timeout=2,
        )
    def tearDown(self): self.tmp.cleanup()

    def manager(self, *, proc=None, http=None, clock=None, sleep=None):
        proc = proc or _Proc()
        return LlamaServerManager(
            self.cfg, http=http or _HTTP(), popen_factory=lambda *_a, **_k: proc,
            monotonic=clock, sleep=sleep,
        ), proc

    @patch("src.ai.server_manager.os.name", "posix")
    def test_normal_start_and_readiness(self):
        manager, proc = self.manager(http=_HTTP([False, True]))
        self.assertTrue(manager.ensure_running())
        self.assertTrue(manager.is_available)
        manager.shutdown()

    @patch("src.ai.server_manager.os.name", "posix")
    def test_normal_shutdown_has_no_owned_process(self):
        manager, proc = self.manager(http=_HTTP([False, True]))
        self.assertTrue(manager.ensure_running())
        manager.shutdown()
        self.assertTrue(proc.terminated and proc.waited)
        self.assertIsNone(manager._proc)

    @patch("src.ai.server_manager.os.name", "posix")
    def test_readiness_timeout_is_controlled_and_cleans_process(self):
        ticks = iter([0, 0, 3])
        manager, proc = self.manager(http=_HTTP([False]), clock=lambda: next(ticks), sleep=lambda _s: None)
        self.assertFalse(manager.ensure_running())
        self.assertEqual(manager.failure_kind, "readiness_failure")
        self.assertTrue(proc.terminated)
        self.assertIsNone(manager._proc)

    @patch("src.ai.server_manager.os.name", "posix")
    def test_immediate_startup_exit_does_not_wait_for_timeout(self):
        calls = []
        manager, _ = self.manager(proc=_Proc([7], [b"fatal cuda out of memory\n"]),
                                  http=_HTTP([False]), clock=lambda: calls.append(1) or 0,
                                  sleep=lambda _s: self.fail("must not sleep"))
        self.assertFalse(manager.ensure_running())
        self.assertEqual(manager.failure_kind, "cuda_oom")
        self.assertLessEqual(len(calls), 2)

    @patch("src.ai.server_manager.os.name", "posix")
    def test_startup_exit_code_is_reported(self):
        manager, _ = self.manager(proc=_Proc([23]), http=_HTTP([False]))
        self.assertFalse(manager.ensure_running())
        self.assertIn("exit=23", manager.error)

    def test_model_missing_is_distinct_from_timeout(self):
        manager = LlamaServerManager(replace(self.cfg, llama_model_path="missing.gguf"), http=_HTTP([False]))
        self.assertFalse(manager.ensure_running())
        self.assertEqual(manager.failure_kind, "model_missing")

    def test_oom_classification_is_distinct(self):
        self.assertEqual(LlamaServerManager._classify_start_failure("CUDA out of memory"), "cuda_oom")
        self.assertEqual(LlamaServerManager._classify_start_failure("std::bad_alloc"), "ram_oom")
        self.assertEqual(LlamaServerManager._classify_start_failure("download cache bad"), "server_start_failure")

    def test_inference_success(self):
        manager = LlamaServerManager(self.cfg, http=_HTTP(post=_Response(content="OK")))
        self.assertTrue(manager.smoke_inference())

    def test_inference_timeout_is_typed_and_does_not_kill_server(self):
        proc = _Proc()
        manager = LlamaServerManager(self.cfg, http=_HTTP(post=requests.Timeout("late")))
        manager._proc = proc
        self.assertFalse(manager.smoke_inference())
        self.assertEqual(manager.failure_kind, "inference_timeout")
        self.assertFalse(proc.terminated)

    @patch("src.ai.server_manager.os.name", "posix")
    def test_runtime_crash_gets_one_bounded_recovery(self):
        procs = iter([_Proc([1]), _Proc([None]), _Proc([1])])
        made = []
        def spawn(*_a, **_k):
            p = next(procs); made.append(p); return p
        manager = LlamaServerManager(self.cfg, http=_HTTP([False, True, False]),
                                     popen_factory=spawn, sleep=lambda _s: None)
        manager._proc = spawn()
        self.assertTrue(manager.recover_if_needed())
        manager._proc = spawn()
        self.assertFalse(manager.recover_if_needed())
        self.assertEqual(manager._recovery_attempts, 1)

    @patch("src.ai.server_manager.os.name", "posix")
    def test_shutdown_blocks_restart(self):
        manager, _ = self.manager()
        manager.shutdown()
        self.assertFalse(manager.ensure_running())
        self.assertEqual(manager.failure_kind, "app_shutting_down")
        self.assertFalse(manager.recover_if_needed())

    def test_port_conflict_does_not_spawn_or_kill_foreign_server(self):
        spawn = Mock()
        manager = LlamaServerManager(self.cfg, http=_HTTP([False]), popen_factory=spawn)
        with patch.object(manager, "_detect_port_conflict", return_value="busy"):
            self.assertFalse(manager.ensure_running())
        spawn.assert_not_called()
        self.assertIsNone(manager._proc)

    def test_stderr_reader_closes_with_process(self):
        manager, proc = self.manager()
        manager._proc = proc
        manager._start_stderr_reader()
        manager.shutdown()
        self.assertTrue(proc.stderr.closed)
        self.assertIsNone(manager._stderr_thread)


class Batch17ClientAndUITests(unittest.TestCase):
    def tearDown(self):
        set_runtime_recovery(None)

    def test_qwen_controlled_timeout_is_user_safe(self):
        session = Mock()
        session.post.side_effect = requests.Timeout("secret command")
        with self.assertRaisesRegex(AITimeoutError, "시간이 초과"):
            QwenClient(session=session).request_text("hello", timeout=0.01)

    def test_timeout_ui_message_is_distinct_from_connection(self):
        source = Path("src/ui/ai_workers.py").read_text(encoding="utf-8")
        timeout_pos = source.index("except AITimeoutError")
        connection_pos = source.index("except AIConnectionError")
        self.assertLess(timeout_pos, connection_pos)
        self.assertIn("로컬 AI 응답 시간이 초과", source)

    def test_connection_failure_recovers_and_retries_once(self):
        session = Mock()
        session.post.side_effect = [requests.ConnectionError("crash"), _Response(content="recovered")]
        recovery = Mock(return_value=True)
        set_runtime_recovery(recovery)
        self.assertEqual(QwenClient(session=session).request_text("hello"), "recovered")
        recovery.assert_called_once_with()
        self.assertEqual(session.post.call_count, 2)

    def test_failed_recovery_does_not_loop(self):
        session = Mock()
        session.post.side_effect = requests.ConnectionError("crash")
        recovery = Mock(return_value=False)
        set_runtime_recovery(recovery)
        with self.assertRaisesRegex(Exception, "연결할 수 없습니다"):
            QwenClient(session=session).request_text("hello")
        self.assertEqual(session.post.call_count, 1)

    def test_main_close_marks_shutdown_before_server_shutdown(self):
        source = Path("main.py").read_text(encoding="utf-8")
        body = source[source.index("def closeEvent"):source.index("def _navigate")]
        self.assertLess(body.index("_app_shutting_down = True"), body.index(".shutdown()"))


@unittest.skipUnless(os.name == "nt", "Windows Job Object integration")
class Batch17WindowsJobIntegration(unittest.TestCase):
    @staticmethod
    def _alive(pid):
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False

    def test_force_killed_parent_takes_owned_child_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            owned_pid = Path(tmp) / "owned.pid"
            foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            script = (
                "import subprocess,sys,time; from src.ai.windows_job import KillOnCloseJob; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "j=KillOnCloseJob(); j.assign(p); "
                f"open({str(owned_pid)!r},'w').write(str(p.pid)); "
                "time.sleep(30)"
            )
            parent = subprocess.Popen([sys.executable, "-c", script], cwd=os.getcwd())
            try:
                deadline = time.time() + 5
                while not owned_pid.exists() and time.time() < deadline: time.sleep(.05)
                self.assertTrue(owned_pid.exists())
                owned = int(owned_pid.read_text())
                parent.kill(); parent.wait(5)
                deadline = time.time() + 5
                while self._alive(owned) and time.time() < deadline: time.sleep(.05)
                self.assertFalse(self._alive(owned), "owned child became orphan")
                self.assertTrue(self._alive(foreign.pid), "foreign process was terminated")
            finally:
                if parent.poll() is None: parent.kill()
                if foreign.poll() is None: foreign.kill()
                foreign.wait(5)

    def test_job_assignment_uses_handle_not_pid_or_name(self):
        source = Path("src/ai/windows_job.py").read_text(encoding="utf-8")
        self.assertIn("AssignProcessToJobObject", source)
        self.assertNotIn("taskkill", source.lower())
        self.assertNotIn("llama-server.exe", source.lower())

    def test_actual_bundled_server_abnormal_startup_is_detected(self):
        import socket
        executable = Path("dist-batch15/Clasq/_internal/runtime/llama-server.exe").resolve()
        if not executable.is_file():
            self.skipTest("bundled llama-server is not present")
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as reservation:
            root = Path(tmp)
            model = root / "invalid.gguf"
            mmproj = root / "invalid-mmproj.gguf"
            model.write_bytes(b"controlled invalid fixture")
            mmproj.write_bytes(b"controlled invalid fixture")
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            reservation.close()
            cfg = replace(
                AIConfig(), llama_server_exe=str(executable),
                llama_model_path=str(model), llama_mmproj_path=str(mmproj),
                llama_port=port, llama_startup_timeout=10,
            )
            manager = LlamaServerManager(cfg)
            started = time.monotonic()
            self.assertFalse(manager.ensure_running())
            self.assertLess(time.monotonic() - started, 10)
            self.assertIsNone(manager._proc)
            self.assertIn(manager.failure_kind, {"server_start_failure", "ram_oom", "cuda_oom"})


class Batch17RegressionGuards(unittest.TestCase):
    def test_schema_remains_v3(self):
        source = Path("src/utils/db_manager.py").read_text(encoding="utf-8")
        self.assertIn('(3, "organize_history table for Undo/History", self._migration_v3)', source)
        self.assertNotIn("_migration_v4", source)

    def test_batch16_profiles_unchanged(self):
        from src.ai.runtime_profile import _ALL_PROFILES
        self.assertEqual(len(_ALL_PROFILES), 3)
        self.assertEqual([p.context_size for p in _ALL_PROFILES], [32768, 8192, 2048])

    def test_no_shell_true_in_lifecycle(self):
        text = (Path("src/ai/server_manager.py").read_text(encoding="utf-8") +
                Path("src/ai/windows_job.py").read_text(encoding="utf-8"))
        self.assertNotIn("shell=True", text)


if __name__ == "__main__":
    unittest.main()

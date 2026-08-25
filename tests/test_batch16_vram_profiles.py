import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.ai.hardware_detector import HardwareInfo, HardwareDetector
from src.ai.runtime_profile import (
    ProfileSelector,
    RuntimeProfile,
    _ALL_PROFILES,
    PROFILE_QWEN3VL_8B_Q4KM_CUDA,
    PROFILE_QWEN3VL_8B_Q4KM_12GB,
    PROFILE_QWEN3VL_8B_Q4KM_8GB,
)
from src.ai.server_manager import LlamaServerManager
from src.ai.startup_worker import StartupWorker


def profile(name, minimum):
    return RuntimeProfile(
        name=name, description=name,
        model_filename="model.gguf", model_url="", model_sha256="0", model_size_bytes=1,
        mmproj_filename="mmproj.gguf", mmproj_url="", mmproj_sha256="0", mmproj_size_bytes=1,
        n_gpu_layers=1, context_size=1024, min_vram_mb=minimum, min_ram_mb=1024,
    )


P8, P12, P16 = profile("8gb", 8_000), profile("12gb", 12_000), profile("16gb", 16_000)


class TestBatch16DetectionAndSelection(unittest.TestCase):
    def test_vram_parsing(self):
        output = "NVIDIA Test, 16384, 12000\n"
        with patch("subprocess.check_output", return_value=output):
            result = HardwareDetector._query_nvidia_smi()
        self.assertEqual((result["vram_total_mb"], result["vram_free_mb"]), (16384, 12000))

    def test_boundaries_and_order(self):
        selector = ProfileSelector((P8, P16, P12))
        hw = HardwareInfo(True, "GPU", 16_000, 16_000, True, 32_000)
        self.assertEqual([p.name for p in selector.select_candidates(hw)], ["16gb", "12gb", "8gb"])

    def test_free_vram_prevents_unsafe_selection(self):
        selector = ProfileSelector((P8, P16, P12))
        hw = HardwareInfo(True, "GPU", 16_000, 11_000, True, 32_000)
        self.assertEqual([p.name for p in selector.select_candidates(hw)], ["8gb"])

    def test_unknown_vram_and_no_gpu(self):
        selector = ProfileSelector((P8,))
        unknown = HardwareInfo(True, "GPU", 0, 0, True, 32_000)
        none = HardwareInfo(False, "", 0, 0, False, 32_000)
        self.assertEqual(selector.select_candidates(unknown), ())
        self.assertEqual(selector.select_candidates(none), ())


class TestBatch16ServerFailureSafety(unittest.TestCase):
    def test_oom_classification_distinguishes_gpu_ram_and_other(self):
        self.assertEqual(LlamaServerManager._classify_start_failure("CUDA out of memory"), "cuda_oom")
        self.assertEqual(LlamaServerManager._classify_start_failure("std::bad_alloc"), "ram_oom")
        self.assertEqual(LlamaServerManager._classify_start_failure("invalid gguf"), "server_start_failure")

    def test_smoke_inference_requires_content(self):
        cfg = SimpleNamespace(
            chat_completions_url="http://local/v1/chat/completions", model="qwen", timeout=1,
        )
        manager = LlamaServerManager(cfg)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        with patch("src.ai.server_manager.requests.post", return_value=response):
            self.assertTrue(manager.smoke_inference())

    def test_smoke_inference_failure_is_typed(self):
        cfg = SimpleNamespace(
            chat_completions_url="http://local/v1/chat/completions", model="qwen", timeout=1,
        )
        manager = LlamaServerManager(cfg)
        with patch("src.ai.server_manager.requests.post", side_effect=TimeoutError("late")):
            self.assertFalse(manager.smoke_inference())
        self.assertEqual(manager.failure_kind, "inference_failure")

    def test_shutdown_cleans_owned_process(self):
        cfg = SimpleNamespace()
        manager = LlamaServerManager(cfg)
        proc = Mock(pid=123)
        manager._proc = proc
        manager.shutdown()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        self.assertIsNone(manager._proc)

    def test_readiness_timeout_cleans_failed_process(self):
        cfg = SimpleNamespace(
            llama_server_exe="server.exe", llama_model_path="model.gguf",
            llama_mmproj_path="mmproj.gguf", llama_n_gpu_layers=1,
            llama_context_size=1024, llama_host="127.0.0.1", llama_port=8080,
            llama_startup_timeout=1,
        )
        manager = LlamaServerManager(cfg)
        proc = Mock(pid=123)
        proc.poll.return_value = None
        with patch("src.ai.server_manager.os.path.isfile", return_value=True), \
             patch("src.ai.server_manager.subprocess.Popen", return_value=proc), \
             patch.object(manager, "_check_health", return_value=False), \
             patch("src.ai.server_manager.time.monotonic", side_effect=[0, 2]):
            self.assertFalse(manager._start())
        proc.terminate.assert_called_once()
        self.assertIsNone(manager._proc)
        self.assertEqual(manager.failure_kind, "readiness_failure")


class TestBatch16FallbackSequence(unittest.TestCase):
    def _run_worker(self, managers):
        cfg = SimpleNamespace(llama_model_path=str(Path("Z:/sjb/Clasq/.batch16/model.gguf")))
        hw = HardwareInfo(True, "GPU", 16_000, 16_000, True, 32_000)
        selector = Mock()
        selector.select_candidates.return_value = (P16, P12, P8)
        downloader = Mock()
        downloader.ensure_ready.return_value = True
        manager_factory = Mock(side_effect=managers)
        worker = StartupWorker()
        ready = []
        worker.ready.connect(lambda ok, error: ready.append((ok, error)))
        with patch("src.ai.config.AIConfig", return_value=cfg), \
             patch("src.ai.hardware_detector.HardwareDetector.detect", return_value=hw), \
             patch("src.ai.runtime_profile.ProfileSelector", return_value=selector), \
             patch("src.ai.model_downloader.ModelDownloader", return_value=downloader), \
             patch("src.ai.model_downloader._free_space_bytes", return_value=10**12), \
             patch("pathlib.Path.mkdir"), \
             patch("src.ai.server_manager.LlamaServerManager", manager_factory):
            worker.run()
        return worker, ready, managers

    @staticmethod
    def _manager(start=True, inference=True, kind=None, error="failed"):
        manager = Mock()
        manager.is_running.return_value = False
        manager.ensure_running.return_value = start
        manager.smoke_inference.return_value = inference
        manager.failure_kind = kind
        manager.error = error
        return manager

    def test_upper_failure_falls_back_once_then_infers(self):
        high = self._manager(start=False, kind="cuda_oom")
        middle = self._manager(start=True, inference=True, error=None)
        worker, ready, _ = self._run_worker([high, middle])
        self.assertEqual(ready, [(True, "")])
        self.assertEqual(worker.attempted_profiles, ["16gb", "12gb"])
        self.assertEqual(worker.selected_profile.name, "12gb")
        self.assertTrue(worker.fallback_occurred)
        high.shutdown.assert_called_once()
        middle.smoke_inference.assert_called_once()

    def test_each_profile_attempted_once_without_loop(self):
        managers = [self._manager(start=False, kind="cuda_oom") for _ in range(3)]
        worker, ready, _ = self._run_worker(managers)
        self.assertFalse(ready[-1][0])
        self.assertEqual(worker.attempted_profiles, ["16gb", "12gb", "8gb"])
        self.assertEqual([m.shutdown.call_count for m in managers], [1, 1, 1])

    def test_model_missing_does_not_masquerade_as_oom_or_fallback(self):
        missing = self._manager(start=False, kind="model_missing", error="model missing")
        worker, ready, _ = self._run_worker([missing])
        self.assertEqual(worker.failure_kind, "model_missing")
        self.assertEqual(worker.attempted_profiles, ["16gb"])
        self.assertEqual(ready, [(False, "model missing")])

    def test_inference_failure_cleans_before_next_profile(self):
        first = self._manager(start=True, inference=False, kind="inference_failure")
        second = self._manager(start=True, inference=True, error=None)
        worker, ready, _ = self._run_worker([first, second])
        self.assertEqual(ready, [(True, "")])
        first.shutdown.assert_called_once()
        self.assertEqual(worker.selected_profile.name, "12gb")


class TestBatch16SupplementProductionProfiles(unittest.TestCase):
    """Batch 16 보완: 실제 production profile 정의 및 Auto 선택 검증."""

    # ── 프로필 정의 존재 확인 ──────────────────────────────────────────────

    def test_all_three_production_profiles_defined(self):
        names = {p.name for p in _ALL_PROFILES}
        self.assertIn("qwen3vl-8b-q4km-cuda", names)   # 16GB
        self.assertIn("qwen3vl-8b-q4km-12gb", names)   # 12GB
        self.assertIn("qwen3vl-8b-q4km-8gb", names)    # 8GB

    def test_profiles_are_single_source_of_truth(self):
        self.assertIn(PROFILE_QWEN3VL_8B_Q4KM_CUDA, _ALL_PROFILES)
        self.assertIn(PROFILE_QWEN3VL_8B_Q4KM_12GB, _ALL_PROFILES)
        self.assertIn(PROFILE_QWEN3VL_8B_Q4KM_8GB, _ALL_PROFILES)

    # ── 동일 모델 파일 (단일 다운로드) ────────────────────────────────────

    def test_all_profiles_share_model_filename(self):
        filenames = {p.model_filename for p in _ALL_PROFILES}
        self.assertEqual(len(filenames), 1, "모든 프로필은 동일 모델 파일 사용")

    def test_all_profiles_share_mmproj_filename(self):
        filenames = {p.mmproj_filename for p in _ALL_PROFILES}
        self.assertEqual(len(filenames), 1, "모든 프로필은 동일 mmproj 파일 사용")

    def test_all_profiles_share_model_sha256(self):
        sha256s = {p.model_sha256 for p in _ALL_PROFILES}
        self.assertEqual(len(sha256s), 1, "SHA-256은 단일 source of truth")

    def test_all_profiles_share_model_url(self):
        urls = {p.model_url for p in _ALL_PROFILES}
        self.assertEqual(len(urls), 1, "모든 프로필은 동일 다운로드 URL 사용")

    # ── 프로필 ordering: VRAM 높을수록 context 크고 min_vram 높음 ─────────

    def test_min_vram_ordering_16_gt_12_gt_8(self):
        self.assertGreater(PROFILE_QWEN3VL_8B_Q4KM_CUDA.min_vram_mb,
                           PROFILE_QWEN3VL_8B_Q4KM_12GB.min_vram_mb)
        self.assertGreater(PROFILE_QWEN3VL_8B_Q4KM_12GB.min_vram_mb,
                           PROFILE_QWEN3VL_8B_Q4KM_8GB.min_vram_mb)

    def test_context_size_ordering_16_gte_12_gte_8(self):
        self.assertGreaterEqual(PROFILE_QWEN3VL_8B_Q4KM_CUDA.context_size,
                                PROFILE_QWEN3VL_8B_Q4KM_12GB.context_size)
        self.assertGreaterEqual(PROFILE_QWEN3VL_8B_Q4KM_12GB.context_size,
                                PROFILE_QWEN3VL_8B_Q4KM_8GB.context_size)

    def test_selector_returns_profiles_high_to_low(self):
        hw = HardwareInfo(True, "GPU", 32_000, 32_000, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        vram_order = [p.min_vram_mb for p in candidates]
        self.assertEqual(vram_order, sorted(vram_order, reverse=True))

    # ── min_vram_mb 임계값이 각 GPU 클래스에 맞는지 확인 ─────────────────

    def test_16gb_profile_threshold_fits_in_16gb_gpu(self):
        # 16GB GPU(16,384 MB) 전체보다 작아야 선택 가능
        self.assertLess(PROFILE_QWEN3VL_8B_Q4KM_CUDA.min_vram_mb, 16_384)

    def test_12gb_profile_threshold_fits_in_12gb_gpu(self):
        self.assertLess(PROFILE_QWEN3VL_8B_Q4KM_12GB.min_vram_mb, 12_288)

    def test_8gb_profile_threshold_fits_in_8gb_gpu(self):
        self.assertLess(PROFILE_QWEN3VL_8B_Q4KM_8GB.min_vram_mb, 8_192)

    # ── Auto 선택: 실제 HW 시뮬레이션 ──────────────────────────────────────

    def test_auto_select_16gb_profile_on_24gb_gpu(self):
        # RTX 3090 (24GB) → 16GB 프로필 선택
        hw = HardwareInfo(True, "RTX 3090", 24_576, 22_000, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0].name, "qwen3vl-8b-q4km-cuda")

    def test_auto_select_12gb_profile_on_12gb_gpu(self):
        # RTX 3060 12GB (11GB free) → 12GB 프로필 (16GB 프로필 요구 미충족)
        hw = HardwareInfo(True, "RTX 3060", 12_288, 11_000, True, 16_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertGreater(len(candidates), 0)
        self.assertNotEqual(candidates[0].name, "qwen3vl-8b-q4km-cuda")
        self.assertEqual(candidates[0].name, "qwen3vl-8b-q4km-12gb")

    def test_auto_select_8gb_profile_on_8gb_gpu(self):
        # RTX 3070 8GB (7.5GB free) → 8GB 프로필만 선택
        hw = HardwareInfo(True, "RTX 3070", 8_192, 7_500, True, 16_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0].name, "qwen3vl-8b-q4km-8gb")

    def test_no_profile_for_gpu_below_8gb_threshold(self):
        # 6GB GPU → 어떤 프로필도 선택 안 됨
        hw = HardwareInfo(True, "RTX 2060 Super", 6_144, 5_500, True, 16_000)
        sel = ProfileSelector()
        candidates = sel.select_candidates(hw)
        self.assertEqual(candidates, ())
        self.assertIn("지원하는 GPU 프로필이 없습니다", sel.reason)

    def test_free_vram_blocks_16gb_profile_on_loaded_gpu(self):
        # 24GB GPU지만 사용 중 (8GB free) → 16GB/12GB 미선택, 8GB만 선택
        hw = HardwareInfo(True, "RTX 3090", 24_576, 8_000, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        if candidates:
            self.assertEqual(candidates[0].name, "qwen3vl-8b-q4km-8gb")

    # ── threshold boundary ─────────────────────────────────────────────────

    def test_12gb_profile_boundary_just_below(self):
        threshold = PROFILE_QWEN3VL_8B_Q4KM_12GB.min_vram_mb
        hw = HardwareInfo(True, "GPU", threshold - 1, threshold - 1, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertTrue(all(c.name != "qwen3vl-8b-q4km-12gb" for c in candidates))

    def test_12gb_profile_boundary_exactly_at_threshold(self):
        threshold = PROFILE_QWEN3VL_8B_Q4KM_12GB.min_vram_mb
        hw = HardwareInfo(True, "GPU", threshold, threshold, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertTrue(any(c.name == "qwen3vl-8b-q4km-12gb" for c in candidates))

    def test_8gb_profile_boundary_just_below(self):
        threshold = PROFILE_QWEN3VL_8B_Q4KM_8GB.min_vram_mb
        hw = HardwareInfo(True, "GPU", threshold - 1, threshold - 1, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertTrue(all(c.name != "qwen3vl-8b-q4km-8gb" for c in candidates))

    def test_8gb_profile_boundary_exactly_at_threshold(self):
        threshold = PROFILE_QWEN3VL_8B_Q4KM_8GB.min_vram_mb
        hw = HardwareInfo(True, "GPU", threshold, threshold, True, 32_000)
        candidates = ProfileSelector().select_candidates(hw)
        self.assertTrue(any(c.name == "qwen3vl-8b-q4km-8gb" for c in candidates))


if __name__ == "__main__":
    unittest.main()

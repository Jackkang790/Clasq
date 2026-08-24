from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.filter_runtime_binaries import (
    ROOT_RUNTIME_DUPLICATE_ALLOWLIST,
    exclude_verified_root_duplicates,
)


class Batch19RuntimePruningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, relative: str, data: bytes = b"binary") -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def pair(self, name: str, *, root_data=b"same", runtime_data=b"same"):
        return [
            (name, self.source(f"root-source/{name}", root_data), "BINARY"),
            (f"runtime\\{name}", self.source(f"runtime-source/{name}", runtime_data), "BINARY"),
        ]

    def test_identical_root_duplicate_is_excluded(self):
        entries, removed = exclude_verified_root_duplicates(
            self.pair("llama.dll"), expected_names={"llama.dll"}
        )
        self.assertEqual([entry[0] for entry in entries], ["runtime\\llama.dll"])
        self.assertEqual(removed[0]["relative_path"], "llama.dll")

    def test_runtime_copy_and_hash_are_retained_in_report(self):
        entries, removed = exclude_verified_root_duplicates(
            self.pair("cudart64_12.dll"), expected_names={"cudart64_12.dll"}
        )
        self.assertEqual(entries[0][0], "runtime\\cudart64_12.dll")
        self.assertEqual(len(removed[0]["sha256"]), 64)
        self.assertEqual(removed[0]["size"], 4)

    def test_missing_runtime_counterpart_fails_closed(self):
        root = ("llama.dll", self.source("llama.dll"), "BINARY")
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            exclude_verified_root_duplicates([root], expected_names={"llama.dll"})

    def test_missing_root_counterpart_fails_closed(self):
        runtime = ("runtime/llama.dll", self.source("runtime/llama.dll"), "BINARY")
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            exclude_verified_root_duplicates([runtime], expected_names={"llama.dll"})

    def test_different_hash_is_never_excluded(self):
        with self.assertRaisesRegex(RuntimeError, "non-identical"):
            exclude_verified_root_duplicates(
                self.pair("llama.dll", root_data=b"one", runtime_data=b"two"),
                expected_names={"llama.dll"},
            )

    def test_non_allowlisted_binary_is_untouched(self):
        foreign = [("other.dll", self.source("other.dll"), "BINARY")]
        entries, removed = exclude_verified_root_duplicates(foreign, expected_names=set())
        self.assertEqual(entries, foreign)
        self.assertEqual(removed, [])

    def test_same_name_different_hash_msvc_runtimes_are_protected(self):
        entries = [
            ("VCRUNTIME140.dll", self.source("a/VCRUNTIME140.dll", b"a"), "BINARY"),
            ("runtime/VCRUNTIME140.dll", self.source("b/VCRUNTIME140.dll", b"b"), "BINARY"),
        ]
        filtered, removed = exclude_verified_root_duplicates(entries, expected_names=set())
        self.assertEqual(filtered, entries)
        self.assertEqual(removed, [])

    def test_cpu_and_cuda_backends_are_not_allowlisted(self):
        self.assertNotIn("ggml-cuda.dll", ROOT_RUNTIME_DUPLICATE_ALLOWLIST)
        self.assertFalse(any(name.startswith("ggml-cpu-") for name in ROOT_RUNTIME_DUPLICATE_ALLOWLIST))

    def test_allowlist_is_limited_to_reviewed_exact_duplicates(self):
        self.assertEqual(len(ROOT_RUNTIME_DUPLICATE_ALLOWLIST), 10)
        self.assertIn("cublasLt64_12.dll", ROOT_RUNTIME_DUPLICATE_ALLOWLIST)
        self.assertIn("llama-server-impl.dll", ROOT_RUNTIME_DUPLICATE_ALLOWLIST)

    def test_duplicate_destinations_fail_closed(self):
        entries = self.pair("llama.dll")
        entries.append(entries[0])
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            exclude_verified_root_duplicates(entries, expected_names={"llama.dll"})


if __name__ == "__main__":
    unittest.main()

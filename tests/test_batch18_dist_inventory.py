from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_dist import build_inventory, categorize, write_reports


class Batch18DistInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, data: bytes):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_file_size_hash_and_total(self):
        self.write("Clasq.exe", b"abc")
        report = build_inventory(self.root)
        self.assertEqual(report["file_count"], 1)
        self.assertEqual(report["total_bytes"], 3)
        self.assertEqual(len(report["files"][0]["sha256"]), 64)

    def test_same_content_same_name_duplicate(self):
        self.write("a/x.dll", b"same")
        self.write("b/x.dll", b"same")
        report = build_inventory(self.root)
        self.assertEqual(report["duplicate_group_count"], 1)
        self.assertEqual(report["duplicate_bytes"], 4)

    def test_different_name_same_hash_is_detected(self):
        self.write("a/one.dll", b"same")
        self.write("b/two.bin", b"same")
        group = build_inventory(self.root)["duplicate_groups"][0]
        self.assertEqual(group["paths"], ["a/one.dll", "b/two.bin"])

    def test_same_name_different_hash_is_not_duplicate(self):
        self.write("a/x.dll", b"one")
        self.write("b/x.dll", b"two")
        report = build_inventory(self.root)
        self.assertEqual(report["duplicate_group_count"], 0)
        self.assertEqual(len(report["same_name_different_hash"]), 1)

    def test_category_and_directory_totals(self):
        self.write("_internal/runtime/cublas64_12.dll", b"1234")
        self.write("_internal/PySide6/plugins/platforms/qwindows.dll", b"12")
        report = build_inventory(self.root)
        totals = {item["category"]: item["bytes"] for item in report["category_totals"]}
        self.assertEqual(totals["CUDA / NVIDIA runtime"], 4)
        self.assertEqual(totals["Qt plugins"], 2)

    def test_category_rules_distinguish_runtime_components(self):
        self.assertEqual(categorize("_internal/runtime/ffmpeg.exe"), "FFmpeg")
        self.assertEqual(categorize("_internal/runtime/llama.dll"), "llama.cpp runtime")
        self.assertEqual(categorize("_internal/runtime/cudart64_12.dll"), "CUDA / NVIDIA runtime")

    def test_reports_are_machine_readable(self):
        self.write("file.txt", b"content")
        inventory = build_inventory(self.root)
        json_path = self.root / "out" / "report.json"
        csv_path = self.root / "out" / "report.csv"
        write_reports(inventory, json_path, csv_path)
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["file_count"], 1)
        self.assertIn("relative_path", csv_path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()

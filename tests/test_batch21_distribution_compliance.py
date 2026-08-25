from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts.analyze_licenses import (
    load_inventory, summarize, validate_inventory, validate_license_files,
)


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "packaging" / "third-party-components.json"


class Batch21DistributionComplianceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = load_inventory(INVENTORY_PATH)
        cls.components = {item["id"]: item for item in cls.inventory["components"]}

    def test_inventory_schema_parses(self):
        self.assertEqual(self.inventory["schema_version"], 1)
        self.assertGreater(len(self.components), 10)

    def test_component_ids_are_unique(self):
        items = self.inventory["components"]
        self.assertEqual(len(items), len({item["id"] for item in items}))

    def test_all_components_have_version_license_and_evidence(self):
        for component in self.inventory["components"]:
            with self.subTest(component=component["id"]):
                self.assertTrue(component["version"])
                self.assertTrue(component["license"])
                self.assertTrue(component["evidence"])

    def test_unknown_blockers_are_machine_detectable_and_currently_absent(self):
        self.assertEqual(summarize(self.inventory)["UNKNOWN / BLOCKER"], 0)

    def test_all_declared_bundled_license_files_exist(self):
        self.assertEqual(validate_license_files(self.inventory, ROOT), [])

    def test_required_bundled_components_are_inventoryed(self):
        required = {"ffmpeg", "llama-cpp", "pyside6-qt", "python", "openssl", "nvidia-cuda"}
        self.assertTrue(required.issubset(self.components))
        self.assertTrue(all(self.components[item]["bundled"] for item in required))

    def test_build_only_dependencies_are_not_marked_bundled(self):
        self.assertFalse(self.components["pyinstaller"]["bundled"])
        self.assertFalse(self.components["pefile"]["bundled"])

    def test_downloaded_model_is_not_marked_bundled(self):
        model = self.components["qwen-model"]
        self.assertFalse(model["bundled"])
        self.assertIn("downloaded", model["role"])

    def test_ffmpeg_identity_matches_pinned_manifest(self):
        pinned = json.loads((ROOT / "packaging" / "ffmpeg-manifest.json").read_text(encoding="utf-8"))
        ffmpeg = self.components["ffmpeg"]
        self.assertEqual(ffmpeg["version"], pinned["version"])
        source_info = (ROOT / "FFMPEG_SOURCE_INFO.txt").read_text(encoding="utf-8")
        self.assertIn(pinned["sha256"], source_info)
        self.assertIn(str(pinned["size"]), source_info)

    def test_existing_packaged_ffmpeg_matches_manifest(self):
        binary = ROOT / "dist-batch21" / "Clasq" / "_internal" / "runtime" / "ffmpeg.exe"
        if not binary.is_file():
            binary = ROOT / "dist-batch20" / "Clasq" / "_internal" / "runtime" / "ffmpeg.exe"
        if not binary.is_file():
            self.skipTest("Batch 20 validated dist is not available")
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        pinned = json.loads((ROOT / "packaging" / "ffmpeg-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(digest, pinned["sha256"])

    def test_ffmpeg_technical_boundary_is_recorded_without_legal_conclusion(self):
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("separate process", notice)
        self.assertIn("No conclusion", notice)
        self.assertIn("LEGAL REVIEW REQUIRED", notice)

    def test_qt_virtual_keyboard_is_explicitly_review_required(self):
        component = self.components["qt-virtual-keyboard"]
        self.assertEqual(component["status"], "REVIEW REQUIRED")
        self.assertIn("GPL-3.0", component["license"])

    def test_official_license_texts_are_not_empty(self):
        paths = [
            "THIRD_PARTY_LICENSES/FFmpeg/COPYING.GPLv3",
            "THIRD_PARTY_LICENSES/FFmpeg/COPYING.LGPLv3",
            "THIRD_PARTY_LICENSES/llama.cpp/LICENSE",
            "THIRD_PARTY_LICENSES/Qt/GPL-3.0.txt",
            "THIRD_PARTY_LICENSES/Qt/LGPL-3.0.txt",
            "THIRD_PARTY_LICENSES/Python/LICENSE.txt",
        ]
        for relative in paths:
            with self.subTest(path=relative):
                self.assertGreater((ROOT / relative).stat().st_size, 500)

    def test_clasq_root_license_remains_apache_2(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0", text)

    def test_spec_uses_public_datas_for_compliance_files(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertIn('"THIRD_PARTY_NOTICES.md"', spec)
        self.assertIn('"THIRD_PARTY_LICENSES"', spec)
        self.assertIn("*compliance_files", spec)

    def test_spec_preserves_ffmpeg_runtime_destination(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertIn('binaries.append((str(ffmpeg_source), "runtime"))', spec)

    def test_spec_preserves_batch19_duplicate_filter(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertIn("exclude_verified_root_duplicates(a.binaries)", spec)

    def test_invalid_inventory_duplicate_id_fails(self):
        data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        data["components"].append(dict(data["components"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate component id"):
            validate_inventory(data)

    def test_invalid_inventory_status_fails(self):
        data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        data["components"][0]["status"] = "SAFE"
        with self.assertRaisesRegex(ValueError, "invalid status"):
            validate_inventory(data)


if __name__ == "__main__":
    unittest.main()

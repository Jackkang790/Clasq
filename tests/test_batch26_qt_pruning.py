import hashlib
import json
import re
import shutil
import unittest
import uuid
from pathlib import Path

import pefile

from scripts.filter_qt_runtime import (
    QT_UNUSED_RUNTIME_ALLOWLIST,
    exclude_verified_unused_qt_runtime,
)


ROOT = Path(__file__).resolve().parents[1]
DIST25 = ROOT / "dist-batch25" / "Clasq"
DIST26 = ROOT / "dist-batch26" / "Clasq"


class Batch26QtPruningTests(unittest.TestCase):
    def test_source_has_no_qml_quick_or_virtual_keyboard_usage(self):
        patterns = re.compile(
            r"PySide6\.Qt(?:Qml|Quick|QuickWidgets)|QQuick(?:View|Widget)|"
            r"QQml(?:ApplicationEngine|Engine)|QtVirtualKeyboard|qrc:/",
            re.IGNORECASE,
        )
        matches = []
        for path in (ROOT / "src").rglob("*.py"):
            if patterns.search(path.read_text(encoding="utf-8")):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_repository_has_no_qml_resources(self):
        qml = [p for p in ROOT.rglob("*.qml") if not any(
            part.startswith(("build-", "dist-")) or part == ".tmp" for part in p.parts
        )]
        self.assertEqual(qml, [])

    def test_allowlist_is_exact_reviewed_scope(self):
        self.assertEqual(len(QT_UNUSED_RUNTIME_ALLOWLIST), 7)
        self.assertEqual(sum(v[0] for v in QT_UNUSED_RUNTIME_ALLOWLIST.values()), 13_688_968)
        for name in QT_UNUSED_RUNTIME_ALLOWLIST:
            self.assertRegex(name, r"(?i)(qml|quick|virtualkeyboard)")

    def test_batch25_candidate_inventory_matches_allowlist(self):
        actual = {}
        for destination in QT_UNUSED_RUNTIME_ALLOWLIST:
            path = DIST25 / "_internal" / Path(destination)
            actual[destination] = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(actual, QT_UNUSED_RUNTIME_ALLOWLIST)

    def test_virtual_keyboard_plugin_starts_dependency_chain(self):
        plugin = DIST25 / "_internal/PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll"
        imports = {item.dll.decode().casefold() for item in pefile.PE(str(plugin)).DIRECTORY_ENTRY_IMPORT}
        self.assertIn("qt6virtualkeyboard.dll", imports)

    def test_virtual_keyboard_dll_imports_qml_and_quick(self):
        dll = DIST25 / "_internal/PySide6/Qt6VirtualKeyboard.dll"
        imports = {item.dll.decode().casefold() for item in pefile.PE(str(dll)).DIRECTORY_ENTRY_IMPORT}
        self.assertIn("qt6qml.dll", imports)
        self.assertIn("qt6quick.dll", imports)

    def test_filter_removes_only_allowlist(self):
        entries = []
        for destination in QT_UNUSED_RUNTIME_ALLOWLIST:
            entries.append((destination, str(DIST25 / "_internal" / Path(destination)), "BINARY"))
        protected = ("PySide6/Qt6Core.dll", str(DIST25 / "_internal/PySide6/Qt6Core.dll"), "BINARY")
        entries.append(protected)
        kept, removed = exclude_verified_unused_qt_runtime(entries)
        self.assertEqual(kept, [protected])
        self.assertEqual({x["relative_path"] for x in removed}, set(QT_UNUSED_RUNTIME_ALLOWLIST))

    def test_filter_fails_when_candidate_missing(self):
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            exclude_verified_unused_qt_runtime([])

    def test_filter_fails_when_candidate_hash_changes(self):
        entries = [(d, str(DIST25 / "_internal" / Path(d)), "BINARY") for d in QT_UNUSED_RUNTIME_ALLOWLIST]
        temp = ROOT / ".tmp" / "batch26" / uuid.uuid4().hex
        temp.mkdir(parents=True)
        changed = temp / "Qt6Qml.dll"
        changed.write_bytes(b"changed")
        entries[entries.index(next(x for x in entries if x[0] == "PySide6/Qt6Qml.dll"))] = (
            "PySide6/Qt6Qml.dll", str(changed), "BINARY"
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "Refusing to exclude changed"):
                exclude_verified_unused_qt_runtime(entries)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_protected_qt_components_are_not_allowlisted(self):
        protected = {
            "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "qwindows.dll",
            "Qt6Svg.dll", "Qt6SvgWidgets.dll", "opengl32sw.dll", "Qt6Network.dll", "Qt6Pdf.dll",
        }
        names = {Path(name).name for name in QT_UNUSED_RUNTIME_ALLOWLIST}
        self.assertTrue(protected.isdisjoint(names))

    def test_spec_uses_analysis_excludes_and_fail_closed_filter(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        for module in ("PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets"):
            self.assertIn(f'"{module}"', spec)
        self.assertIn("exclude_verified_unused_qt_runtime(a.binaries)", spec)

    def test_qt_filter_runs_after_batch19_duplicate_filter(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertLess(spec.index("exclude_verified_root_duplicates(a.binaries)"),
                        spec.index("exclude_verified_unused_qt_runtime(a.binaries)"))

    def test_no_post_build_delete_strategy(self):
        source = (ROOT / "scripts/filter_qt_runtime.py").read_text(encoding="utf-8")
        for forbidden in ("unlink(", "rmtree(", "remove(", "dist-batch26"):
            self.assertNotIn(forbidden, source)

    def test_virtual_keyboard_inventory_is_not_bundled(self):
        data = json.loads((ROOT / "packaging/third-party-components.json").read_text(encoding="utf-8"))
        item = next(x for x in data["components"] if x["id"] == "qt-virtual-keyboard")
        self.assertFalse(item["bundled"])
        self.assertIn("not bundled", item["role"])

    def test_notices_keep_other_qt_review(self):
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Not bundled in the Batch 26 Windows one-dir", notice)
        self.assertIn("other Qt review remains", notice)
        self.assertTrue((ROOT / "THIRD_PARTY_LICENSES/Qt/GPL-3.0.txt").is_file())

    def test_batch26_has_no_candidate_runtime(self):
        self.assertTrue(DIST26.is_dir(), "clean Batch 26 dist is required")
        remaining = [name for name in QT_UNUSED_RUNTIME_ALLOWLIST if (DIST26 / "_internal" / Path(name)).exists()]
        self.assertEqual(remaining, [])

    def test_batch26_protects_required_qt_runtime(self):
        required = [
            "_internal/PySide6/Qt6Core.dll", "_internal/PySide6/Qt6Gui.dll",
            "_internal/PySide6/Qt6Widgets.dll", "_internal/PySide6/plugins/platforms/qwindows.dll",
            "_internal/PySide6/Qt6Svg.dll", "_internal/PySide6/Qt6SvgWidgets.dll",
            "_internal/PySide6/opengl32sw.dll", "_internal/PySide6/Qt6Network.dll",
            "_internal/PySide6/Qt6Pdf.dll",
        ]
        self.assertEqual([x for x in required if not (DIST26 / Path(x)).is_file()], [])

    def test_batch26_keeps_plugins_and_translations(self):
        required = [
            "_internal/PySide6/plugins/imageformats/qjpeg.dll",
            "_internal/PySide6/plugins/iconengines/qsvgicon.dll",
            "_internal/PySide6/plugins/imageformats/qsvg.dll",
            "_internal/PySide6/translations/qtbase_ko.qm",
        ]
        self.assertEqual([x for x in required if not (DIST26 / Path(x)).is_file()], [])

    def test_db_schema_v3_is_unchanged(self):
        source = (ROOT / "src/utils/db_manager.py").read_text(encoding="utf-8")
        self.assertIn("schema v3", source)


if __name__ == "__main__":
    unittest.main()

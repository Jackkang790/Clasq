import hashlib
import io
import re
import shutil
import unittest
import uuid
from pathlib import Path

from PIL import Image

from scripts.filter_pillow_runtime import (
    PILLOW_UNUSED_RUNTIME_ALLOWLIST,
    exclude_verified_unused_pillow_runtime,
)
from src.ai.image_analyzer import ImageAnalyzer
from src.utils.file_pipeline import ExtensionTagger, TextExtractor


ROOT = Path(__file__).resolve().parents[1]
DIST26 = ROOT / "dist-batch26" / "Clasq"
DIST27 = ROOT / "dist-batch27" / "Clasq"
SUPPORTED = ("PNG", "JPEG", "WEBP", "BMP", "GIF", "TIFF")


class Batch27PillowPruningTests(unittest.TestCase):
    def test_source_has_no_avif_imagetk_or_tkinter_usage(self):
        pattern = re.compile(r"(?i)PIL\.ImageTk|ImageTk|tkinter|\.avifs?\b")
        matches = []
        for path in (ROOT / "src").rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_repository_has_no_avif_assets(self):
        assets = [p for p in ROOT.rglob("*.avif") if not any(
            part.startswith(("build-", "dist-")) or part == ".tmp" for part in p.parts
        )]
        self.assertEqual(assets, [])

    def test_product_image_allowlists_exclude_avif(self):
        sources = [
            ROOT / "src/utils/config.py", ROOT / "src/utils/file_pipeline.py",
            ROOT / "src/ui/ai_workers.py", ROOT / "src/ui/views/organize_view.py",
        ]
        self.assertTrue(all(".avif" not in p.read_text(encoding="utf-8").casefold() for p in sources))

    def test_avif_is_gracefully_outside_product_image_pipeline(self):
        extractor = TextExtractor()
        self.assertFalse(extractor.is_image_file("sample.avif"))
        self.assertEqual(ExtensionTagger.tag_for("sample.avif"), ExtensionTagger.DEFAULT_TAG)

    def test_candidate_allowlist_is_exact(self):
        self.assertEqual(set(PILLOW_UNUSED_RUNTIME_ALLOWLIST), {
            "PIL/_avif.cp313-win_amd64.pyd", "PIL/_imagingtk.cp313-win_amd64.pyd",
        })
        self.assertEqual(sum(x[0] for x in PILLOW_UNUSED_RUNTIME_ALLOWLIST.values()), 7_905_792)

    def test_batch26_candidate_inventory_matches(self):
        actual = {}
        for destination in PILLOW_UNUSED_RUNTIME_ALLOWLIST:
            path = DIST26 / "_internal" / Path(destination)
            actual[destination] = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(actual, PILLOW_UNUSED_RUNTIME_ALLOWLIST)

    def test_filter_removes_only_allowlist(self):
        entries = [(d, str(DIST26 / "_internal" / Path(d)), "EXTENSION")
                   for d in PILLOW_UNUSED_RUNTIME_ALLOWLIST]
        protected = ("PIL/_imaging.cp313-win_amd64.pyd",
                     str(DIST26 / "_internal/PIL/_imaging.cp313-win_amd64.pyd"), "EXTENSION")
        entries.append(protected)
        kept, removed = exclude_verified_unused_pillow_runtime(entries)
        self.assertEqual(kept, [protected])
        self.assertEqual({x["relative_path"] for x in removed}, set(PILLOW_UNUSED_RUNTIME_ALLOWLIST))

    def test_filter_is_fail_closed_for_missing_candidate(self):
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            exclude_verified_unused_pillow_runtime([])

    def test_filter_is_fail_closed_for_changed_hash(self):
        entries = [(d, str(DIST26 / "_internal" / Path(d)), "EXTENSION")
                   for d in PILLOW_UNUSED_RUNTIME_ALLOWLIST]
        temp = ROOT / ".tmp" / "batch27" / uuid.uuid4().hex
        temp.mkdir(parents=True)
        changed = temp / "_avif.cp313-win_amd64.pyd"
        changed.write_bytes(b"changed")
        entries[0] = (entries[0][0], str(changed), entries[0][2])
        try:
            with self.assertRaisesRegex(RuntimeError, "Refusing to exclude changed"):
                exclude_verified_unused_pillow_runtime(entries)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_core_extensions_are_not_allowlisted(self):
        protected = {"_imaging.cp313-win_amd64.pyd", "_imagingft.cp313-win_amd64.pyd",
                     "_imagingcms.cp313-win_amd64.pyd", "_webp.cp313-win_amd64.pyd"}
        self.assertTrue(protected.isdisjoint({Path(x).name for x in PILLOW_UNUSED_RUNTIME_ALLOWLIST}))

    def test_spec_filter_order_and_no_post_build_delete(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertLess(spec.index("exclude_verified_unused_qt_runtime(a.binaries)"),
                        spec.index("exclude_verified_unused_pillow_runtime(a.binaries)"))
        source = (ROOT / "scripts/filter_pillow_runtime.py").read_text(encoding="utf-8")
        for forbidden in ("unlink(", "rmtree(", "remove(", "dist-batch27"):
            self.assertNotIn(forbidden, source)

    def test_supported_image_formats_load_and_initialize_plugins(self):
        for fmt in SUPPORTED:
            with self.subTest(fmt=fmt):
                stream = io.BytesIO()
                Image.new("RGB", (4, 3), (12, 34, 56)).save(stream, fmt)
                stream.seek(0)
                with Image.open(stream) as image:
                    image.load()
                    self.assertEqual(image.size, (4, 3))
        Image.init()
        self.assertIn("PNG", Image.OPEN)
        self.assertIn("JPEG", Image.OPEN)
        self.assertIn("WEBP", Image.OPEN)

    def test_pillow_plugin_initialization_tolerates_missing_avif_extension(self):
        # Pillow's Image.init() intentionally treats an unavailable optional
        # plugin as ImportError and continues registering the other decoders.
        import builtins
        import sys
        from unittest import mock

        real_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name in {"PIL.AvifImagePlugin", "PIL._avif"} or name.endswith("._avif"):
                raise ImportError("optional AVIF extension excluded")
            return real_import(name, *args, **kwargs)

        old_initialized = Image._initialized
        avif_plugin = sys.modules.pop("PIL.AvifImagePlugin", None)
        avif_binary = sys.modules.pop("PIL._avif", None)
        try:
            Image._initialized = 0
            with mock.patch("builtins.__import__", side_effect=guarded_import):
                Image.init()
            self.assertIn("PNG", Image.OPEN)
            self.assertIn("JPEG", Image.OPEN)
        finally:
            Image._initialized = old_initialized
            if avif_plugin is not None:
                sys.modules["PIL.AvifImagePlugin"] = avif_plugin
            if avif_binary is not None:
                sys.modules["PIL._avif"] = avif_binary

    def test_unicode_and_space_path_preprocessing(self):
        folder = ROOT / ".tmp" / "batch27" / "테스트 이미지 공백"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "샘플 이미지.png"
        Image.new("RGB", (8, 6), (20, 40, 60)).save(path)
        try:
            data, status = TextExtractor().process_image(str(path))
            self.assertEqual(status, "SUCCESS")
            self.assertTrue(data.startswith(b"\xff\xd8"))
        finally:
            path.unlink(missing_ok=True)

    def test_image_ai_ocr_preprocessing(self):
        class Config:
            image_ocr_upscale_factor = 2
            image_ocr_small_max_edge = 100
        class Client:
            config = Config()
        stream = io.BytesIO()
        Image.new("RGB", (10, 8), "white").save(stream, "PNG")
        folder = ROOT / ".tmp" / "batch27"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "ocr.png"
        path.write_bytes(stream.getvalue())
        try:
            result = ImageAnalyzer(client=Client()).prepare_ocr_data_url(str(path))
            self.assertTrue(result.startswith("data:image/png;base64,"))
        finally:
            path.unlink(missing_ok=True)

    def test_batch27_candidate_runtime_is_absent(self):
        self.assertTrue(DIST27.is_dir(), "clean Batch 27 dist is required")
        remaining = [x for x in PILLOW_UNUSED_RUNTIME_ALLOWLIST
                     if (DIST27 / "_internal" / Path(x)).exists()]
        self.assertEqual(remaining, [])

    def test_batch27_protects_pillow_core_and_webp(self):
        required = ["_imaging.cp313-win_amd64.pyd", "_imagingft.cp313-win_amd64.pyd",
                    "_imagingcms.cp313-win_amd64.pyd", "_webp.cp313-win_amd64.pyd"]
        missing = [x for x in required if not (DIST27 / "_internal/PIL" / x).is_file()]
        self.assertEqual(missing, [])

    def test_batch26_qt_pruning_remains(self):
        removed = ["Qt6Qml.dll", "Qt6Quick.dll", "Qt6VirtualKeyboard.dll"]
        self.assertEqual([x for x in removed if (DIST27 / "_internal/PySide6" / x).exists()], [])

    def test_diagnostic_exporter_and_pillow_notice_remain(self):
        self.assertTrue((ROOT / "src/utils/diagnostic_bundle.py").is_file())
        self.assertTrue((DIST27 / "_internal/THIRD_PARTY_LICENSES/Python-Packages/Pillow-LICENSE").is_file())

    def test_ffmpeg_hash_is_unchanged(self):
        path = DIST27 / "_internal/runtime/ffmpeg.exe"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "ad8f211bc894755e0061c55ab280ae00e8d3d4f15a8cc4372b24cfa247b5942e")

    def test_db_schema_v3_is_unchanged(self):
        self.assertIn("schema v3", (ROOT / "src/utils/db_manager.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

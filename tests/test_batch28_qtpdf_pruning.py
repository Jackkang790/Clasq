import hashlib
import sqlite3
import unittest
import uuid
from pathlib import Path

import pefile
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts.filter_qtpdf_runtime import (
    QTPDF_UNUSED_RUNTIME_ALLOWLIST,
    exclude_verified_unused_qtpdf_runtime,
)
from src.utils.file_pipeline import TextExtractor
from src.utils.local_text_index import LocalTextIndexer
from src.utils.search_snapshot import refresh_search_snapshot


ROOT = Path(__file__).resolve().parents[1]
DIST27 = ROOT / "dist-batch27" / "Clasq"
DIST28 = ROOT / "dist-batch28" / "Clasq"
TOKEN = "CLASQ_BATCH28_PDF_TEST"


def make_pdf(path: Path, text: str = TOKEN) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    contents = DecodedStreamObject()
    contents.set_data(f"BT /F1 12 Tf 40 120 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(contents)
    with path.open("wb") as stream:
        writer.write(stream)


class Batch28QtPdfPruningTests(unittest.TestCase):
    def test_source_has_no_qtpdf_api_usage(self):
        needles = ("PySide6.QtPdf", "QtPdfWidgets", "QtPdfQuick", "QPdfDocument",
                   "QPdfView", "QPdfPageRenderer", "QPdfSearchModel")
        matches = []
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(x in text for x in needles):
                matches.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(matches, [])

    def test_pdf_architecture_uses_pypdf(self):
        pipeline = (ROOT / "src/utils/file_pipeline.py").read_text(encoding="utf-8")
        indexer = (ROOT / "src/utils/local_text_index.py").read_text(encoding="utf-8")
        self.assertIn("from pypdf import PdfReader", pipeline)
        self.assertIn("reader = PdfReader(path)", pipeline)
        self.assertIn("\".pdf\": \"pypdf\"", indexer)

    def test_candidate_allowlist_is_exact(self):
        self.assertEqual(set(QTPDF_UNUSED_RUNTIME_ALLOWLIST), {
            "PySide6/plugins/imageformats/qpdf.dll", "PySide6/Qt6Pdf.dll",
        })
        self.assertEqual(sum(v[0] for v in QTPDF_UNUSED_RUNTIME_ALLOWLIST.values()), 4_653_680)

    def test_batch27_candidate_inventory_matches(self):
        actual = {}
        for destination in QTPDF_UNUSED_RUNTIME_ALLOWLIST:
            path = DIST27 / "_internal" / Path(destination)
            actual[destination] = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(actual, QTPDF_UNUSED_RUNTIME_ALLOWLIST)

    def test_qpdf_plugin_imports_qt6pdf(self):
        path = DIST27 / "_internal/PySide6/plugins/imageformats/qpdf.dll"
        pe = pefile.PE(str(path))
        imports = {x.dll.decode().casefold() for x in pe.DIRECTORY_ENTRY_IMPORT}
        pe.close()
        self.assertIn("qt6pdf.dll", imports)

    def test_filter_removes_only_allowlist(self):
        entries = [(d, str(DIST27 / "_internal" / Path(d)), "BINARY")
                   for d in QTPDF_UNUSED_RUNTIME_ALLOWLIST]
        protected = ("PySide6/Qt6Core.dll",
                     str(DIST27 / "_internal/PySide6/Qt6Core.dll"), "BINARY")
        entries.append(protected)
        kept, removed = exclude_verified_unused_qtpdf_runtime(entries)
        self.assertEqual(kept, [protected])
        self.assertEqual({x["relative_path"] for x in removed}, set(QTPDF_UNUSED_RUNTIME_ALLOWLIST))

    def test_filter_fails_closed_when_missing(self):
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            exclude_verified_unused_qtpdf_runtime([])

    def test_filter_fails_closed_when_hash_changes(self):
        entries = [(d, str(DIST27 / "_internal" / Path(d)), "BINARY")
                   for d in QTPDF_UNUSED_RUNTIME_ALLOWLIST]
        changed = ROOT / ".tmp/batch28/changed.dll"
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_bytes(uuid.uuid4().bytes)
        entries[0] = (entries[0][0], str(changed), entries[0][2])
        with self.assertRaisesRegex(RuntimeError, "Refusing to exclude changed"):
            exclude_verified_unused_qtpdf_runtime(entries)

    def test_protected_qt_is_not_allowlisted(self):
        protected = {"Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "qwindows.dll",
                     "Qt6Svg.dll", "Qt6Network.dll", "Qt6PrintSupport.dll", "qjpeg.dll"}
        self.assertTrue(protected.isdisjoint({Path(x).name for x in QTPDF_UNUSED_RUNTIME_ALLOWLIST}))

    def test_spec_order_and_no_post_build_delete(self):
        spec = (ROOT / "clasq.spec").read_text(encoding="utf-8")
        self.assertLess(spec.index("exclude_verified_unused_pillow_runtime(a.binaries)"),
                        spec.index("exclude_verified_unused_qtpdf_runtime(a.binaries)"))
        source = (ROOT / "scripts/filter_qtpdf_runtime.py").read_text(encoding="utf-8")
        for forbidden in ("unlink(", "rmtree(", "remove(", "dist-batch28", "*Pdf*"):
            self.assertNotIn(forbidden, source)

    def test_pdf_recognition_and_text_extraction_unicode_space_path(self):
        folder = ROOT / ".tmp/batch28/테스트 PDF 공백"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "문서 샘플.pdf"
        make_pdf(path)
        text, status = TextExtractor().extract(str(path))
        self.assertEqual(status, "SUCCESS")
        self.assertIn(TOKEN, text)
        self.assertEqual(PdfReader(str(path)).pages[0].extract_text().strip(), TOKEN)

    def test_pdf_indexing_and_search_snapshot(self):
        folder = ROOT / ".tmp/batch28/index"
        folder.mkdir(parents=True, exist_ok=True)
        path, db = folder / "indexed.pdf", folder / "index.db"
        make_pdf(path)
        stats = LocalTextIndexer(str(db)).synchronize([str(path)])
        self.assertEqual(stats["success"], 1)
        connection = sqlite3.connect(db)
        row = connection.execute(
            "SELECT extracted_text, extractor_type FROM file_text_index WHERE file_path=?", (str(path),)
        ).fetchone()
        connection.close()
        self.assertIn(TOKEN, row[0])
        self.assertEqual(row[1], "pypdf")
        snapshot = refresh_search_snapshot(str(db))
        self.assertTrue(any(TOKEN.casefold() in item.normalized_text for item in snapshot.records))

    def test_malformed_pdf_is_graceful(self):
        path = ROOT / ".tmp/batch28/malformed.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"NOT A PDF")
        text, status = TextExtractor().extract(str(path))
        self.assertEqual(text, "")
        self.assertTrue(status.startswith("ERROR:"))

    def test_batch28_candidates_are_absent(self):
        self.assertTrue(DIST28.is_dir(), "clean Batch 28 dist is required")
        self.assertEqual([x for x in QTPDF_UNUSED_RUNTIME_ALLOWLIST
                          if (DIST28 / "_internal" / Path(x)).exists()], [])

    def test_batch28_protects_qt_and_pypdf(self):
        required = [
            "_internal/PySide6/Qt6Core.dll", "_internal/PySide6/Qt6Gui.dll",
            "_internal/PySide6/Qt6Widgets.dll", "_internal/PySide6/Qt6Network.dll",
            "_internal/PySide6/plugins/platforms/qwindows.dll",
            "_internal/PySide6/plugins/imageformats/qjpeg.dll",
            "_internal/PySide6/translations/qtbase_ko.qm",
        ]
        self.assertEqual([x for x in required if not (DIST28 / x).is_file()], [])
        toc = (ROOT / "build-batch28/clasq/PYZ-00.toc").read_text(encoding="utf-8")
        self.assertIn("'pypdf'", toc)

    def test_batch26_and_27_pruning_remain(self):
        removed = [
            "_internal/PySide6/Qt6Qml.dll", "_internal/PySide6/Qt6Quick.dll",
            "_internal/PySide6/Qt6VirtualKeyboard.dll",
            "_internal/PIL/_avif.cp313-win_amd64.pyd",
            "_internal/PIL/_imagingtk.cp313-win_amd64.pyd",
        ]
        self.assertEqual([x for x in removed if (DIST28 / x).exists()], [])

    def test_qtpdf_inventory_and_notices_are_consistent(self):
        import json
        data = json.loads((ROOT / "packaging/third-party-components.json").read_text(encoding="utf-8"))
        item = next(x for x in data["components"] if x["id"] == "qt-pdf")
        self.assertFalse(item["bundled"])
        self.assertEqual(item["status"], "REVIEW REQUIRED")
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Qt PDF", notice)
        self.assertTrue((ROOT / "THIRD_PARTY_LICENSES/Qt/LGPL-3.0.txt").is_file())

    def test_ffmpeg_and_diagnostic_export_are_preserved(self):
        ffmpeg = DIST28 / "_internal/runtime/ffmpeg.exe"
        self.assertEqual(hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
                         "ad8f211bc894755e0061c55ab280ae00e8d3d4f15a8cc4372b24cfa247b5942e")
        self.assertTrue((ROOT / "src/utils/diagnostic_bundle.py").is_file())

    def test_db_schema_v3_is_unchanged(self):
        self.assertIn("schema v3", (ROOT / "src/utils/db_manager.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.utils.core import ClasqCore
from src.utils.db_manager import FileRegistryManager
from src.utils.query_parser import SearchQueryParser
from src.utils.search_engine import SearchEngine


class _ConversationParser:
    def parse_user_query(self, text):
        return {"status": "SUCCESS", "data": {"@TYPE": "@대화", "reply_text": "일반 대화"}}


class SearchIntentRetrievalFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = str(self.root / "acceptance.db")
        FileRegistryManager(db_path=self.db)
        self.rows = [
            ("sample.pdf", "PDF", "pdf memo"),
            ("sample.png", "Image", "image memo"),
            ("sample.mp4", "Video", "video memo"),
            ("overview.md", "Markdown", "overview summary"),
            ("notes.txt", "Reference", "neutral memo"),
            ("data.json", "JSON", "json memo"),
            ("sheet.xlsx", "Spreadsheet", "excel memo"),
        ]
        conn = sqlite3.connect(self.db)
        try:
            for name, category, comment in self.rows:
                path = str(self.root / name)
                conn.execute(
                    "INSERT INTO files(file_name,file_path,ai_comment,category,file_modified_at) VALUES(?,?,?,?,?)",
                    (name, path, comment, category, date.today().isoformat()),
                )
            notes = str(self.root / "notes.txt")
            conn.execute(
                "INSERT INTO file_text_index(file_path,extracted_text,extract_status,extractor_type) VALUES(?,?,?,?)",
                (notes, "A handbook chapter about Cryptography primitives.", "success", "text"),
            )
            conn.commit()
        finally:
            conn.close()
        self.engine = SearchEngine(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def guarded(self, text, parsed=None):
        return SearchQueryParser._apply_intent_guards(
            text, parsed or {"@TYPE": "@대화", "date_range": {"start": "2026-01-01", "end": "2026-01-01"}},
        )

    def execute(self, text):
        parsed = self.guarded(text)
        return parsed, self.engine.process_query_result(parsed)

    def test_inventory_uses_actual_database_counts(self):
        parsed, result = self.execute("무슨 파일이 있니")
        self.assertEqual(parsed["intent"], "inventory")
        self.assertEqual(result["action"], "SHOW_INVENTORY")
        self.assertEqual(result["inventory"]["total"], 7)
        self.assertEqual(result["inventory"]["types"]["PDF"], 1)
        self.assertNotIn("audio", result["message"])

    def test_video_type_only_has_no_filler_or_date_and_returns_fixture(self):
        parsed, result = self.execute("비디오 파일 아무거나")
        self.assertEqual(parsed["query_keywords"], [])
        self.assertIsNone(parsed["date_range"])
        self.assertIn(".mp4", parsed["target_extension"])
        self.assertNotIn("연관 키워드", result["message"])
        self.assertEqual([row[1] for row in result["data"]], ["sample.mp4"])

    def test_pdf_type_only_has_no_filler_or_date_and_returns_fixture(self):
        parsed, result = self.execute("PDF 파일 보여줘")
        self.assertEqual(parsed["query_keywords"], [])
        self.assertIsNone(parsed["date_range"])
        self.assertEqual(parsed["target_extension"], [".pdf"])
        self.assertEqual([row[1] for row in result["data"]], ["sample.pdf"])

    def test_image_type_only_has_no_filler_or_date_and_returns_fixture(self):
        parsed, result = self.execute("이미지 파일 있어?")
        self.assertEqual(parsed["query_keywords"], [])
        self.assertIsNone(parsed["date_range"])
        self.assertEqual([row[1] for row in result["data"]], ["sample.png"])

    def test_type_with_real_content_keyword_is_preserved(self):
        parsed = self.guarded("PDF에서 암호화 내용 찾아줘")
        self.assertEqual(parsed["target_extension"], [".pdf"])
        self.assertEqual(parsed["query_keywords"], ["암호화"])

    def test_bare_stem_bypasses_llm_conversation_only_when_file_exists(self):
        core = ClasqCore(db_path=self.db)
        core.query_parser = _ConversationParser()
        result = core.process_user_query("overview")
        self.assertEqual(result["retrieval_path"], "exact_filename_or_stem")
        self.assertEqual(result["data"][0][1], "overview.md")
        chat = core.process_user_query("안녕")
        self.assertEqual(chat["action"], "SHOW_CHAT")

    def test_content_index_is_retrieved_without_metadata_match(self):
        result, _ = self.engine.search_files_smart(["Cryptography"])
        self.assertEqual([row[1] for row in result], ["notes.txt"])

    def test_single_result_followup_uses_file_context(self):
        core = ClasqCore(db_path=self.db)
        core.query_parser = _ConversationParser()
        core.process_user_query("overview")
        result = core.process_user_query("요약해줘")
        self.assertEqual(result["context_file"], str(self.root / "overview.md"))
        self.assertIn("overview summary", result["message"])

    def test_multiple_results_followup_requires_selection(self):
        core = ClasqCore(db_path=self.db)
        core._last_search_results = self.engine.search_files_smart([], [".md", ".txt"])[0]
        result = core.process_user_query("요약해줘")
        self.assertIn("선택", result["message"])

    def test_date_positive_and_negative_guard(self):
        positive = self.guarded("오늘 PDF 파일 보여줘")
        negative = self.guarded("PDF 파일 보여줘")
        self.assertEqual(positive["date_range"], {"start": date.today().isoformat(), "end": date.today().isoformat()})
        self.assertIsNone(negative["date_range"])
        self.assertEqual(positive["query_keywords"], [])

    def test_metadata_fields_and_type_regressions(self):
        self.assertEqual(self.engine.search_files_smart(["Reference"])[0][0][1], "notes.txt")
        self.assertEqual(self.engine.search_files_smart(["neutral"])[0][0][1], "notes.txt")
        self.assertEqual(self.engine.search_files_smart(["overview"])[0][0][1], "overview.md")
        parsed = self.guarded("JSON 파일 보여줘")
        self.assertEqual(parsed["target_extension"], [".json"])

    def test_content_rows_are_deduplicated_and_text_is_not_returned(self):
        rows = self.engine.search_files_smart(["Cryptography"])[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 5)
        self.assertNotIn("Cryptography", repr(rows[0]))

    def test_index_only_content_is_searchable_without_files_row(self):
        path = str(self.root / "indexed-only.txt")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO file_text_index(file_path,extracted_text,extract_status,extractor_type) VALUES(?,?,?,?)",
                (path, "A unique searchable passage about orbital mechanics.", "success", "text"),
            )
            conn.commit()
        finally:
            conn.close()
        rows = self.engine.search_files_smart(["orbital mechanics"])[0]
        self.assertEqual([(row[1], row[2]) for row in rows], [("indexed-only.txt", path)])
        self.assertEqual(len(rows[0]), 5)
        self.assertNotIn("orbital mechanics", repr(rows[0]))

    def test_index_only_filename_and_type_search(self):
        path = str(self.root / "indexed-video.mp4")
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO file_text_index(file_path,extracted_text,extract_status,extractor_type) VALUES(?,?,?,?)",
                (path, "", "success", "metadata"),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.engine.probe_filename("indexed-video")[0][1], "indexed-video.mp4")
        rows = self.engine.search_files_smart([], [".mp4"])[0]
        self.assertIn("indexed-video.mp4", [row[1] for row in rows])


if __name__ == "__main__":
    unittest.main()

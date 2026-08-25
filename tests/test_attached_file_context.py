import os
import tempfile
import unittest
from pathlib import Path

from src.ui.views.search_view import SearchView
from src.ui.ai_workers import AIFileWorker
from src.utils.workers import FolderScanAndTagWorker


class _Core:
    class _Registry:
        duplicates_dir_name = "duplicates"

    registry = _Registry()

    def process_file_upload(self, file_path):
        return {
            "status": "SUCCESS",
            "file_info": {"original_name": os.path.basename(file_path)},
            "metadata": {"display_name": "Demo", "description": "Video summary", "tags": ["video"]},
        }


class AttachedFileContextTests(unittest.TestCase):
    def test_attachment_error_clears_context_and_explains_recovery(self):
        view = SearchView.__new__(SearchView)
        view._attached_file_path = "unsupported.bin"
        view._attached_analysis = {"stale": True}
        view._ai_services = object()
        messages = []
        view.hide_loading = lambda: None
        view.add_message = lambda message, **kwargs: messages.append((message, kwargs))

        view._on_attachment_error("지원하지 않는 파일 형식")

        self.assertIsNone(view._attached_file_path)
        self.assertIsNone(view._attached_analysis)
        self.assertIsNone(view._ai_services)
        self.assertIn("일반 검색으로 돌아갑니다", messages[0][0])

    def test_folder_analysis_summary_uses_user_facing_partial_failure_message(self):
        message = SearchView._format_file_analysis_summary({
            "total": 40,
            "success": 37,
            "failed": [{"reason": "unsupported"}] * 3,
        })

        self.assertIn("40개 파일 중 37개를 처리했고", message)
        self.assertIn("폴더 분석을 마쳤습니다", message)
        self.assertIn("3개는 처리하지 못했습니다", message)
        self.assertIn("일반 검색으로 돌아갑니다", message)

    def test_single_attachment_summary_explains_followup(self):
        self.assertEqual(
            SearchView._format_file_analysis_summary({"total": 1, "success": 1, "failed": []}),
            "파일 분석을 마쳤습니다. 이 파일에 대해 이어서 질문할 수 있습니다.",
        )

    def test_worker_preserves_successful_analysis_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "demo.mp4"
            video.write_bytes(b"fixture")
            worker = FolderScanAndTagWorker([str(video)], _Core())
            emitted = []
            worker.finished.connect(emitted.append)
            worker.run()
            self.assertEqual(emitted[0]["success"], 1)
            self.assertEqual(emitted[0]["results"][0]["file_path"], str(video))
            self.assertEqual(emitted[0]["results"][0]["result"]["metadata"]["description"], "Video summary")

    def test_analysis_result_is_rendered_with_summary_and_tags(self):
        text = SearchView._format_attached_analysis(_Core().process_file_upload("demo.mp4"))
        self.assertIn("Demo", text)
        self.assertIn("Video summary", text)
        self.assertIn("#video", text)

    def test_followup_uses_attachment_but_explicit_search_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "demo.mp4"
            video.write_bytes(b"fixture")
            self.assertTrue(SearchView._should_use_attached_context("이 영상에서 뭘 하고 있어?", str(video)))
            self.assertTrue(SearchView._should_use_attached_context("주인공은 누구야?", str(video)))
            self.assertFalse(SearchView._should_use_attached_context("PDF 파일 찾아줘", str(video)))

    def test_attachment_analysis_has_explicit_exit_commands(self):
        self.assertTrue(SearchView._is_attachment_exit_command("첨부 분석 종료"))
        self.assertTrue(SearchView._is_attachment_exit_command("분석 모드 끝"))
        self.assertTrue(SearchView._is_attachment_exit_command("새 대화"))
        self.assertFalse(SearchView._is_attachment_exit_command("이 영상 요약해줘"))

    def test_document_followup_uses_extracted_content(self):
        class Extractor:
            def extract(self, _path):
                return "The document says the launch date is Friday.", "SUCCESS"

        class Client:
            def __init__(self):
                self.prompt = ""

            def request_text(self, prompt, **_kwargs):
                self.prompt = prompt
                return "Friday"

        class Analyzer:
            _qwen_client = Client()

        class Processor:
            extractor = Extractor()
            analyzer = Analyzer()

        class Services:
            def get(self):
                return Processor(), object(), object()

        with tempfile.TemporaryDirectory() as tmp:
            document = Path(tmp) / "brief.txt"
            document.write_text("fixture", encoding="utf-8")
            worker = AIFileWorker(
                AIFileWorker.ASK_DOCUMENT, str(document), services=Services(),
                user_prompt="When is the launch?",
            )
            answers = []
            worker.succeeded.connect(answers.append)
            worker.run()
            self.assertEqual(answers, ["Friday"])
            self.assertIn("launch date is Friday", Processor.analyzer._qwen_client.prompt)
            self.assertIn("When is the launch?", Processor.analyzer._qwen_client.prompt)


if __name__ == "__main__":
    unittest.main()

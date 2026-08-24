import inspect
import unittest

from src.ui.views.saved_view import SavedView


class SavedBulkAITaggingTests(unittest.TestCase):
    def test_saved_view_exposes_explicit_bulk_tagging_button(self):
        source = inspect.getsource(SavedView.init_ui)
        self.assertIn("미태깅 전체 AI 태깅", source)
        self.assertIn("on_tag_all_untagged", source)

    def test_bulk_tagging_only_collects_empty_tags(self):
        source = inspect.getsource(SavedView._untagged_file_paths)
        self.assertIn("not (tag_item.text().strip()", source)
        self.assertIn("isfile", source)

    def test_bulk_tagging_requires_confirmation_and_uses_worker(self):
        source = inspect.getsource(SavedView.on_tag_all_untagged)
        self.assertIn("QMessageBox.question", source)
        self.assertIn("FolderScanAndTagWorker", source)
        self.assertIn("이미 태그된 파일은 변경하지 않", source)

    def test_bulk_tagging_reports_success_failure_and_remaining(self):
        source = inspect.getsource(SavedView._on_bulk_tagging_finished)
        self.assertIn("성공", source)
        self.assertIn("실패", source)
        self.assertIn("남은 미태깅", source)


if __name__ == "__main__":
    unittest.main()

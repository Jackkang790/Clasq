import ast
import unittest
from pathlib import Path


class OrganizeTaggedOnlyTests(unittest.TestCase):
    @staticmethod
    def _method_sources():
        source = Path("src/ui/views/organize_view.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        return {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }

    def test_organize_never_starts_ai_tagging_for_untagged_files(self):
        methods = self._method_sources()
        confirmed = methods["_on_organize_confirmed"]
        completed = methods["_on_plan_completed"]
        self.assertNotIn("_start_untagged_analysis", confirmed)
        self.assertNotIn("_start_untagged_analysis", completed)
        self.assertIn("get_files_for_organize", confirmed)
        self.assertIn("미태깅 파일", confirmed)
        self.assertIn("저장목록", confirmed)

    def test_adding_path_starts_incremental_tagging_workflow(self):
        methods = self._method_sources()
        added = methods["_on_path_added"]
        inventory = methods["_on_inventory_completed"]
        self.assertIn("_start_incremental_inventory", added)
        self.assertIn("QMessageBox.question", inventory)
        self.assertIn("_start_ai_tagging", inventory)
        self.assertNotIn("미태깅 전체 AI 태깅", added)


if __name__ == "__main__":
    unittest.main()

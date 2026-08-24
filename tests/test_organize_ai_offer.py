import ast
import unittest
from pathlib import Path


class OrganizeAIOfferTests(unittest.TestCase):
    def test_scan_completion_offers_all_untagged_files_for_ai(self):
        source = Path("src/ui/views/organize_view.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        completed = methods["_on_plan_completed"]
        offer = methods["_offer_untagged_analysis"]
        self.assertIn("_offer_untagged_analysis", completed)
        self.assertIn("list(untagged)", completed)
        self.assertIn("_start_untagged_analysis(paths)", offer)
        self.assertIn("QMessageBox.Yes", offer)
        self.assertIn("실제 파일은 아직 이동되지 않습니다", offer)


if __name__ == "__main__":
    unittest.main()

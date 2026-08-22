import os
import shutil
import sqlite3
import time
import unittest
from dataclasses import replace
from collections import Counter
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from src.recommendation.folder_repository import FolderProfileRepository
from src.recommendation.family import FolderFamilyResolver
from src.recommendation.models import (
    FileRecommendationContext, FolderProfile, RecommendationPlanItem, SourceFingerprint,
)
from src.recommendation.profile_builder import FolderProfileBuilder
from src.recommendation.qwen_reranker import QwenFolderReranker, QwenRerankDecision
from src.recommendation.retriever import FolderCandidateRetriever
from src.recommendation.service import FolderRecommendationService
from src.recommendation.scope_policy import RootInboxOrganizationPolicy
from src.ui.recommendation_worker import FolderRecommendationWorker
from src.utils.db_manager import FileRegistryManager


def profile(folder_id, path, *, extensions=(("pptx", 5.0),), categories=(), tags=(),
            filenames=(), texts=(), semantic=()):
    return FolderProfile(
        folder_id, str(path), Path(path).name, str(Path(path).parent), 1, 5, 5,
        extensions, categories, tags, filenames, texts, 1.0, False,
        semantic or tuple(part.casefold() for part in Path(path).parts if len(part) >= 2),
    )


def context(path, *, words=("lobodoc",), tags=(), category="", text=(), coverage=1.0):
    return FileRecommendationContext(
        str(path), Path(path).name, "pptx", str(Path(path).parent), words,
        tags, category, text, "", coverage, SourceFingerprint.capture(str(path)),
    )


class StubReranker:
    def __init__(self, selected=None, status="SELECTED"):
        self.selected = selected
        self.status = status
        self.calls = 0

    def rerank(self, _context, candidates, _profiles):
        self.calls += 1
        selected = self.selected or candidates[0].folder_id
        return QwenRerankDecision(selected, self.status, "stub reason", 0.8, 0.01)


class FakeQwenClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.config = SimpleNamespace(timeout=1, max_tokens=1000)

    def request_text(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response

    @staticmethod
    def parse_json_content(raw):
        import json
        return json.loads(raw)


class FolderRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests/fixtures/recommendation_runtime").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "incoming" / "LoboDoc_사업계획.pptx"
        self.file.parent.mkdir()
        self.file.write_text("fixture", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_folder_name_and_profile_signals_rank_obvious_folder_first(self):
        target = self.root / "프로젝트" / "LoboDoc"
        other = self.root / "교육" / "AI"
        target.mkdir(parents=True)
        other.mkdir(parents=True)
        profiles = {
            "F_TARGET": profile("F_TARGET", target, filenames=(("lobodoc", 5),)),
            "F_OTHER": profile("F_OTHER", other, filenames=(("교육", 5),)),
        }
        candidates = FolderCandidateRetriever().retrieve(context(self.file), profiles)
        self.assertEqual(candidates[0].folder_id, "F_TARGET")
        self.assertGreater(dict(candidates[0].score_breakdown)["folder_path"], 0)

    def test_tag_category_and_all_signals_enable_fast_path_without_qwen(self):
        target = self.root / "LoboDoc"
        other = self.root / "Other"
        target.mkdir(); other.mkdir()
        profiles = {
            "F_TARGET": profile(
                "F_TARGET", target, categories=(("사업계획", 5),),
                tags=(("수출", 5),), filenames=(("lobodoc", 5),), texts=(("규제", 5),)),
            "F_OTHER": profile("F_OTHER", other, extensions=(("pdf", 5),)),
        }
        reranker = StubReranker()
        ctx = context(self.file, tags=("수출",), category="사업계획", text=("규제",))
        result = FolderRecommendationService(reranker=reranker).recommend(ctx, profiles)
        self.assertEqual(result.selected_folder_id, "F_TARGET")
        self.assertFalse(result.qwen_used)
        self.assertEqual(reranker.calls, 0)

    def test_low_score_returns_none_without_qwen(self):
        folder = self.root / "Unrelated"
        folder.mkdir()
        reranker = StubReranker()
        result = FolderRecommendationService(reranker=reranker).recommend(
            context(self.file, words=("unknown",), coverage=0.0),
            {"F1": profile("F1", folder, extensions=(("pdf", 5),))},
        )
        self.assertEqual(result.status, "NONE")
        self.assertEqual(reranker.calls, 0)

    def test_close_candidates_call_qwen_and_valid_id_is_selected(self):
        one, two = self.root / "One", self.root / "Two"
        one.mkdir(); two.mkdir()
        profiles = {
            "F1": profile("F1", one, filenames=(("lobodoc", 5),), tags=(("수출", 3),)),
            "F2": profile("F2", two, filenames=(("lobodoc", 5),), tags=(("수출", 3),)),
        }
        reranker = StubReranker("F2")
        result = FolderRecommendationService(reranker=reranker).recommend(
            context(self.file, tags=("수출",)), profiles)
        self.assertEqual(reranker.calls, 1)
        self.assertTrue(result.qwen_used)
        self.assertEqual(result.selected_folder_id, "F2")

    def test_invalid_qwen_id_or_path_is_rejected(self):
        folder = self.root / "Candidate"
        folder.mkdir()
        candidate_profiles = {"F1": profile("F1", folder, filenames=(("lobodoc", 5),))}
        for selected in ("F_NOT_ALLOWED", str(folder)):
            client = FakeQwenClient(
                '{"selected_folder_id":"' + selected.replace("\\", "\\\\")
                + '","confidence":0.9,"reason":"x"}'
            )
            decision = QwenFolderReranker(client).rerank(
                context(self.file), FolderCandidateRetriever().retrieve(
                    context(self.file), candidate_profiles), candidate_profiles)
            self.assertEqual(decision.selected_folder_id, "NONE")
            self.assertEqual(decision.status, "INVALID")

    def test_qwen_timeout_and_invalid_json_are_safe(self):
        folder = self.root / "Candidate"
        folder.mkdir()
        profiles = {"F1": profile("F1", folder, filenames=(("lobodoc", 5),))}
        candidates = FolderCandidateRetriever().retrieve(context(self.file), profiles)
        for client in (FakeQwenClient(error=TimeoutError("timeout")), FakeQwenClient("not-json")):
            decision = QwenFolderReranker(client).rerank(context(self.file), candidates, profiles)
            self.assertEqual(decision.status, "ERROR")

    def test_profile_builder_excludes_empty_deleted_and_excluded_folders(self):
        db_path = str(self.root / "profiles.db")
        FileRegistryManager(db_path)
        valid = self.root / "valid"
        excluded = self.root / "node_modules"
        empty = self.root / "empty"
        valid.mkdir(); excluded.mkdir(); empty.mkdir()
        valid_file = valid / "a.txt"; valid_file.write_text("a")
        excluded_file = excluded / "b.txt"; excluded_file.write_text("b")
        scanned = [str(valid_file), str(excluded_file)]
        repository = FolderProfileRepository(str(self.root), db_path)
        profiles = FolderProfileBuilder(repository).build(repository.load_records(scanned))
        paths = {item.folder_path for item in profiles.values()}
        self.assertIn(str(valid), paths)
        self.assertNotIn(str(excluded), paths)
        self.assertNotIn(str(empty), paths)

    def test_deleted_folder_is_not_a_candidate(self):
        db_path = str(self.root / "deleted.db")
        FileRegistryManager(db_path)
        deleted = self.root / "deleted"
        deleted.mkdir()
        missing_file = deleted / "gone.txt"
        missing_file.write_text("gone")
        repository = FolderProfileRepository(str(self.root), db_path)
        records = repository.load_records([str(missing_file)])
        missing_file.unlink()
        deleted.rmdir()
        profiles = FolderProfileBuilder(repository).build(records)
        self.assertNotIn(str(deleted), {item.folder_path for item in profiles.values()})

    def test_metadata_free_file_uses_filename_fallback_or_none(self):
        target = self.root / "LoboDoc"
        target.mkdir()
        result = FolderRecommendationService(reranker=StubReranker()).recommend(
            context(self.file, coverage=0.0),
            {"F1": profile("F1", target, filenames=(("lobodoc", 4),))},
        )
        self.assertIn(result.status, {"RECOMMENDED", "REVIEW_REQUIRED", "NONE"})
        self.assertEqual(result.candidates[0].folder_id, "F1")

    def test_same_filename_is_disambiguated_by_folder_profile(self):
        target, other = self.root / "sales", self.root / "education"
        target.mkdir(); other.mkdir()
        profiles = {
            "F1": profile("F1", target, categories=(("proposal", 5),),
                          tags=(("client", 5),), filenames=(("report", 5),)),
            "F2": profile("F2", other, categories=(("lesson", 5),),
                          tags=(("school", 5),), filenames=(("report", 5),)),
        }
        ctx = context(self.file, words=("report",), tags=("client",),
                      category="proposal")
        candidates = FolderCandidateRetriever().retrieve(ctx, profiles)
        self.assertEqual(candidates[0].folder_id, "F1")

    def test_review_state_never_moves_file_and_stale_is_detected(self):
        folder = self.root / "Target"; folder.mkdir()
        result = FolderRecommendationService(reranker=StubReranker()).recommend(
            context(self.file, tags=("x",), category="x", text=("x",)),
            {"F1": profile("F1", folder, categories=(("x", 2),), tags=(("x", 2),),
                           filenames=(("lobodoc", 2),), texts=(("x", 2),))},
        )
        item = RecommendationPlanItem(str(self.file), str(self.file.parent), result)
        accepted = item.accept()
        self.assertEqual(accepted.review_status, "ACCEPTED")
        self.assertTrue(self.file.exists())
        skipped = item.skip()
        self.assertEqual(skipped.review_status, "SKIPPED")
        with self.assertRaises(ValueError):
            item.override("F_UNKNOWN")
        overridden = item.override("F1")
        self.assertEqual(overridden.review_status, "OVERRIDDEN")
        self.assertEqual(overridden.chosen_folder_path, str(folder))
        managed_override = item.override(
            "F_OTHER", str(self.root), allowed_folder_ids=("F_OTHER",)
        )
        self.assertEqual(managed_override.chosen_folder_path, str(self.root))
        self.file.write_text("changed content", encoding="utf-8")
        self.assertEqual(accepted.refresh_stale().review_status, "STALE")
        self.assertTrue(self.file.exists())

    def test_review_operations_never_move_or_create_directories(self):
        folder = self.root / "Target"; folder.mkdir()
        result = FolderRecommendationService(reranker=StubReranker()).recommend(
            context(self.file, tags=("x",), category="x", text=("x",)),
            {"F1": profile("F1", folder, categories=(("x", 2),), tags=(("x", 2),),
                           filenames=(("lobodoc", 2),), texts=(("x", 2),))},
        )
        item = RecommendationPlanItem(str(self.file), str(self.file.parent), result)
        with patch("shutil.move") as move, patch("os.makedirs") as makedirs:
            item.accept()
            item.override("F1")
            item.skip()
        move.assert_not_called()
        makedirs.assert_not_called()

    def test_recommendation_worker_honors_cooperative_cancellation(self):
        db_path = str(self.root / "worker.db")
        FileRegistryManager(db_path)
        worker = FolderRecommendationWorker(
            str(self.root), [str(self.file)], [str(self.file)], db_path=db_path,
        )
        payloads = []
        worker.cancelled.connect(payloads.append)
        worker.request_stop()
        worker.run()
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["items"], ())

    def test_root_inbox_scope_separates_sources_from_profile_files(self):
        policy = RootInboxOrganizationPolicy()
        direct = self.root / "inbox.pdf"
        nested_folder = self.root / "project"
        nested_folder.mkdir()
        nested = nested_folder / "existing.pdf"
        direct.write_text("direct"); nested.write_text("nested")
        self.assertTrue(policy.is_organizable_file(str(direct), str(self.root)))
        self.assertFalse(policy.is_organizable_file(str(nested), str(self.root)))
        self.assertTrue(policy.is_destination_folder(str(nested_folder), str(self.root)))

    def test_nested_package_tree_is_not_a_destination(self):
        policy = RootInboxOrganizationPolicy()
        direct_packages = self.root / "packages"
        nested_packages = self.root / "project" / "packages"
        generated = nested_packages / "Vendor" / "generated"
        direct_packages.mkdir()
        generated.mkdir(parents=True)
        self.assertTrue(policy.is_destination_folder(str(direct_packages), str(self.root)))
        self.assertFalse(policy.is_destination_folder(str(nested_packages), str(self.root)))
        self.assertFalse(policy.is_destination_folder(str(generated), str(self.root)))

    def test_source_parent_path_does_not_affect_retrieval(self):
        target, other = self.root / "LoboDoc", self.root / "Education"
        target.mkdir(); other.mkdir()
        profiles = {
            "F1": profile("F1", target, filenames=(("lobodoc", 4),)),
            "F2": profile("F2", other, filenames=(("education", 4),)),
        }
        base = context(self.file, words=("lobodoc",))
        leaked = FileRecommendationContext(
            base.file_path, base.file_name, base.extension,
            str(other / "secret_parent"), base.filename_keywords,
            base.tags, base.category, base.text_keywords, base.summary,
            base.metadata_coverage, base.source_fingerprint,
        )
        retriever = FolderCandidateRetriever()
        self.assertEqual(
            retriever.retrieve(base, profiles)[0].folder_id,
            retriever.retrieve(leaked, profiles)[0].folder_id,
        )

    def test_decorated_duplicate_folders_share_family_and_receive_penalty(self):
        parent = self.root / "projects"; parent.mkdir()
        original = parent / "LoboDoc"
        backup = parent / "LoboDoc backup"
        original.mkdir(); backup.mkdir()
        common = dict(
            filenames=(("lobodoc", 5),), tags=(("client", 5),),
            categories=(("proposal", 5),), texts=(("business", 5),),
        )
        profiles = {
            "F1": profile("F1", original, **common),
            "F2": profile("F2", backup, **common),
        }
        families = FolderFamilyResolver().resolve(profiles)
        self.assertEqual(families["F1"].family_id, families["F2"].family_id)
        self.assertEqual(families["F1"].structural_penalty, 0.0)
        self.assertGreater(families["F2"].structural_penalty, 0.0)

    def test_diversity_top_k_limits_one_family_to_two_candidates(self):
        parent = self.root / "families"; parent.mkdir()
        profiles = {}
        for index, name in enumerate(("LoboDoc", "LoboDoc backup", "LoboDoc copy"), 1):
            folder = parent / name; folder.mkdir()
            profiles[f"F{index}"] = profile(
                f"F{index}", folder, filenames=(("lobodoc", 5),),
                tags=(("client", 5),), categories=(("proposal", 5),),
            )
        other = parent / "Education"; other.mkdir()
        profiles["F4"] = profile("F4", other, filenames=(("course", 5),))
        candidates = FolderCandidateRetriever().retrieve(
            context(self.file, tags=("client",), category="proposal"), profiles, 5
        )
        counts = Counter(item.family_id for item in candidates)
        self.assertLessEqual(max(counts.values()), 2)
        self.assertTrue(all(item.depth >= 0 for item in candidates))


if __name__ == "__main__":
    unittest.main()

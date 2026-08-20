import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel

from src.ai.qwen_client import AIConnectionError
from src.ai.video_analyzer import FFmpegNotFoundError
from src.ui.views.search_view import SearchView


def pipeline_result(kind="image"):
    metadata = {
        "display_name": "테스트 파일",
        "description": "테스트 설명",
        "tags": ["테스트", kind],
        "ocr_text": "+ 환자 등록" if kind == "image" else "",
        "summary": "테스트 영상 요약" if kind == "video" else "",
        "applications": ["계산기"] if kind == "video" else [],
        "timeline": [{"time": "00:00", "scene": "시작 화면"}] if kind == "video" else [],
    }
    return {
        "response_type": "FILE_ORGANIZE",
        "payload": {"data": {
            "status": "SUCCESS", "error": None,
            "display_name": metadata["display_name"],
            "description": metadata["description"],
            "tags": metadata["tags"], "metadata": metadata,
        }},
    }


class FakeImageAnalyzer:
    def __init__(self):
        self.questions = []

    def ask_image(self, file_path, prompt):
        self.questions.append((file_path, prompt))
        return "이미지 답변"


class FakeVideoAnalyzer:
    def __init__(self):
        self.questions = []

    def ask_video(self, file_path, prompt):
        self.questions.append((file_path, prompt))
        return "영상 답변"


class FailingImageAnalyzer(FakeImageAnalyzer):
    def ask_image(self, file_path, prompt):
        raise AIConnectionError("offline")


class FailingVideoAnalyzer(FakeVideoAnalyzer):
    def ask_video(self, file_path, prompt):
        raise FFmpegNotFoundError("missing")


class FakeMainProcessor:
    def __init__(self, image, video, delay=0):
        self.analyzer = SimpleNamespace(image_analyzer=image, video_analyzer=video)
        self.paths = []
        self.delay = delay

    def process_file_upload(self, file_path):
        self.paths.append(file_path)
        if self.delay:
            time.sleep(self.delay)
        kind = "video" if file_path.lower().endswith((".mp4", ".mkv", ".avi")) else "image"
        return pipeline_result(kind)


class FailedPipelineMainProcessor(FakeMainProcessor):
    def process_file_upload(self, file_path):
        self.paths.append(file_path)
        return {
            "response_type": "FILE_ORGANIZE",
            "payload": {"data": {
                "status": "FAILED",
                "error": "AI 서버에 연결할 수 없습니다.",
                "description": "분석 실패",
                "metadata": {},
            }},
        }


class FakeSearchEngine:
    def __init__(self):
        self.queries = []

    def process_query_result(self, parsed):
        self.queries.append(parsed)
        return {"action": "UPDATE_TABLE", "message": "기존 검색 성공", "data": []}


class SearchViewAITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self, delay=0):
        image = FakeImageAnalyzer()
        video = FakeVideoAnalyzer()
        main = FakeMainProcessor(image, video, delay=delay)
        search = FakeSearchEngine()
        view = SearchView(
            search_engine=search,
            main_processor=main,
            image_analyzer=image,
            video_analyzer=video,
        )
        return view, main, image, video, search

    def wait_for_idle(self, view, timeout=2000):
        deadline = time.monotonic() + timeout / 1000
        while view._ai_busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertFalse(view._ai_busy)

    @staticmethod
    def messages(view):
        return [label.text() for label in view.chat_container.findChildren(QLabel)]

    def test_search_without_attachment_is_preserved(self):
        view, _main, _image, _video, search = self.make_view()
        view.process_query("회의록 찾아줘")
        self.assertEqual(len(search.queries), 1)
        self.assertIn("기존 검색 성공", self.messages(view))

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_image_attachment_analysis_question_and_replacement(self, _isfile):
        view, main, image, _video, _search = self.make_view()
        view.on_file_attached("first.png")
        self.wait_for_idle(view)
        self.assertTrue(main.paths[0].endswith("first.png"))
        self.assertTrue(any("이미지 분석 완료" in text for text in self.messages(view)))

        view.process_query("가장 많이 사용된 색상은 뭐야?")
        self.wait_for_idle(view)
        self.assertEqual(image.questions[-1][1], "가장 많이 사용된 색상은 뭐야?")
        self.assertIn("이미지 답변", self.messages(view))

        view.on_file_attached("second.png")
        self.wait_for_idle(view)
        self.assertTrue(view.current_file_path.endswith("second.png"))

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_video_attachment_and_question(self, _isfile):
        view, main, _image, video, _search = self.make_view()
        view.on_file_attached("sample.mp4")
        self.wait_for_idle(view)
        self.assertTrue(main.paths[0].endswith("sample.mp4"))
        self.assertTrue(any("영상 분석 완료" in text for text in self.messages(view)))
        view.process_query("이 영상을 5줄로 요약해줘")
        self.wait_for_idle(view)
        self.assertEqual(video.questions[-1][1], "이 영상을 5줄로 요약해줘")
        self.assertIn("영상 답변", self.messages(view))

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_worker_keeps_event_loop_responsive_and_restores_inputs(self, _isfile):
        view, _main, _image, _video, _search = self.make_view(delay=0.15)
        timer_fired = []
        QTimer.singleShot(20, lambda: timer_fired.append(True))
        view.on_file_attached("slow.png")
        self.assertFalse(view.chat_input_widget.send_btn.isEnabled())
        self.wait_for_idle(view)
        self.assertTrue(timer_fired)
        self.assertTrue(view.chat_input_widget.send_btn.isEnabled())
        self.assertTrue(view.chat_input_widget.plus_btn.isEnabled())
        self.assertTrue(view.chat_input_widget.input_field.isEnabled())

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_ai_server_error_uses_error_bubble_and_restores_inputs(self, _isfile):
        image = FailingImageAnalyzer()
        video = FakeVideoAnalyzer()
        main = FakeMainProcessor(image, video)
        view = SearchView(
            search_engine=FakeSearchEngine(), main_processor=main,
            image_analyzer=image, video_analyzer=video,
        )
        view.on_file_attached("offline.png")
        self.wait_for_idle(view)
        view.process_query("설명해줘")
        self.wait_for_idle(view)
        self.assertTrue(any("AI 서버에 연결할 수 없습니다" in text for text in self.messages(view)))
        self.assertTrue(view.chat_input_widget.send_btn.isEnabled())

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_failed_pipeline_result_uses_error_bubble(self, _isfile):
        image = FakeImageAnalyzer()
        video = FakeVideoAnalyzer()
        main = FailedPipelineMainProcessor(image, video)
        view = SearchView(
            search_engine=FakeSearchEngine(), main_processor=main,
            image_analyzer=image, video_analyzer=video,
        )
        view.on_file_attached("offline.png")
        self.wait_for_idle(view)
        self.assertTrue(any("AI 서버에 연결할 수 없습니다" in text for text in self.messages(view)))
        self.assertTrue(view.chat_input_widget.send_btn.isEnabled())

    @patch("src.ui.views.search_view.os.path.isfile", return_value=True)
    def test_ffmpeg_error_uses_error_bubble(self, _isfile):
        image = FakeImageAnalyzer()
        video = FailingVideoAnalyzer()
        main = FakeMainProcessor(image, video)
        view = SearchView(
            search_engine=FakeSearchEngine(), main_processor=main,
            image_analyzer=image, video_analyzer=video,
        )
        view.on_file_attached("missing-ffmpeg.mp4")
        self.wait_for_idle(view)
        view.process_query("요약해줘")
        self.wait_for_idle(view)
        self.assertTrue(any("FFmpeg를 찾지 못했습니다" in text for text in self.messages(view)))


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.ai.image_analyzer import OCR_PROMPT, ImageAnalyzer
from src.ai.qwen_client import AIJSONError, QwenClient
from src.ai.video_analyzer import FFmpegExecutionError, FFmpegNotFoundError, VideoAnalyzer


class FakeClient:
    def __init__(self):
        self.config = SimpleNamespace(
            timeout=300,
            video_ai_timeout=900,
            max_tokens=1000,
            video_scene_threshold=0.30,
            video_max_gap_seconds=10,
            video_image_width=640,
            video_max_frames=24,
            ffmpeg_timeout=600,
            ffmpeg_path=None,
        )
        self.content_calls = []
        self.json_calls = []

    text_part = staticmethod(QwenClient.text_part)
    image_part = staticmethod(QwenClient.image_part)

    def request_content(self, content, **kwargs):
        self.content_calls.append((content, kwargs))
        return "환자 등록"

    def request_json(self, content, **kwargs):
        self.json_calls.append((content, kwargs))
        return {
            "display_name": "환자 등록 화면",
            "tags": ["의료", "UI"],
            "description": "환자 등록용 화면입니다.",
            "summary": "대표 프레임 영상",
            "timeline": [],
            "applications": [],
        }


class QwenJSONTests(unittest.TestCase):
    def test_json_variants(self):
        expected = {"ok": True}
        self.assertEqual(QwenClient.parse_json_content('{"ok": true}'), expected)
        self.assertEqual(QwenClient.parse_json_content('```json\n{"ok": true}\n```'), expected)
        self.assertEqual(QwenClient.parse_json_content('설명 앞 {"ok": true} 설명 뒤'), expected)
        with self.assertRaises(AIJSONError):
            QwenClient.parse_json_content("JSON 없음")


class ImageAnalyzerTests(unittest.TestCase):
    def test_missing_image_returns_fallback(self):
        result = ImageAnalyzer(FakeClient()).analyze_image("does-not-exist.png")
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["error"])

    def test_two_passes_use_upscaled_ocr_and_original_metadata(self):
        client = FakeClient()
        analyzer = ImageAnalyzer(client)
        original_url = "data:image/png;base64,b3JpZ2luYWwtZnVsbC1yZXNvbHV0aW9u"
        ocr_url = "data:image/png;base64,dXBzY2FsZWQtZm9yLW9jcg=="
        with patch.object(analyzer, "image_to_data_url", return_value=original_url), patch.object(
            analyzer, "prepare_ocr_data_url", return_value=ocr_url
        ):
            result = analyzer.analyze_image("small-ui.png")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["metadata"]["ocr_text"], "환자 등록")
        self.assertEqual(len(client.content_calls), 1)
        self.assertEqual(len(client.json_calls), 1)
        first_url = client.content_calls[0][0][0]["image_url"]["url"]
        second_url = client.json_calls[0][0][0]["image_url"]["url"]
        self.assertEqual(first_url, ocr_url)
        self.assertEqual(second_url, original_url)
        self.assertNotIn("환자 등록", OCR_PROMPT)


class VideoAnalyzerTests(unittest.TestCase):
    def test_even_frame_reduction_keeps_ends(self):
        frames = [Path(f"{i}.jpg") for i in range(30)]
        timestamps = list(range(30))
        reduced, reduced_times = VideoAnalyzer.reduce_frames(frames, timestamps, 24)
        self.assertEqual(len(reduced), 24)
        self.assertEqual(reduced_times[0], 0)
        self.assertEqual(reduced_times[-1], 29)

    def test_all_frames_are_sent_in_one_request(self):
        client = FakeClient()
        analyzer = VideoAnalyzer(client)
        frames = [Path(f"frame_{index}.jpg") for index in range(3)]
        with patch.object(
            analyzer,
            "image_to_data_url",
            side_effect=lambda path: f"data:image/jpeg;base64,{path.stem}",
        ):
            analyzer._request_analysis(frames, [0.0, 10.0, 20.0], None, None)
        self.assertEqual(len(client.json_calls), 1)
        image_parts = [part for part in client.json_calls[0][0] if part["type"] == "image_url"]
        self.assertEqual(len(image_parts), 3)
        self.assertEqual(client.json_calls[0][1]["timeout"], 900)

    @patch("src.ai.video_analyzer.shutil.which", return_value=None)
    @patch("src.ai.video_analyzer.Path.is_file", return_value=False)
    def test_missing_ffmpeg_is_distinct_error(self, _is_file, _which):
        with self.assertRaises(FFmpegNotFoundError):
            VideoAnalyzer(FakeClient()).find_ffmpeg()

    @patch("src.ai.video_analyzer.subprocess.run")
    def test_ffmpeg_timeout_is_distinct_error(self, run):
        import subprocess

        run.side_effect = subprocess.TimeoutExpired("ffmpeg", 600)
        analyzer = VideoAnalyzer(FakeClient())
        with patch.object(analyzer, "find_ffmpeg", return_value="ffmpeg"):
            with self.assertRaises(FFmpegExecutionError):
                analyzer.extract_representative_frames(__file__, Path("."))


if __name__ == "__main__":
    unittest.main()

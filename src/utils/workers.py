import os
from PySide6.QtCore import QThread, Signal
from ollama_manager import OllamaManager
from .core import ClasqCore
from .query_parser import SearchQueryParser
from .config import SUPPORTED_EXTENSIONS


class FolderScanAndTagWorker(QThread):
    progress = Signal(str)
    fileProgress = Signal(int, int, str)  # 처리 순번, 전체 개수, 현재 파일명
    taggingFinished = Signal()
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, folder_paths: list, core: ClasqCore):
        super().__init__()
        self.folder_paths = folder_paths
        self.core = core

    def run(self):
        try:
            files_to_process = []

            for target_path in self.folder_paths:
                clean_target = os.path.abspath(os.path.normpath(target_path))
                if os.path.isfile(clean_target):
                    if clean_target.lower().endswith(SUPPORTED_EXTENSIONS):
                        files_to_process.append(clean_target)
                    continue
                if not os.path.isdir(clean_target):
                    continue
                for root, dirs, files in os.walk(clean_target):
                    dirs[:] = [name for name in dirs if name != self.core.registry.duplicates_dir_name]
                    for file in files:
                        if file.lower().endswith(SUPPORTED_EXTENSIONS):
                            full_path = os.path.join(root, file)
                            files_to_process.append(os.path.abspath(os.path.normpath(full_path)))

            files_to_process = list(dict.fromkeys(files_to_process))
            if not files_to_process:
                self.error.emit("스캔할 지원 파일이 지정된 경로에 없습니다.")
                return

            total_count = len(files_to_process)

            succeeded, failures = 0, []
            for idx, file_path in enumerate(files_to_process, start=1):
                file_name = os.path.basename(file_path)
                self.progress.emit(f"AI 분석 중 ({idx}/{total_count}): {file_name}")
                self.fileProgress.emit(idx, total_count, file_name)
                try:
                    result = self.core.process_file_upload(file_path)
                    if result.get("status") == "SUCCESS":
                        succeeded += 1
                    else:
                        failures.append({"file_path": file_path, "reason": result.get("error") or result.get("message", "분석 실패")})
                except Exception as exc:
                    failures.append({"file_path": file_path, "reason": str(exc)})

            summary = {"total": total_count, "success": succeeded, "failed": failures}
            self.taggingFinished.emit()  # 기존 UI 연결 호환성
            self.finished.emit(summary)

        except Exception as e:
            self.error.emit(f"스캔 및 태깅 작업 중 오류 발생: {str(e)}")


class OllamaInitWorker(QThread):
    """Ollama 설치·서버·모델 준비 과정을 GUI 스레드 밖에서 단계별로 수행합니다."""

    TOTAL_STEPS = 4

    progress = Signal(int, int, str)  # 완료 단계, 전체 단계, 현재 상태 문구
    completed = Signal(bool, str)

    def run(self):
        try:
            self.progress.emit(0, self.TOTAL_STEPS, "Ollama 설치 상태를 확인하고 있습니다...")
            if not OllamaManager.is_installed() and not OllamaManager.install():
                self.completed.emit(False, "Ollama를 설치하지 못했습니다.")
                return

            self.progress.emit(1, self.TOTAL_STEPS, "Ollama 서버를 시작하고 있습니다...")
            if not OllamaManager.start_server():
                self.completed.emit(False, "Ollama 서버를 시작하지 못했습니다.")
                return

            model_name = OllamaManager.MODEL_NAME
            self.progress.emit(2, self.TOTAL_STEPS, f"{model_name} 모델을 확인하고 있습니다...")
            if not OllamaManager.model_exists() and not OllamaManager.download_model():
                self.completed.emit(False, f"{model_name} 모델을 내려받지 못했습니다.")
                return

            self.progress.emit(3, self.TOTAL_STEPS, f"{model_name} 모델을 불러오고 있습니다...")
            if not OllamaManager.test_model():
                self.completed.emit(False, "AI 모델 응답 확인에 실패했습니다.")
                return

            self.progress.emit(self.TOTAL_STEPS, self.TOTAL_STEPS, "AI 모델 준비를 마쳤습니다.")
            self.completed.emit(True, "")
        except Exception as exc:
            self.completed.emit(False, f"Ollama 초기화 중 오류가 발생했습니다: {exc}")


class QueryParseWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_text: str, query_parser: SearchQueryParser):
        super().__init__()
        self.user_text = user_text
        self.query_parser = query_parser

    def run(self):
        try:
            result = self.query_parser.parse_user_query(self.user_text)
            self.finished.emit(result)   # ← self.taggingFinished.emit() 에서 수정 (result도 복구)
        except Exception as e:
            self.error.emit(f"자연어 파싱 처리 중 오류: {str(e)}")

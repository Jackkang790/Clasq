import os
from PySide6.QtCore import QThread, Signal
from .core import ClasqCore
from .config import SUPPORTED_EXTENSIONS


class FolderScanAndTagWorker(QThread):
    progress = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, folder_paths: list, core: ClasqCore):
        super().__init__()
        self.folder_paths = folder_paths
        self.core = core

    # 비동기 제어랑 스레드 반복 처리 하는 함수
    # 독립된 백그라운드 스레드인 QThread 안에서 동작함
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
                for root, _, files in os.walk(clean_target):
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
                # 파일 리스트를 하나씩 꺼내서
                # core.process_file_upload로 넘겨줌
                file_name = os.path.basename(file_path)
                self.progress.emit(
                    # 파일 처리 진행 상황 프론트 엔드 연결 필요
                    f"AI 분석 중 ({idx}/{total_count}): {file_name}")
                try:
                    result = self.core.process_file_upload(file_path)
                    if result.get("status") == "SUCCESS":
                        succeeded += 1
                    else:
                        failures.append({"file_path": file_path, "reason": result.get("error") or result.get("message", "분석 실패")})
                except Exception as exc:
                    failures.append({"file_path": file_path, "reason": str(exc)})

            self.finished.emit({"total": total_count, "success": succeeded, "failed": failures})

        except Exception as e:
            self.error.emit(f"스캔 및 태깅 작업 중 오류 발생: {str(e)}")

import os
from PySide6.QtCore import QThread, Signal
# 제가 임의로 프론트엔드팀 코드랑 합쳐서
# 테스트 한거라 경로는 쓰실 때 고치시면 됩니다
from main_processor import MainProcessor
from query_parser import SearchQueryParser


class FolderScanAndTagWorker(QThread):
    progress = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, folder_paths: list, main_processor: MainProcessor):
        super().__init__()
        self.folder_paths = folder_paths
        self.main_processor = main_processor

    # 비동기 제어랑 스레드 반복 처리 하는 함수
    # 독립된 백그라운드 스레드인 QThread 안에서 동작함
    def run(self):
        try:
            valid_extensions = (
                '.txt', '.pdf', '.docx', '.xlsx', '.pptx', '.hwp', '.hwpx',
                '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif',
                '.mp3', '.mp4', '.wav', '.m4a', '.mkv', '.avi'
            )
            files_to_process = []

            for folder_path in self.folder_paths:
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        if file.lower().endswith(valid_extensions):
                            full_path = os.path.join(root, file)
                            clean_path = os.path.abspath(os.path.normpath(
                                full_path.replace('￥', '/').replace('\\', '/')))
                            files_to_process.append(clean_path)

            if not files_to_process:
                self.error.emit("스캔할 지원 파일이 지정된 경로에 없습니다.")
                return

            total_count = len(files_to_process)

            for idx, file_path in enumerate(files_to_process, start=1):
                # 파일 리스트를 하나씩 꺼내서
                # main_processor.process_file_upload로 넘겨줌
                file_name = os.path.basename(file_path)
                self.progress.emit(
                    # 파일 처리 진행 상황 프론트 엔드 연결 필요
                    f"AI 분석 중 ({idx}/{total_count}): {file_name}")
                self.main_processor.process_file_upload(file_path)

            self.finished.emit()

        except Exception as e:
            self.error.emit(f"스캔 및 태깅 작업 중 오류 발생: {str(e)}")


class QueryParseWorker(QThread):
    # 사용자가 자연어 입력 시 UI 멈춤 방지
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, user_text: str, query_parser: SearchQueryParser):
        super().__init__()
        self.user_text = user_text
        self.query_parser = query_parser

    def run(self):
        try:
            result = self.query_parser.parse_user_query(self.user_text)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"자연어 파싱 처리 중 오류: {str(e)}")

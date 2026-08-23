"""작업 진행 상황을 같은 디자인으로 보여주는 공용 Progress Dialog."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProgressDialog


class TaskProgressDialog(QProgressDialog):
    """제목 / 상태 문구 / Progress Bar / 진행 상황을 함께 표시하는 모달 다이얼로그."""

    def __init__(self, title, status_text, parent=None, unit="파일"):
        super().__init__(status_text, None, 0, 0, parent)
        self._status_text = status_text
        self._unit = unit
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setCancelButton(None)
        self.setMinimumWidth(380)
        self.setLabelText(status_text)

    def update_progress(self, current, total, detail="", status=None):
        """실제 처리 개수에 맞춰 Progress Bar와 안내 문구를 갱신합니다."""
        if status:
            self._status_text = status

        lines = [self._status_text]
        if total > 0:
            self.setRange(0, total)
            self.setValue(current)
            lines.append(f"{current} / {total} {self._unit} 처리 중")
        else:
            self.setRange(0, 0)
        if detail:
            lines.append(f"현재 {self._unit}: {detail}")
        self.setLabelText("\n\n".join(lines))

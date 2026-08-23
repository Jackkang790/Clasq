"""DB 변경 뒤 화면 모델을 일관되게 갱신하는 공용 신호입니다."""
from PySide6.QtCore import QObject, Signal


class RefreshManager(QObject):
    database_changed = Signal()

    def refresh(self) -> None:
        """DB 쓰기 작업이 완료된 뒤 UI 스레드에서 호출합니다."""
        self.database_changed.emit()

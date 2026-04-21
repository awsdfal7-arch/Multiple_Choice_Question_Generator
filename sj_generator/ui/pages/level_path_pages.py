from PyQt6.QtWidgets import QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWizardPage

from sj_generator.ui.constants import PAGE_DEDUPE_OPTION
from sj_generator.ui.state import WizardState


class AiLevelPathPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("补充层级归属")

        self._level_path_edit = QLineEdit()
        self._level_path_edit.setPlaceholderText("例如：3.6.3")

        hint = QLabel("请为本次文档解析得到的题目补充所属层级，后续可用于归类与入库。")
        hint.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("level_path："))
        layout.addWidget(self._level_path_edit)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._level_path_edit.setText(self._state.ai_import_level_path)

    def validatePage(self) -> bool:
        text = self._level_path_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "未填写层级", "请填写导入题目的 level_path 归属。")
            return False
        if text != self._state.ai_import_level_path:
            self._state.reset_db_import()
        self._state.ai_import_level_path = text
        return True

    def nextId(self) -> int:
        return PAGE_DEDUPE_OPTION

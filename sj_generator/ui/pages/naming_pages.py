import re
from pathlib import Path

from PyQt6.QtWidgets import QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWizardPage

from sj_generator.ui.constants import PAGE_EXPORT
from sj_generator.ui.state import WizardState


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\\\|?*]+')


def _sanitize_filename(name: str) -> str:
    s = (name or "").strip()
    s = _INVALID_FILENAME_CHARS.sub("_", s)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = s.strip(" .")
    return s


def _unique_child_dir(parent: Path, base_name: str) -> Path:
    candidate = parent / base_name
    if not candidate.exists():
        return candidate
    i = 2
    while True:
        c = parent / f"{base_name}_{i}"
        if not c.exists():
            return c
        i += 1


class NamingPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("输入名称")

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：第一章_单选题库")

        hint = QLabel("将用于重命名题库文件夹与 xlsx 文件名。")
        hint.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("题库名称："))
        layout.addWidget(self._name_edit)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        if self._state.project_dir is not None and self._state.project_name_is_placeholder:
            self._name_edit.setText("")

    def nextId(self) -> int:
        return PAGE_EXPORT

    def validatePage(self) -> bool:
        project_dir = self._state.project_dir
        repo_path = self._state.repo_path
        if project_dir is None or repo_path is None:
            QMessageBox.warning(self, "未创建题库", "请先创建题库。")
            return False

        name = self._name_edit.text().strip()
        safe = _sanitize_filename(name)
        if not safe:
            QMessageBox.warning(self, "名称为空", "请输入题库名称。")
            return False

        parent = project_dir.parent
        target_dir = _unique_child_dir(parent, safe)
        target_name = target_dir.name
        target_repo = target_dir / f"{target_name}.xlsx"

        try:
            if project_dir != target_dir:
                project_dir.rename(target_dir)
            cur_repo = target_dir / repo_path.name
            if cur_repo != target_repo and cur_repo.exists():
                cur_repo.rename(target_repo)
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", str(e))
            return False

        self._state.project_dir = target_dir
        self._state.repo_path = target_repo
        self._state.project_name_is_placeholder = False
        return True


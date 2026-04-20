import re
from datetime import datetime
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWizardPage,
)

from sj_generator.models import Question
from sj_generator.ui.state import WizardState, normalize_default_repo_parent_dir_text
from sj_generator.ui.constants import PAGE_MANUAL, PAGE_AI_SELECT, PAGE_REVIEW

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\\\|?*]+')

def _sanitize_filename(name: str) -> str:
    s = name.strip()
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


def ensure_default_project_target(state: WizardState) -> None:
    if state.project_dir is not None and state.repo_path is not None:
        return
    parent_dir = Path(normalize_default_repo_parent_dir_text(state.default_repo_parent_dir_text))
    safe = f"未命名_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = _unique_child_dir(parent_dir, safe)
    repo_path = project_dir / f"{project_dir.name}.xlsx"
    state.project_dir = project_dir
    state.repo_path = repo_path
    state.project_name_is_placeholder = True


class RepoPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("建题库")

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：第一章_单选题库")

        self._data_radio = QRadioButton("默认保存到 桌面/思政题库文件夹")
        self._custom_radio = QRadioButton("自定义位置（选择父目录）")
        self._data_radio.setChecked(True)

        self._parent_dir_edit = QLineEdit()
        self._parent_dir_edit.setPlaceholderText("选择父目录（自定义模式）")

        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("题库名称："))
        layout.addWidget(self._name_edit)
        layout.addWidget(self._data_radio)
        layout.addWidget(self._custom_radio)

        path_row = QHBoxLayout()
        path_row.addWidget(self._parent_dir_edit, 1)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        self.setLayout(layout)
        self._sync_mode_ui()
        self._data_radio.toggled.connect(self._sync_mode_ui)
        self._custom_radio.toggled.connect(self._sync_mode_ui)

    def _browse(self) -> None:
        self._custom_radio.setChecked(True)
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择父目录",
            normalize_default_repo_parent_dir_text(self._state.default_repo_parent_dir_text),
        )
        if folder:
            self._parent_dir_edit.setText(folder)

    def _sync_mode_ui(self) -> None:
        self._parent_dir_edit.setEnabled(self._custom_radio.isChecked())

    def validatePage(self) -> bool:
        name = self._name_edit.text().strip()
        safe = _sanitize_filename(name) if name else ""
        is_placeholder = False
        if not safe:
            is_placeholder = True
            safe = f"未命名_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        if self._data_radio.isChecked():
            parent_dir = Path(normalize_default_repo_parent_dir_text(self._state.default_repo_parent_dir_text))
        else:
            raw_parent = self._parent_dir_edit.text().strip()
            if not raw_parent:
                QMessageBox.warning(self, "路径为空", "请选择父目录。")
                return False
            parent_dir = Path(raw_parent)

        project_dir = _unique_child_dir(parent_dir, safe)
        safe = project_dir.name

        repo_path = project_dir / f"{safe}.xlsx"

        self._state.project_dir = project_dir
        self._state.repo_path = repo_path
        self._state.project_name_is_placeholder = is_placeholder
        return True


class ModePage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("选择录题模式")

        self._ai_radio = QRadioButton("AI 导入（资料 -> 题库）")
        self._manual_radio = QRadioButton("手动录入（逐题输入/粘贴 -> 题库）")
        self._ai_radio.setChecked(True)

        layout = QVBoxLayout()
        layout.addWidget(self._ai_radio)
        layout.addWidget(self._manual_radio)
        layout.addStretch(1)
        self.setLayout(layout)

    def nextId(self) -> int:
        ensure_default_project_target(self._state)
        self._state.input_mode = "manual" if self._manual_radio.isChecked() else "ai"
        return PAGE_MANUAL if self._state.input_mode == "manual" else PAGE_AI_SELECT


class ManualEntryPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("手动录入")

        self._number_edit = QLineEdit()
        self._number_edit.setPlaceholderText("编号（可留空，导出时会自动补序号）")

        self._stem_edit = QTextEdit()
        self._stem_edit.setPlaceholderText("题目（题干）")

        self._options_edit = QTextEdit()
        self._options_edit.setPlaceholderText("选项（支持 A. / A、 或 ①②③④ 等格式；原样写入题库）")

        self._answer_edit = QLineEdit()
        self._answer_edit.setPlaceholderText("答案（单选如 A；多选可能是 ACD 或 ①②③，按题库约定原样填写）")

        self._analysis_edit = QTextEdit()
        self._analysis_edit.setPlaceholderText("解析（可留空）")

        self._append_btn = QPushButton("追加到当前草稿")
        self._append_btn.clicked.connect(self._append)

        grid = QGridLayout()
        grid.addWidget(QLabel("编号："), 0, 0)
        grid.addWidget(self._number_edit, 0, 1)
        grid.addWidget(QLabel("题目："), 1, 0, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._stem_edit, 1, 1)
        grid.addWidget(QLabel("选项："), 2, 0, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._options_edit, 2, 1)
        grid.addWidget(QLabel("答案："), 3, 0)
        grid.addWidget(self._answer_edit, 3, 1)
        grid.addWidget(QLabel("解析："), 4, 0, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._analysis_edit, 4, 1)

        layout = QVBoxLayout()
        layout.addLayout(grid)
        layout.addWidget(self._append_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self.setLayout(layout)

    def _append(self) -> None:
        ensure_default_project_target(self._state)

        stem = self._stem_edit.toPlainText().strip()
        options = self._options_edit.toPlainText().strip()
        answer = self._answer_edit.text().strip()
        analysis = self._analysis_edit.toPlainText().strip()
        if not any([stem, options, answer, analysis]):
            QMessageBox.warning(self, "内容为空", "请至少填写一项内容。")
            return

        q = Question(
            number=self._number_edit.text().strip(),
            stem=stem,
            options=options,
            answer=answer,
            analysis=analysis,
        )
        self._state.draft_questions.append(q)

        self._number_edit.clear()
        self._stem_edit.clear()
        self._options_edit.clear()
        self._answer_edit.clear()
        self._analysis_edit.clear()
        QMessageBox.information(self, "已加入草稿", "已追加到当前题库草稿。")

    def nextId(self) -> int:
        return PAGE_REVIEW

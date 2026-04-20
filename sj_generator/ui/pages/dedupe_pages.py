import re
from pathlib import Path
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWizardPage,
)

from sj_generator.io.dedupe import (
    DedupeHit,
    dedupe_between_questions_and_repos,
    list_xlsx_in_folder,
)
from sj_generator.models import Question
from sj_generator.ui.state import WizardState
from sj_generator.ui.constants import (
    PAGE_AI_ANALYSIS_OPTION,
    PAGE_DEDUPE_RESULT,
    PAGE_DEDUPE_SETUP,
)


class DedupeOptionPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("查重选项")

        self._yes_radio = QRadioButton("运行跨库查重")
        self._no_radio = QRadioButton("跳过跨库查重")
        self._yes_radio.setChecked(True)

        hint = QLabel("选择“运行跨库查重”后，将进入跨库查重页面，可设置文件夹与相似度阈值并运行。")
        hint.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self._yes_radio)
        layout.addWidget(self._no_radio)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        if self._state.dedupe_enabled:
            self._yes_radio.setChecked(True)
        else:
            self._no_radio.setChecked(True)

    def validatePage(self) -> bool:
        self._state.dedupe_enabled = self._yes_radio.isChecked()
        if not self._state.dedupe_enabled:
            self._state.dedupe_hits = None
        return True

    def nextId(self) -> int:
        return PAGE_DEDUPE_SETUP if self._state.dedupe_enabled else PAGE_AI_ANALYSIS_OPTION


class DedupeSetupPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("跨库查重（设置）")

        folder_btn = QPushButton("选择文件夹…")
        folder_btn.clicked.connect(self._pick_folder)

        self._targets_edit = QLineEdit()
        self._targets_edit.setReadOnly(True)
        self._targets_edit.setPlaceholderText("待查重文件来源（默认 data 文件夹）")
        base_dir = Path(__file__).resolve().parents[3]
        self._targets_edit.setText(str(base_dir / "data"))

        self._threshold_edit = QLineEdit()
        self._threshold_edit.setPlaceholderText("相似度阈值（0-1）")
        self._threshold_edit.setText("0.85")

        hint = QLabel("点击“下一步”后开始查重，并在下一页显示结果。")
        hint.setWordWrap(True)

        target_row = QHBoxLayout()
        target_row.addWidget(self._targets_edit, 1)
        target_row.addWidget(folder_btn)

        layout = QVBoxLayout()
        layout.addLayout(target_row)
        layout.addWidget(self._threshold_edit)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        base_dir = Path(__file__).resolve().parents[3]
        if self._state.dedupe_folder is not None:
            self._targets_edit.setText(str(self._state.dedupe_folder))
        else:
            self._targets_edit.setText(str(base_dir / "data"))
        self._threshold_edit.setText(str(self._state.dedupe_threshold))
        self._state.dedupe_hits = None

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if folder:
            self._targets_edit.setText(folder)

    def validatePage(self) -> bool:
        raw = self._targets_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "未选择查重来源", "请选择存在的文件夹。")
            return False
        p = Path(raw)
        if not p.exists() or not p.is_dir():
            QMessageBox.warning(self, "路径不正确", "请选择存在的文件夹。")
            return False

        try:
            threshold = float(self._threshold_edit.text().strip())
        except Exception:
            QMessageBox.warning(self, "阈值不合法", "相似度阈值需要是 0-1 的数字。")
            return False
        if threshold <= 0 or threshold > 1:
            QMessageBox.warning(self, "阈值不合法", "相似度阈值范围为 (0, 1]。")
            return False

        self._state.dedupe_folder = p
        self._state.dedupe_threshold = threshold
        self._state.dedupe_hits = None
        return True

    def nextId(self) -> int:
        return PAGE_DEDUPE_RESULT


class DedupeResultPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("跨库查重（结果）")

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["左文件", "左序号", "右文件", "右序号", "相似度"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setRowCount(0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.cellDoubleClicked.connect(self._open_detail)

        hint = QLabel("双击行可查看两侧题目的题干、选项与答案。")
        hint.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(self._status_label)
        layout.addWidget(self._table, 1)
        layout.addWidget(hint)
        self.setLayout(layout)

        self._thread: QThread | None = None
        self._worker: _DedupeWorker | None = None
        self._running = False
        self._done = False
        self._hits: list[DedupeHit] = []
        self._questions_cache: dict[Path, list[Question]] = {}

    def initializePage(self) -> None:
        if self._state.dedupe_hits is not None:
            self._hits = self._state.dedupe_hits
            self._render_hits(self._hits)
            self._running = False
            self._done = True
            self.completeChanged.emit()
            return

        self._hits = []
        self._table.setRowCount(0)
        self._status_label.setText("正在查重…")
        self._running = True
        self._done = False
        self.completeChanged.emit()

        repo = self._state.repo_path
        left_questions = list(self._state.draft_questions)
        folder = self._state.dedupe_folder
        threshold = self._state.dedupe_threshold
        if repo is None or folder is None:
            self._on_error("查重配置缺失，请返回上一步重新设置。")
            return
        if not left_questions:
            self._on_error("当前题库草稿为空，无法执行查重。")
            return

        targets = [p for p in list_xlsx_in_folder(folder) if p.suffix.lower() == ".xlsx" and p.exists()]
        targets = [p for p in targets if p.resolve() != repo.resolve()]
        if not targets:
            self._on_done([])
            return

        thread = QThread(self)
        worker = _DedupeWorker(left_repo=repo, left_questions=left_questions, targets=targets, threshold=threshold)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_error)
        worker.done.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def isComplete(self) -> bool:
        return self._done and (not self._running)

    def nextId(self) -> int:
        return PAGE_AI_ANALYSIS_OPTION

    def _render_hits(self, hits: list[DedupeHit]) -> None:
        self._table.setRowCount(len(hits))
        for r, h in enumerate(hits):
            self._table.setItem(r, 0, QTableWidgetItem(h.left_file.name))
            self._table.setItem(r, 1, QTableWidgetItem(h.left_number or ""))
            self._table.setItem(r, 2, QTableWidgetItem(h.right_file.name))
            self._table.setItem(r, 3, QTableWidgetItem(h.right_number or ""))
            self._table.setItem(r, 4, QTableWidgetItem(f"{h.similarity:.3f}"))

    def _on_done(self, hits: list[DedupeHit]) -> None:
        self._running = False
        self._done = True
        self._thread = None
        self._worker = None

        self._hits = hits
        self._state.dedupe_hits = hits
        if not hits:
            self._status_label.setText("未发现达到阈值的重复题。")
            self._table.setRowCount(0)
        else:
            self._render_hits(hits)
            self._status_label.setText(f"查重完成：{len(hits)} 条结果（双击查看详情）")
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        self._running = False
        self._done = False
        self._thread = None
        self._worker = None
        QMessageBox.critical(self, "查重失败", msg)
        self._status_label.setText("查重失败。")
        self.completeChanged.emit()

    def _open_detail(self, row: int, col: int) -> None:
        if row < 0 or row >= len(self._hits):
            return
        hit = self._hits[row]
        dlg = _DedupeDetailDialog(self, hit=hit, loader=self._load_questions_cached)
        dlg.exec()

    def _load_questions_cached(self, path: Path) -> list[Question]:
        p = path.resolve()
        if p in self._questions_cache:
            return self._questions_cache[p]
        if self._state.repo_path is not None and p == self._state.repo_path.resolve():
            qs = list(self._state.draft_questions)
        else:
            from sj_generator.io.excel_repo import load_questions

            qs = load_questions(p)
        self._questions_cache[p] = qs
        return qs


class _DedupeWorker(QObject):
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, *, left_repo: Path, left_questions: list[Question], targets: list[Path], threshold: float) -> None:
        super().__init__()
        self._left_repo = left_repo
        self._left_questions = left_questions
        self._targets = targets
        self._threshold = threshold

    def run(self) -> None:
        try:
            hits = dedupe_between_questions_and_repos(
                left_questions=self._left_questions,
                left_file=self._left_repo,
                other_repos=self._targets,
                threshold=self._threshold,
            )
            self.done.emit(hits)
        except Exception as e:
            self.error.emit(str(e))


class _DedupeDetailDialog(QDialog):
    def __init__(self, parent, *, hit, loader) -> None:
        super().__init__(parent)
        self.setWindowTitle("查重详情")

        left = QTextEdit()
        left.setReadOnly(True)
        right = QTextEdit()
        right.setReadOnly(True)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f"相似度：{hit.similarity:.3f}"))
        layout.addWidget(splitter)
        layout.addWidget(buttons)
        self.setLayout(layout)

        left_q = _find_question(loader(hit.left_file), hit.left_number, hit.left_stem)
        right_q = _find_question(loader(hit.right_file), hit.right_number, hit.right_stem)
        left.setPlainText(_format_question_block(hit.left_file, left_q, fallback_stem=hit.left_stem))
        right.setPlainText(_format_question_block(hit.right_file, right_q, fallback_stem=hit.right_stem))


def _format_question_block(path: Path, q: Question | None, *, fallback_stem: str) -> str:
    if q is None:
        return "\n".join(
            [
                f"文件：{path.name}",
                "编号：",
                "",
                "题干：",
                (fallback_stem or "").strip(),
            ]
        ).strip() + "\n"
    return "\n".join(
        [
            f"文件：{path.name}",
            f"编号：{(q.number or '').strip()}",
            "",
            "题干：",
            (q.stem or "").strip(),
            "",
            "选项：",
            (q.options or "").strip(),
            "",
            "答案：",
            (q.answer or "").strip(),
        ]
    ).strip() + "\n"


def _find_question(questions: list[Question], number: str, stem: str) -> Question | None:
    num = (number or "").strip()
    if num:
        for q in questions:
            if (q.number or "").strip() == num:
                return q
    key = _norm_text(stem)
    if not key:
        return None
    for q in questions:
        if _norm_text(q.stem) == key:
            return q
    return None


def _norm_text(text: str) -> str:
    s = (text or "").strip().replace("\r", "\n")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

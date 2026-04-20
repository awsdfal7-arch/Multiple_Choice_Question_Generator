import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from docx import Document
from PyQt6.QtCore import QBuffer, QObject, QSizeF, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDragEnterEvent, QDropEvent, QPageSize, QPdfWriter, QTextDocument
from PyQt6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from sj_generator.ai.client import LlmClient
from sj_generator.ai.import_questions import _fingerprint_question_obj, import_questions_from_sources
from sj_generator.config import (
    load_deepseek_config,
    load_kimi_config,
    load_qwen_config,
    to_kimi_llm_config,
    to_llm_config,
    to_qwen_llm_config,
)
from sj_generator.io.source_reader import read_source_text
from sj_generator.models import Question
from sj_generator.ui.compare_highlight import compare_highlight_model_styles
from sj_generator.ui.state import WizardState, normalize_ai_concurrency
from sj_generator.ui.constants import PAGE_AI_IMPORT, PAGE_AI_IMPORT_EDIT, PAGE_AI_LEVEL_PATH
from sj_generator.ui.pdf_preview import DocumentPdfWebView


def _sanitize_filename(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r'[<>:"/\\\\|?*]+', "_", s)
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


def _rename_project(state: WizardState, *, new_name: str) -> bool:
    project_dir = state.project_dir
    repo_path = state.repo_path
    if project_dir is None or repo_path is None:
        return False
    safe = _sanitize_filename(new_name)
    if not safe:
        return False

    parent = project_dir.parent
    target_dir = _unique_child_dir(parent, safe)
    target_name = target_dir.name
    target_repo = target_dir / f"{target_name}.xlsx"

    state.project_dir = target_dir
    state.repo_path = target_repo
    state.project_name_is_placeholder = False
    return True


def _extract_paths_from_drop_event(event: QDropEvent) -> list[Path]:
    md = event.mimeData()
    if not md.hasUrls():
        return []
    out: list[Path] = []
    for u in md.urls():
        if not u.isLocalFile():
            continue
        p = Path(u.toLocalFile())
        if p.exists():
            out.append(p)
    return out


def _merge_paths_text(existing_text: str, paths: list[Path]) -> str:
    existing = [p.strip() for p in (existing_text or "").split(";") if p.strip()]
    seen: set[str] = set()
    merged: list[str] = []
    for s in existing:
        if s not in seen:
            merged.append(s)
            seen.add(s)
    for p in paths:
        s = str(p)
        if s not in seen:
            merged.append(s)
            seen.add(s)
    return "; ".join(merged)


class AiSelectFilesPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("选择资料文件")
        self.setAcceptDrops(True)

        self._files_edit = QTextEdit()
        self._files_edit.setReadOnly(True)
        self._files_edit.setPlaceholderText("选择或把待处理的 docx/txt 资料文件拖动到此处")

        browse_btn = QPushButton("选择文件…")
        browse_btn.clicked.connect(self._browse)

        row = QHBoxLayout()
        row.addWidget(browse_btn)
        row.addStretch(1)

        file_hint = QLabel("左侧上方用于选择或拖入资料文件。")
        file_hint.setWordWrap(True)

        left_top_layout = QVBoxLayout()
        left_top_layout.addLayout(row)
        left_top_layout.addWidget(self._files_edit, 1)
        left_top_layout.addWidget(file_hint)
        left_top_panel = QWidget()
        left_top_panel.setStyleSheet("border: 1px solid black;")
        left_top_panel.setLayout(left_top_layout)

        self._import_reminder = QTextEdit()
        self._import_reminder.setReadOnly(True)
        self._import_reminder.setPlainText(
            "导入提醒\n\n"
            "1. 当前支持 docx 与 txt 文件。\n"
            "2. 可点击“选择文件”或直接拖拽文件到左上区域。\n"
            "3. 建议导入前先确认文档内容完整、题号清晰。\n"
            "4. 点击“下一步”后将直接开始 AI 解析。"
        )
        left_bottom_layout = QVBoxLayout()
        left_bottom_layout.addWidget(self._import_reminder, 1)
        left_bottom_panel = QWidget()
        left_bottom_panel.setStyleSheet("border: 1px solid black;")
        left_bottom_panel.setLayout(left_bottom_layout)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(left_top_panel)
        left_splitter.addWidget(left_bottom_panel)
        left_splitter.setChildrenCollapsible(False)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        left_splitter.setSizes([300, 180])

        self._preview_placeholder = QTextEdit()
        self._preview_placeholder.setReadOnly(True)
        self._preview_placeholder.setPlainText("文档预览区域\n\n请选择文件后预览。")
        self._preview_text = QTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_pdf_view = DocumentPdfWebView(self)
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self._preview_placeholder)
        self._preview_stack.addWidget(self._preview_text)
        self._preview_stack.addWidget(self._preview_pdf_view)
        self._preview_temp_dir = TemporaryDirectory(prefix="sj_doc_preview_")
        self._preview_pdf_path = Path(self._preview_temp_dir.name) / "preview.pdf"

        right_layout = QVBoxLayout()
        right_layout.addWidget(self._preview_stack, 1)
        right_panel = QWidget()
        right_panel.setStyleSheet("border: 1px solid black;")
        right_panel.setLayout(right_layout)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.addWidget(left_splitter)
        content_splitter.addWidget(right_panel)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.setStretchFactor(0, 1)
        content_splitter.setStretchFactor(1, 1)
        content_splitter.setSizes([460, 460])

        layout = QVBoxLayout()
        layout.addWidget(content_splitter, 1)
        hint = QLabel("点击“下一步”后会直接开始解析。")
        self.setLayout(layout)

    def initializePage(self) -> None:
        if self._state.ai_source_files_text:
            self._files_edit.setPlainText(self._display_paths_text(self._state.ai_source_files_text))
            paths = self._state.ai_source_files or []
            self._update_import_reminder(paths)
            self._update_preview(paths)
        else:
            self._update_import_reminder([])
            self._update_preview([])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = _extract_paths_from_drop_event(event)
        paths = [p for p in paths if p.suffix.lower() in (".docx", ".txt")]
        if paths:
            merged = _merge_paths_text(self._serialize_paths_text(), paths)
            self._files_edit.setPlainText(self._display_paths_text(merged))
            merged_paths = [Path(p.strip()) for p in merged.split(";") if p.strip()]
            self._update_import_reminder(merged_paths)
            self._update_preview(merged_paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择资料文件", "", "Word (*.docx);;Text (*.txt);;All Files (*)"
        )
        if files:
            selected_paths = [Path(p) for p in files]
            self._files_edit.setPlainText("\n".join(files))
            self._update_import_reminder(selected_paths)
            self._update_preview(selected_paths)

    def validatePage(self) -> bool:
        raw = self._serialize_paths_text()
        if not raw:
            QMessageBox.warning(self, "未选择文件", "请选择待处理的资料文件。")
            return False
        paths = [Path(p.strip()) for p in raw.split(";") if p.strip()]
        paths = [p for p in paths if p.exists()]
        if not paths:
            QMessageBox.warning(self, "文件不存在", "请选择存在的资料文件。")
            return False
        self._state.ai_source_files = paths
        self._state.ai_source_files_text = raw
        if self._state.project_name_is_placeholder and self._state.project_dir is not None:
            first = paths[0]
            _rename_project(self._state, new_name=first.stem)
        return True

    def nextId(self) -> int:
        return PAGE_AI_IMPORT

    def _serialize_paths_text(self) -> str:
        raw = self._files_edit.toPlainText()
        parts = [p.strip() for line in raw.splitlines() for p in line.split(";") if p.strip()]
        return "; ".join(parts)

    def _display_paths_text(self, raw: str) -> str:
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        return "\n".join(parts)

    def _update_import_reminder(self, paths: list[Path]) -> None:
        if not paths:
            self._import_reminder.setPlainText(
                "导入提醒\n\n"
                "当前检查：\n"
                "1. 尚未选择文件。\n"
                "2. Word 文档中的图片检查：待检查。\n"
                "3. Word 文档中的表格检查：待检查。"
            )
            return

        lines = ["导入提醒", "", "当前检查："]
        for path in paths:
            ext = path.suffix.lower()
            lines.append(f"- {path.name}")
            if ext != ".docx":
                lines.append("  图片：仅对 Word 文档检查")
                lines.append("  表格：仅对 Word 文档检查")
                continue
            has_image, has_table, error_text = self._inspect_docx_content(path)
            if error_text:
                lines.append(f"  图片：检查失败（{error_text}）")
                lines.append(f"  表格：检查失败（{error_text}）")
                continue
            lines.append(f"  图片：{'存在' if has_image else '不存在'}")
            lines.append(f"  表格：{'存在' if has_table else '不存在'}")

        self._import_reminder.setPlainText("\n".join(lines))

    def _inspect_docx_content(self, path: Path) -> tuple[bool, bool, str]:
        try:
            doc = Document(str(path))
            has_table = len(doc.tables) > 0
            has_image = len(doc.inline_shapes) > 0
            return has_image, has_table, ""
        except Exception as e:
            return False, False, str(e)

    def _update_preview(self, paths: list[Path]) -> None:
        if not paths:
            self._preview_placeholder.setPlainText("文档预览区域\n\n请选择文件后预览。")
            self._preview_stack.setCurrentWidget(self._preview_placeholder)
            return

        path = paths[0]
        ext = path.suffix.lower()
        if ext == ".txt":
            self._preview_text.setPlainText(read_source_text(path))
            self._preview_stack.setCurrentWidget(self._preview_text)
            return
        if ext == ".docx":
            try:
                pdf_data = self._build_docx_preview_pdf(path)
                self._preview_pdf_path.write_bytes(pdf_data)
                self._preview_pdf_view.open_pdf(self._preview_pdf_path)
                self._preview_stack.setCurrentWidget(self._preview_pdf_view)
                return
            except Exception as e:
                self._preview_placeholder.setPlainText(f"文档预览区域\n\nWord 预览失败：{e}")
                self._preview_stack.setCurrentWidget(self._preview_placeholder)
                return

        self._preview_placeholder.setPlainText("文档预览区域\n\n当前文件类型暂不支持预览。")
        self._preview_stack.setCurrentWidget(self._preview_placeholder)

    def _build_docx_preview_pdf(self, path: Path) -> bytes:
        text = read_source_text(path)
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        writer = QPdfWriter(buffer)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setResolution(96)

        doc = QTextDocument()
        doc.setPlainText(text or "(文档内容为空)")
        page_size = writer.pageLayout().paintRectPixels(writer.resolution()).size()
        doc.setPageSize(QSizeF(page_size))
        doc.print(writer)
        buffer.close()
        return bytes(buffer.data())


class AiImportPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("AI 解析详情")

        self._files_edit = QLineEdit()
        self._files_edit.setReadOnly(True)
        self._files_edit.setPlaceholderText("待处理资料文件")

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFormat("正在统计题数…")

        self._stop_btn = QPushButton("停止解析")
        self._stop_btn.clicked.connect(self._stop)
        self._stop_btn.setEnabled(False)

        self._retry_btn = QPushButton("重试")
        self._retry_btn.clicked.connect(self._retry)
        self._retry_btn.setEnabled(False)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._detail_table = QTableWidget()
        self._detail_table.setColumnCount(10)
        self._detail_table.setHorizontalHeaderLabels(
            ["题号", "轮次", "DeepSeek耗时(s)", "Kimi耗时(s)", "千问耗时(s)", "DeepSeek返回", "Kimi返回", "千问返回", "比对结论", "最终答案"]
        )
        self._detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._detail_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.setWordWrap(True)
        self._detail_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._detail_table.setRowCount(0)
        self._detail_table.horizontalHeader().sectionResized.connect(
            lambda *_: self._schedule_detail_row_resize()
        )
        self._detail_row_map: dict[int, int] = {}

        row = QHBoxLayout()
        row.addWidget(self._files_edit, 1)
        row.addWidget(self._stop_btn)
        row.addWidget(self._retry_btn)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._status_label)
        layout.addWidget(self._progress)
        layout.addWidget(self._detail_table)
        self.setLayout(layout)
        
        self._items: list[Question] = []
        self._committed = False
        self._last_files_text: str = ""
        self._thread: QThread | None = None
        self._worker: _AiImportWorker | None = None
        self._running = False
        self._stopped = False
        self._finished = False
        self._failed = False
        self._progress_cur = 0
        self._progress_total = 0
        self._accepted_count = 0
        self._skipped_count = 0
        self._phase_text = "准备开始解析…"
        self._detail_text = ""
        self._parallel_text = ""
        self._consistency_text = ""
        self._detail_row_map = {}
        self._compare_secs: dict[int, dict[str, dict[int, int]]] = {}
        self._detail_row_resize_pending = False
        self._deepseek_ready = False
        self._qwen_ready = False
        self._kimi_ready = False
        self._cur_source_name = "-"
        self._cur_question_no = "-"
        self._cur_round_no = "-"

    def initializePage(self) -> None:
        self._files_edit.setText(self._state.ai_source_files_text)
        if self._state.ai_source_files:
            self._cur_source_name = self._state.ai_source_files[0].name
        if self._state.ai_source_files_text and self._state.ai_source_files_text != self._last_files_text:
            self._last_files_text = self._state.ai_source_files_text
            self._detail_table.setRowCount(0)
            self._detail_row_map = {}
            self._compare_secs = {}
            self._items = []
            self._progress.setRange(0, 0)
            self._progress.setValue(0)
            self._reset_progress_meta()
            self._committed = False
            self._running = True
            self._stopped = False
            self._finished = False
            self._failed = False
            self.completeChanged.emit()
            QTimer.singleShot(0, self._start_import)

    def nextId(self) -> int:
        return PAGE_AI_IMPORT_EDIT

    def isComplete(self) -> bool:
        if self._running or self._failed:
            return False
        if not (self._finished or self._stopped):
            return False
        return len(self._items) > 0

    def validatePage(self) -> bool:
        if self._running:
            return False
        if not self._items:
            QMessageBox.warning(self, "暂无结果", "当前没有可进入编辑的题目。")
            return False
        self._state.ai_import_questions = list(self._items)
        self.completeChanged.emit()
        return True

    def _start_import(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        paths = self._state.ai_source_files or []
        if not paths:
            QMessageBox.warning(self, "未选择文件", "请先选择待处理的资料文件。")
            return

        cfg = load_deepseek_config()
        kimi_cfg = load_kimi_config()
        qwen_cfg = load_qwen_config()
        self._deepseek_ready = cfg.is_ready()
        self._kimi_ready = kimi_cfg.is_ready()
        self._qwen_ready = qwen_cfg.is_ready()
        self._render_status()
        if not cfg.is_ready():
            QMessageBox.warning(self, "未配置", "DeepSeek 未配置：请先在配置文件中填写 API Key。")
            return

        self._status_label.setText("正在解析，请稍候…")
        self._reset_progress_meta()
        self._phase_text = "正在解析，请稍候…"
        self._render_status()
        self._stop_btn.setEnabled(True)
        self._retry_btn.setEnabled(False)
        self._running = True
        self._stopped = False
        self._finished = False
        self._failed = False
        self.completeChanged.emit()

        thread = QThread(self)
        worker = _AiImportWorker(
            cfg=cfg,
            paths=paths,
            strategy="per_question",
            max_question_workers=normalize_ai_concurrency(self._state.ai_concurrency),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.progress_count.connect(self._on_progress_count)
        worker.question.connect(self._on_question)
        worker.compare.connect(self._on_compare)
        worker.done.connect(self._on_done)
        worker.error.connect(self._on_error)
        worker.done.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_progress(self, msg: str) -> None:
        self._phase_text = msg
        self._update_detail_from_progress(msg)
        self._render_status()

    def _on_progress_count(self, cur: int, total: int) -> None:
        self._progress_cur = max(0, cur)
        self._progress_total = max(0, total)
        if total <= 0:
            self._progress.setRange(0, 0)
            self._progress.setFormat("正在统计题数…")
            self._render_status()
            return
        if self._progress.maximum() != total:
            self._progress.setRange(0, total)
        self._progress.setValue(max(0, min(cur, total)))
        self._progress.setFormat("选择题 %v/%m题")
        self._render_status()

    def _on_question(self, q: Question) -> None:
        self._items.append(q)
        self._accepted_count = len(self._items)
        self._render_status()

    def _on_compare(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        idx = int(payload.get("index") or 0)
        if idx <= 0:
            return
        row = self._detail_row_map.get(idx)
        if row is None:
            row = self._detail_table.rowCount()
            self._detail_table.setRowCount(row + 1)
            self._detail_row_map[idx] = row
            self._detail_table.setItem(row, 0, QTableWidgetItem(str(idx)))
        round_no = payload.get("round")
        round_no_int = int(round_no or 0)
        self._record_round_sec(
            idx=idx,
            round_no=round_no_int,
            model_key="deepseek",
            sec_value=payload.get("deepseek_sec"),
            ms_value=payload.get("deepseek_ms"),
        )
        self._record_round_sec(
            idx=idx,
            round_no=round_no_int,
            model_key="kimi",
            sec_value=payload.get("kimi_sec"),
            ms_value=payload.get("kimi_ms"),
        )
        self._record_round_sec(
            idx=idx,
            round_no=round_no_int,
            model_key="qwen",
            sec_value=payload.get("qwen_sec"),
            ms_value=payload.get("qwen_ms"),
        )
        self._detail_table.setItem(row, 1, QTableWidgetItem(str(round_no or "")))
        self._detail_table.setItem(
            row,
            2,
            QTableWidgetItem(self._format_round_secs(idx=idx, model_key="deepseek")),
        )
        self._detail_table.setItem(
            row,
            3,
            QTableWidgetItem(self._format_round_secs(idx=idx, model_key="kimi")),
        )
        self._detail_table.setItem(
            row,
            4,
            QTableWidgetItem(self._format_round_secs(idx=idx, model_key="qwen")),
        )
        self._detail_table.setItem(row, 5, QTableWidgetItem(self._format_payload_cell(payload.get("deepseek"))))
        self._detail_table.setItem(row, 6, QTableWidgetItem(self._format_payload_cell(payload.get("kimi"))))
        self._detail_table.setItem(row, 7, QTableWidgetItem(self._format_payload_cell(payload.get("qwen"))))
        self._apply_partial_pass_highlight(row=row, payload=payload)
        verdict = self._build_compare_verdict(payload)
        self._detail_table.setItem(row, 8, QTableWidgetItem(verdict))
        acc = payload.get("accepted_obj") or {}
        if isinstance(acc, dict):
            self._detail_table.setItem(row, 9, QTableWidgetItem(str(acc.get("answer") or "")))
        self._schedule_detail_row_resize()
        round_no = payload.get("round") or "?"
        self._cur_question_no = str(idx)
        self._cur_round_no = str(round_no)
        self._phase_text = f"解析中：已回传第 {idx} 题第 {round_no}/3 轮比对结果"
        self._render_status()

    def _schedule_detail_row_resize(self) -> None:
        if self._detail_row_resize_pending:
            return
        self._detail_row_resize_pending = True
        QTimer.singleShot(0, self._resize_detail_rows_to_contents)

    def _resize_detail_rows_to_contents(self) -> None:
        self._detail_row_resize_pending = False
        self._detail_table.resizeRowsToContents()

    def _sec_int(self, sec_value: object, ms_value: object) -> int | None:
        if isinstance(sec_value, (int, float)):
            return int(round(float(sec_value)))
        if isinstance(ms_value, (int, float)):
            return int(round(float(ms_value) / 1000.0))
        return None

    def _record_round_sec(
        self,
        *,
        idx: int,
        round_no: int,
        model_key: str,
        sec_value: object,
        ms_value: object,
    ) -> None:
        if idx <= 0 or round_no <= 0:
            return
        sec_int = self._sec_int(sec_value, ms_value)
        if sec_int is None:
            return
        if idx not in self._compare_secs:
            self._compare_secs[idx] = {}
        if model_key not in self._compare_secs[idx]:
            self._compare_secs[idx][model_key] = {}
        self._compare_secs[idx][model_key][round_no] = sec_int

    def _format_round_secs(self, *, idx: int, model_key: str) -> str:
        model_rounds = self._compare_secs.get(idx, {}).get(model_key, {})
        if not model_rounds:
            return ""
        parts = [str(model_rounds[r]) for r in sorted(model_rounds.keys())]
        return "+".join(parts)

    def _clear_compare_cell_bg(self, row: int) -> None:
        for col in [5, 6, 7]:
            item = self._detail_table.item(row, col)
            if item is not None:
                item.setBackground(QBrush())

    def _highlight_sig_text(self, obj: object) -> str:
        if isinstance(obj, dict):
            return _fingerprint_question_obj(obj)
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj.strip()
        if isinstance(obj, list) and not obj:
            return ""
        return str(obj).strip()

    def _format_payload_cell(self, value: object) -> str:
        if value is None:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    def _apply_partial_pass_highlight(self, *, row: int, payload: dict[str, object]) -> None:
        self._clear_compare_cell_bg(row)
        model_cols = [("deepseek", 5), ("kimi", 6), ("qwen", 7)]
        highlight_styles = compare_highlight_model_styles(
            model_sigs={key: self._highlight_sig_text(payload.get(key)) for key, _ in model_cols},
            round_no=int(payload.get("round") or 0),
            round_matched_count=int(payload.get("round_matched_count") or 0),
        )
        if not highlight_styles:
            return
        fail_brush = QBrush(QColor(255, 199, 206))
        empty_brush = QBrush(QColor(255, 235, 156))
        for key, col in model_cols:
            style = highlight_styles.get(key)
            if not style:
                continue
            item = self._detail_table.item(row, col)
            if item is None:
                continue
            if style == "yellow":
                item.setBackground(empty_brush)
            elif style == "red":
                item.setBackground(fail_brush)

    def _build_compare_verdict(self, payload: dict[str, object]) -> str:
        round_no = int(payload.get("round") or 0)
        round_labels = {1: "一轮", 2: "二轮", 3: "三轮"}
        label = round_labels.get(round_no, f"第{round_no}轮")
        round_matched = int(payload.get("round_matched_count") or 0)
        round_valid = int(payload.get("round_valid_count") or 0)
        matched = int(payload.get("matched_count") or 0)
        valid = int(payload.get("valid_count") or 0)
        required = int(payload.get("required_count") or 0)
        accepted = bool(payload.get("accepted"))
        status_text = "通过" if accepted else "未通过"
        if round_no <= 1:
            return f"{label}{matched}/{valid} {status_text}（阈值≥{required}，本轮{round_matched}/{round_valid}）"
        return f"{label}累计{matched}/{valid} {status_text}（阈值≥{required}，本轮{round_matched}/{round_valid}）"

    def _on_done(self, total: int) -> None:
        if self._stopped and not self._failed:
            self._phase_text = f"已停止：当前 {total} 题（请确认详情后进入编辑页）"
        else:
            self._phase_text = f"解析完成：{total} 题（请确认详情后进入编辑页）"
        if self._progress.maximum() > 0:
            self._progress.setValue(self._progress.maximum())
            self._progress_cur = self._progress.maximum()
            self._progress_total = self._progress.maximum()
        self._stop_btn.setEnabled(False)
        self._retry_btn.setEnabled(False)
        self._running = False
        self._finished = True
        self._thread = None
        self._worker = None
        self._render_status()
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        QMessageBox.critical(self, "解析失败", msg)
        self._phase_text = "解析失败。"
        self._detail_text = msg
        self._stop_btn.setEnabled(False)
        self._retry_btn.setEnabled(True)
        self._running = False
        self._failed = True
        self._thread = None
        self._worker = None
        self._render_status()
        self.completeChanged.emit()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()
            self._stop_btn.setEnabled(False)
            self._phase_text = "正在停止…"
            self._stopped = True
            self._render_status()
            self.completeChanged.emit()

    def _retry(self) -> None:
        if self._running:
            return
        self._detail_table.setRowCount(0)
        self._detail_row_map = {}
        self._compare_secs = {}
        self._items = []
        self._progress.setRange(0, 0)
        self._progress.setValue(0)
        self._progress.setFormat("正在统计题数…")
        self._reset_progress_meta()
        self._phase_text = "准备重试解析…"
        self._committed = False
        self._stopped = False
        self._finished = False
        self._failed = False
        self._retry_btn.setEnabled(False)
        self._render_status()
        self.completeChanged.emit()
        QTimer.singleShot(0, self._start_import)

    def _reset_progress_meta(self) -> None:
        self._progress_cur = 0
        self._progress_total = 0
        self._accepted_count = 0
        self._skipped_count = 0
        self._detail_text = ""
        self._parallel_text = ""
        self._consistency_text = ""
        self._cur_question_no = "-"
        self._cur_round_no = "-"
        self._render_status()

    def _update_detail_from_progress(self, msg: str) -> None:
        if "已跳过" in msg:
            self._skipped_count += 1
        msrc = re.match(r"^([^：]+)：", msg)
        if msrc:
            self._cur_source_name = msrc.group(1)
        m0 = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*题（第\s*(\d+)\s*/\s*3\s*轮，三模型并行请求中）", msg)
        if m0:
            i, n, r = m0.groups()
            self._cur_question_no = i
            self._cur_round_no = r
            self._parallel_text = f"并行状态：第 {r}/3 轮三模型并行请求中（题目 {i}/{n}）"
            return
        m1 = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*题（第\s*(\d+)\s*/\s*3\s*轮，三模型结果(一致|不一致)）", msg)
        if m1:
            i, n, r, st = m1.groups()
            self._consistency_text = f"一致性结论：第 {r}/3 轮{st}（题目 {i}/{n}）"
            if st == "一致":
                self._parallel_text = ""
            return
        m = re.search(r"第\s*(\d+)\s*/\s*(\d+)\s*题（第\s*(\d+)\s*/\s*3\s*轮，([^）]+)）", msg)
        if m:
            i, n, r, model = m.groups()
            self._cur_question_no = i
            self._cur_round_no = r
            self._detail_text = f"当前题：{i}/{n}；轮次：{r}/3；模型：{model}"
            return
        m2 = re.search(r"^(.+?)：统计题数", msg)
        if m2:
            self._cur_source_name = m2.group(1)

    def _render_status(self) -> None:
        a = "可用" if self._deepseek_ready else "不可用"
        b = "可用" if self._qwen_ready else "不可用"
        c = "可用" if self._kimi_ready else "不可用"
        line1 = f"DeepSeek：{a}；千问：{b}；Kimi：{c}"
        line2 = (
            f"资料：{self._cur_source_name}；"
            f"{self._build_question_progress_text()}；"
            f"第{self._cur_round_no}轮"
        )
        self._status_label.setText(line1 + "\n" + line2)

    def _build_question_progress_text(self) -> str:
        total = self._progress_total
        cur_question = str(self._cur_question_no).strip()
        if total > 0:
            if cur_question.isdigit():
                cur = min(max(int(cur_question), 1), total)
            else:
                cur = min(max(self._progress_cur, 0), total)
            return f"选择题 {cur}/{total}题"
        if cur_question.isdigit():
            return f"选择题 {cur_question}/?题"
        return "选择题 统计中"


class _AiImportWorker(QObject):
    progress = pyqtSignal(str)
    question = pyqtSignal(object)
    compare = pyqtSignal(object)
    progress_count = pyqtSignal(int, int)
    done = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, *, cfg, paths: list[Path], strategy: str, max_question_workers: int) -> None:
        super().__init__()
        self._cfg = cfg
        self._paths = paths
        self._strategy = strategy
        self._max_question_workers = normalize_ai_concurrency(max_question_workers)
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            sources: list[tuple[Path, str]] = []
            for p in self._paths:
                sources.append((p, read_source_text(p)))

            client = LlmClient(to_llm_config(self._cfg))
            kimi_cfg = load_kimi_config()
            qwen_cfg = load_qwen_config()
            if not kimi_cfg.is_ready() or not qwen_cfg.is_ready():
                raise RuntimeError("请先完成 Kimi 与千问配置并通过可用性测试。")
            kimi_client = LlmClient(to_kimi_llm_config(kimi_cfg))
            qwen_client = LlmClient(to_qwen_llm_config(qwen_cfg))
            result = import_questions_from_sources(
                client=client,
                kimi_client=kimi_client,
                qwen_client=qwen_client,
                client_factory=lambda: LlmClient(to_llm_config(self._cfg)),
                kimi_client_factory=lambda: LlmClient(to_kimi_llm_config(kimi_cfg)),
                qwen_client_factory=lambda: LlmClient(to_qwen_llm_config(qwen_cfg)),
                sources=sources,
                strategy=self._strategy,
                max_question_workers=self._max_question_workers,
                progress_cb=self.progress.emit,
                question_cb=self._emit_question,
                compare_cb=self._emit_compare,
                progress_count_cb=self.progress_count.emit,
                stop_cb=self._should_stop,
            )
            self.done.emit(len(result.questions))
        except Exception as e:
            self.error.emit(str(e))

    def _emit_question(self, q: Question) -> None:
        self.question.emit(q)

    def _emit_compare(self, payload: dict) -> None:
        self.compare.emit(payload)

    def _should_stop(self) -> bool:
        return self._stop


class AiImportEditPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("AI 编辑与确认")
        self._status = QLabel("请确认题目后继续。")
        self._status.setWordWrap(True)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["编号", "题目", "选项", "答案", "解析"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(0, True)
        self._table.setColumnHidden(4, True)
        self._table.setWordWrap(True)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.setRowCount(0)
        self._table.itemChanged.connect(self._on_item_changed)

        self._delete_btn = QPushButton("删除选中行")
        self._delete_btn.clicked.connect(self._delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(self._delete_btn)
        btns.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._status)
        layout.addWidget(self._table)
        layout.addLayout(btns)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._table.setRowCount(0)
        qs = self._state.ai_import_questions or []
        for i, q in enumerate(qs, start=1):
            r = self._table.rowCount()
            self._table.setRowCount(r + 1)
            self._table.setItem(r, 0, QTableWidgetItem(str(i)))
            self._table.setItem(r, 1, QTableWidgetItem(q.stem or ""))
            self._table.setItem(r, 2, QTableWidgetItem(q.options or ""))
            self._table.setItem(r, 3, QTableWidgetItem(q.answer or ""))
            self._table.setItem(r, 4, QTableWidgetItem(q.analysis or ""))
            self._table.resizeRowToContents(r)
        self._status.setText(f"当前可编辑题目：{self._table.rowCount()} 题")

    def nextId(self) -> int:
        return PAGE_AI_LEVEL_PATH

    def validatePage(self) -> bool:
        questions: list[Question] = []
        for r in range(self._table.rowCount()):
            number = self._table.item(r, 0).text().strip() if self._table.item(r, 0) else ""
            stem = self._table.item(r, 1).text().strip() if self._table.item(r, 1) else ""
            options = self._table.item(r, 2).text().strip() if self._table.item(r, 2) else ""
            answer = self._table.item(r, 3).text().strip() if self._table.item(r, 3) else ""
            analysis = self._table.item(r, 4).text().strip() if self._table.item(r, 4) else ""
            if not any([number, stem, options, answer, analysis]):
                continue
            questions.append(Question(number=number, stem=stem, options=options, answer=answer, analysis=analysis))
        if not questions:
            QMessageBox.warning(self, "没有可写入内容", "没有可写入的题目。")
            return False
        self._state.draft_questions = questions
        return True

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        self._table.resizeRowToContents(item.row())

    def _delete_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            self._table.removeRow(r)
        for r in range(self._table.rowCount()):
            self._table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
        self._status.setText(f"当前可编辑题目：{self._table.rowCount()} 题")

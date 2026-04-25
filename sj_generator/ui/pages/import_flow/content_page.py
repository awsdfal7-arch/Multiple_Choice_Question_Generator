import os
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtGui import QBrush, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
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
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from sj_generator.infrastructure.llm.import_questions import (
    question_content_round_limit,
    question_content_provider_ready,
)
from sj_generator.infrastructure.llm.question_ref_scan import question_ref_model_specs
from sj_generator.application.settings import (
    load_deepseek_config,
    load_kimi_config,
    load_project_parse_model_rows,
    load_qwen_config,
)
from sj_generator.domain.entities import Question
from sj_generator.ui.import_content_detail import (
    apply_compare_row_background,
    apply_content_detail_column_widths,
    apply_partial_pass_highlight,
    build_compare_verdict,
    content_detail_width_signature,
    effective_content_question_workers,
    format_json_cell,
    format_round_secs,
    question_content_active_model_specs,
    question_content_detail_headers,
    question_content_model_signature,
    question_content_payload_model_specs,
    question_ref_total_count,
    record_round_sec,
    set_content_detail_item,
)
from sj_generator.ui.import_page_common import (
    LevelPathItemDelegate,
    PreserveCellBackgroundDelegate,
    extract_paths_from_drop_event,
    is_valid_level_path,
    merge_paths_text,
    rename_project,
    style_busy_progress,
    style_dialog_button,
)
from sj_generator.ui.import_select_reminder import populate_import_reminder_columns
from sj_generator.ui.import_select_session import (
    build_opened_doc_session,
    poll_opened_doc_sessions,
    select_first_changed_row,
)
from sj_generator.ui.import_db_service import commit_draft_questions_to_db
from sj_generator.ui.import_progress import parse_content_progress_message
from sj_generator.ui.import_question_ref_detail import (
    build_manual_type_combo,
    build_question_ref_detail_rows,
    populate_question_ref_detail_table,
    question_ref_detail_headers,
    question_ref_detail_model_specs,
)
from sj_generator.ui.message_box import show_message_box
from sj_generator.ui.import_costs import capture_import_cost_before, copy_import_cost_before, freeze_import_cost_result
from sj_generator.ui.import_ref_session import QuestionRefRuntimeState
from sj_generator.ui.import_workers import AiImportContentWorker, AiQuestionRefWorker
from sj_generator.ui.table_copy import CopyableTableWidget
from sj_generator.application.state import AiSourceFileItem, WizardState, build_import_flow_state, normalize_ai_concurrency
from sj_generator.ui.constants import (
    PAGE_AI_ANALYSIS,
    PAGE_AI_IMPORT,
    PAGE_AI_IMPORT_CONTENT,
    PAGE_DEDUPE_RESULT,
    PAGE_IMPORT_SUCCESS,
)

class AiImportContentPage(QWizardPage):
    _DETAIL_TABLE_FONT_POINT_SIZE_MIN = 8
    _DETAIL_TABLE_FONT_POINT_SIZE_MAX = 28

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("题目内容解析")
        self._detail_row_resize_pending = False
        self._detail_table_font_point_size = self._load_detail_table_font_point_size()

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFormat("正在统计题数…")
        style_busy_progress(self._progress)

        self._stop_btn = QPushButton("停止解析")
        self._stop_btn.clicked.connect(self._stop)
        self._stop_btn.setEnabled(False)
        self._stop_btn.hide()

        self._retry_btn = QPushButton("重试")
        self._retry_btn.clicked.connect(self._retry)
        self._retry_btn.setEnabled(False)
        self._retry_btn.hide()

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._detail_table = CopyableTableWidget(zoom_callback=self._adjust_detail_table_font_size)
        self._detail_table.setColumnCount(len(question_content_detail_headers()))
        self._detail_table.setHorizontalHeaderLabels(question_content_detail_headers())
        self._detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._detail_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.setWordWrap(True)
        self._detail_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._detail_table.setRowCount(0)
        self._detail_table.horizontalHeader().sectionResized.connect(
            lambda *_: self._schedule_detail_row_resize()
        )
        self._detail_row_map: dict[int, int] = {}
        self._detail_width_signature = ""
        self._apply_detail_table_font_size()

        layout = QVBoxLayout()
        layout.addWidget(self._status_label)
        layout.addWidget(self._detail_table)
        self.setLayout(layout)

        self._items: list[Question] = []
        self._last_files_text: str = ""
        self._last_ref_version: int = -1
        self._last_content_model_signature: str = ""
        self._thread: QThread | None = None
        self._worker: AiImportContentWorker | None = None
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
        self._content_model_specs = question_content_active_model_specs()
        self._content_model_ready: dict[str, bool] = {}
        self._cur_source_name = "-"
        self._cur_question_no = "-"
        self._cur_round_no = "-"

    def initializePage(self) -> None:
        self._sync_wizard_buttons()
        if self._state.ai_source_files:
            self._cur_source_name = self._state.ai_source_files[0].name
        ref_version = int(getattr(self._state, "ai_question_refs_version", 0))
        content_model_specs = question_content_active_model_specs()
        content_model_signature = question_content_model_signature(content_model_specs)
        self._content_model_specs = content_model_specs
        self._detail_table.setColumnCount(len(question_content_detail_headers(self._content_model_specs)))
        self._detail_table.setHorizontalHeaderLabels(question_content_detail_headers(self._content_model_specs))
        self._apply_content_detail_column_widths_if_needed(model_specs=self._content_model_specs)
        should_restart = (
            self._state.ai_source_files_text
            and (
                self._state.ai_source_files_text != self._last_files_text
                or ref_version != self._last_ref_version
                or content_model_signature != self._last_content_model_signature
            )
        )
        should_auto_start = bool(
            self._state.ai_source_files_text
            and question_ref_total_count(self._state.ai_question_refs_by_source) > 0
            and not self._running
            and not self._items
            and (self._thread is None or not self._thread.isRunning())
        )
        if should_restart or should_auto_start:
            self._last_files_text = self._state.ai_source_files_text
            self._last_ref_version = ref_version
            self._last_content_model_signature = content_model_signature
            if should_restart:
                self._detail_table.setRowCount(0)
                self._detail_row_map = {}
                self._compare_secs = {}
                self._items = []
                self._progress.setRange(0, 0)
                self._progress.setValue(0)
            self._reset_progress_meta()
            self._phase_text = "正在解析题目内容，请稍候…"
            self._status_label.setText(self._phase_text)
            self._running = True
            self._stopped = False
            self._finished = False
            self._failed = False
            self._render_status()
            self.completeChanged.emit()
            QTimer.singleShot(30, self._start_import)

    def nextId(self) -> int:
        if self._state.dedupe_enabled:
            return PAGE_DEDUPE_RESULT
        return PAGE_AI_ANALYSIS if self._state.analysis_enabled else PAGE_IMPORT_SUCCESS

    def _sync_wizard_buttons(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        back_button = wizard.button(QWizard.WizardButton.BackButton)
        if back_button is not None:
            back_button.setVisible(False)
        next_text = "开始导题"
        if self._running:
            next_text = "解析中…"
        elif self._failed:
            next_text = "重试后继续"
        elif self._items:
            if self._state.dedupe_enabled:
                next_text = "进入查重"
            elif self._state.analysis_enabled:
                next_text = "进入解析"
            else:
                next_text = "写入题库"
        wizard.setButtonText(QWizard.WizardButton.NextButton, next_text)
        self._sync_custom_wizard_button()

    def _sync_custom_wizard_button(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        stop_button = wizard.button(QWizard.WizardButton.CustomButton1)
        retry_button = wizard.button(QWizard.WizardButton.CustomButton2)
        if stop_button is not None:
            stop_button.setText("停止解析")
            stop_button.setVisible(True)
            stop_button.setEnabled(self._stop_btn.isEnabled())
            try:
                stop_button.clicked.disconnect()
            except TypeError:
                pass
            stop_button.clicked.connect(self._stop)
        if retry_button is not None:
            retry_button.setText("重试")
            retry_button.setVisible(True)
            retry_button.setEnabled(self._retry_btn.isEnabled())
            try:
                retry_button.clicked.disconnect()
            except TypeError:
                pass
            retry_button.clicked.connect(self._retry)

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
            show_message_box(self, title="暂无结果", text="当前没有可进入下一步的题目。", icon=QMessageBox.Icon.Warning)
            return False
        self._state.ai_import_questions = list(self._items)
        self._state.draft_questions = list(self._items)
        self._state.dedupe_hits = None
        self._state.reset_db_import()
        freeze_import_cost_result(self._state)
        if not self._state.dedupe_enabled and not self._state.analysis_enabled:
            return commit_draft_questions_to_db(self, self._state)
        self.completeChanged.emit()
        return True

    def _start_import(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        paths = self._state.ai_source_files or []
        if not paths:
            show_message_box(self, title="未选择文件", text="请先选择待处理的资料文件。", icon=QMessageBox.Icon.Warning)
            return
        if question_ref_total_count(self._state.ai_question_refs_by_source) <= 0:
            show_message_box(self, title="缺少题号", text="请先完成题号与题型解析。", icon=QMessageBox.Icon.Warning)
            return

        self._content_model_specs = question_content_active_model_specs()
        ready_map = {
            str(spec.get("key") or ""): question_content_provider_ready(str(spec.get("provider") or ""))
            for spec in self._content_model_specs
        }
        self._content_model_ready = ready_map
        self._render_status()
        if not self._content_model_specs or not all(ready_map.values()):
            self._sync_wizard_buttons()
            missing_labels = [
                str(spec.get("label") or spec.get("provider") or spec.get("key") or "").replace("\n", " / ")
                for spec in self._content_model_specs
                if not ready_map.get(str(spec.get("key") or ""), False)
            ]
            missing_text = "、".join(label for label in missing_labels if label) or "题目内容解析模型"
            show_message_box(
                self,
                title="未配置",
                text=f"请先完成以下题目内容解析模型配置并通过可用性测试：{missing_text}",
                icon=QMessageBox.Icon.Warning,
            )
            return

        self._status_label.setText("正在解析题目内容，请稍候…")
        self._reset_progress_meta()
        self._phase_text = "正在解析题目内容，请稍候…"
        self._render_status()
        self._stop_btn.setEnabled(True)
        self._retry_btn.setEnabled(False)
        self._running = True
        self._stopped = False
        self._finished = False
        self._failed = False
        self._sync_wizard_buttons()
        self.completeChanged.emit()

        thread = QThread(self)
        worker = AiImportContentWorker(
            model_specs=self._content_model_specs,
            paths=paths,
            question_refs_by_source=dict(self._state.ai_question_refs_by_source),
            strategy="per_question",
            max_question_workers=effective_content_question_workers(
                normalize_ai_concurrency(self._state.question_content_concurrency),
                len(self._content_model_specs),
            ),
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
        model_specs = question_content_payload_model_specs(payload, self._content_model_specs)
        self._content_model_specs = model_specs
        self._detail_table.setColumnCount(len(question_content_detail_headers(model_specs)))
        self._detail_table.setHorizontalHeaderLabels(question_content_detail_headers(model_specs))
        self._apply_content_detail_column_widths_if_needed(model_specs=model_specs)
        row = self._detail_row_map.get(idx)
        if row is None:
            row = self._detail_table.rowCount()
            self._detail_table.setRowCount(row + 1)
            self._detail_row_map[idx] = row
        display_number = str(payload.get("requested_number") or idx)
        set_content_detail_item(self._detail_table, row, 0, display_number)
        round_no = payload.get("round")
        round_no_int = int(round_no or 0)
        costs_sec_by_model = payload.get("costs_sec_by_model") if isinstance(payload.get("costs_sec_by_model"), dict) else {}
        results_by_model = payload.get("results_by_model") if isinstance(payload.get("results_by_model"), dict) else {}
        for spec in model_specs:
            model_key = str(spec.get("key") or "")
            record_round_sec(
                self._compare_secs,
                idx=idx,
                round_no=round_no_int,
                model_key=model_key,
                sec_value=costs_sec_by_model.get(model_key),
                ms_value=None,
            )
        for col_index, spec in enumerate(model_specs):
            model_key = str(spec.get("key") or "")
            set_content_detail_item(
                self._detail_table,
                row,
                1 + col_index,
                format_round_secs(self._compare_secs, idx=idx, model_key=model_key),
            )
        value_col_offset = 1 + len(model_specs)
        for col_index, spec in enumerate(model_specs):
            model_key = str(spec.get("key") or "")
            set_content_detail_item(
                self._detail_table,
                row,
                value_col_offset + col_index,
                format_json_cell(results_by_model.get(model_key)),
                is_json=True,
            )
        verdict = build_compare_verdict(payload)
        set_content_detail_item(self._detail_table, row, value_col_offset + len(model_specs), verdict)
        apply_compare_row_background(self._detail_table, row=row, payload=payload)
        apply_partial_pass_highlight(self._detail_table, row=row, payload=payload, model_specs=model_specs)
        self._schedule_detail_row_resize()
        round_no = payload.get("round") or "?"
        self._cur_question_no = str(idx)
        self._cur_round_no = str(round_no)
        round_limit = max(1, int(payload.get("round_limit") or len(model_specs) or 1))
        self._phase_text = f"解析中：已回传第 {idx} 题第 {round_no}/{round_limit} 轮比对结果"
        self._render_status()

    def _schedule_detail_row_resize(self) -> None:
        if getattr(self, "_detail_row_resize_pending", False):
            return
        self._detail_row_resize_pending = True
        QTimer.singleShot(0, self._resize_detail_rows_to_contents)

    def _resize_detail_rows_to_contents(self) -> None:
        self._detail_row_resize_pending = False
        self._detail_table.resizeRowsToContents()

    def _load_detail_table_font_point_size(self) -> int:
        default_size = self.font().pointSize()
        if default_size <= 0:
            default_size = 10
        return max(
            self._DETAIL_TABLE_FONT_POINT_SIZE_MIN,
            min(self._DETAIL_TABLE_FONT_POINT_SIZE_MAX, default_size),
        )

    def _apply_detail_table_font_size(self) -> None:
        font = self._detail_table.font()
        font.setPointSize(self._detail_table_font_point_size)
        self._detail_table.setFont(font)
        self._detail_table.horizontalHeader().setFont(font)
        self._detail_table.verticalHeader().setFont(font)
        self._detail_table.doItemsLayout()
        self._detail_table.viewport().update()
        self._detail_table.horizontalHeader().viewport().update()
        self._schedule_detail_row_resize()

    def _adjust_detail_table_font_size(self, step: int) -> None:
        new_size = max(
            self._DETAIL_TABLE_FONT_POINT_SIZE_MIN,
            min(self._DETAIL_TABLE_FONT_POINT_SIZE_MAX, self._detail_table_font_point_size + int(step)),
        )
        if new_size == self._detail_table_font_point_size:
            return
        self._detail_table_font_point_size = new_size
        self._apply_detail_table_font_size()

    def _content_detail_width_signature(self, model_specs: list[dict[str, str]]) -> str:
        return content_detail_width_signature(model_specs)

    def _apply_content_detail_column_widths_if_needed(self, *, model_specs: list[dict[str, str]]) -> None:
        signature = self._content_detail_width_signature(model_specs)
        if signature == self._detail_width_signature:
            return
        self._detail_width_signature = signature
        QTimer.singleShot(0, lambda specs=list(model_specs): self._apply_content_detail_column_widths(specs))

    def _apply_content_detail_column_widths(self, model_specs: list[dict[str, str]]) -> None:
        apply_content_detail_column_widths(self._detail_table, model_specs)

    def _set_content_detail_item(self, row: int, col: int, text: str, *, is_json: bool = False) -> None:
        set_content_detail_item(self._detail_table, row, col, text, is_json=is_json)

    def _on_done(self, total: int) -> None:
        if self._stopped and not self._failed:
            self._phase_text = f"已停止：当前 {total} 题（可继续进入下一步）"
        else:
            self._phase_text = f"解析完成：{total} 题（可继续进入下一步）"
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
        self._sync_wizard_buttons()
        self.completeChanged.emit()

    def _on_error(self, msg: str) -> None:
        show_message_box(self, title="解析失败", text=msg, icon=QMessageBox.Icon.Critical)
        self._phase_text = f"解析失败：{msg}"
        self._detail_text = msg
        self._append_failure_row(msg)
        self._stop_btn.setEnabled(False)
        self._retry_btn.setEnabled(True)
        self._running = False
        self._failed = True
        self._thread = None
        self._worker = None
        self._render_status()
        self._sync_wizard_buttons()
        self.completeChanged.emit()

    def _append_failure_row(self, msg: str) -> None:
        row = self._detail_table.rowCount()
        self._detail_table.setRowCount(row + 1)
        text = f"失败：{msg}"
        self._set_content_detail_item(row, 0, "-")
        payload_col_start = 1 + len(self._content_model_specs)
        payload_col_end = payload_col_start + len(self._content_model_specs)
        for col in range(payload_col_start, payload_col_end):
            self._set_content_detail_item(row, col, "", is_json=True)
        self._set_content_detail_item(row, payload_col_end, text)
        self._schedule_detail_row_resize()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()
            self._stop_btn.setEnabled(False)
            self._phase_text = "正在停止…"
            self._stopped = True
            self._render_status()
            self._sync_wizard_buttons()
            self.completeChanged.emit()

    def prepare_to_close(self) -> bool:
        thread = self._thread
        if thread is None or (not thread.isRunning()):
            return True
        if self._worker is not None:
            self._worker.request_stop()
        self._stop_btn.setEnabled(False)
        self._phase_text = "正在停止…"
        self._stopped = True
        self._render_status()
        show_message_box(
            self,
            title="正在停止",
            text="解析线程仍在收尾，请稍候片刻后再关闭窗口。",
            icon=QMessageBox.Icon.Information,
        )
        return False

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
        self._stopped = False
        self._finished = False
        self._failed = False
        self._retry_btn.setEnabled(False)
        self._render_status()
        self._sync_wizard_buttons()
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
        parsed = parse_content_progress_message(msg)
        self._skipped_count += int(parsed.get("skipped_delta") or 0)
        source_name = str(parsed.get("source_name") or "").strip()
        if source_name:
            self._cur_source_name = source_name
        question_no = str(parsed.get("question_no") or "").strip()
        if question_no:
            self._cur_question_no = question_no
        round_no = str(parsed.get("round_no") or "").strip()
        if round_no:
            self._cur_round_no = round_no
        if "parallel_text" in parsed:
            self._parallel_text = str(parsed.get("parallel_text") or "")
            return
        if "consistency_text" in parsed:
            self._consistency_text = str(parsed.get("consistency_text") or "")
            if "parallel_text" in parsed:
                self._parallel_text = str(parsed.get("parallel_text") or "")
            return
        detail_text = str(parsed.get("detail_text") or "")
        if detail_text:
            self._detail_text = detail_text
            return

    def _render_status(self) -> None:
        line = (
            f"{self._build_question_progress_text()}；"
            f"最高轮次 {question_content_round_limit()}轮；"
            f"并发 {normalize_ai_concurrency(self._state.question_content_concurrency)} 路"
        )
        available_text = self._build_available_question_text()
        if available_text:
            line = f"{line}；{available_text}"
        self._status_label.setText(line)

    def _build_question_progress_text(self) -> str:
        total = self._progress_total
        if total > 0:
            completed = min(max(int(self._progress_cur), 0), total)
            return f"选择题 {completed}/{total}题"
        return "选择题 统计中"

    def _build_available_question_text(self) -> str:
        total = max(0, int(self._progress_total))
        completed = min(max(int(self._progress_cur), 0), total) if total > 0 else 0
        if self._running or self._failed or self._stopped or not self._finished or total <= 0 or completed < total:
            return ""
        return f"可用题目 {max(0, int(self._accepted_count))}题"

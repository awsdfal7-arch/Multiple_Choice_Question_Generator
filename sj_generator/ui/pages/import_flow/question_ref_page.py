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

class AiImportPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("题号与题型解析")
        self._detail_row_resize_pending = False

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFormat("正在统计题数…")
        style_busy_progress(self._progress)
        self._progress.hide()

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
        self._status_label.hide()

        self._detail_table = CopyableTableWidget()
        self._detail_table.setColumnCount(len(question_ref_detail_headers()))
        self._detail_table.setHorizontalHeaderLabels(question_ref_detail_headers())
        self._detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._apply_question_ref_detail_column_widths()
        self._detail_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.setShowGrid(True)
        self._detail_table.setWordWrap(True)
        self._detail_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._detail_table.setStyleSheet(
            """
            QTableWidget {
                border: 1px solid black;
                gridline-color: black;
                selection-background-color: rgba(0, 0, 0, 0);
                selection-color: black;
            }
            """
        )
        self._detail_table.setRowCount(0)
        self._detail_table.itemSelectionChanged.connect(self._sync_open_doc_button)
        self._detail_table.horizontalHeader().sectionResized.connect(
            lambda *_: self._schedule_detail_row_resize()
        )
        self._detail_row_map: dict[int, int] = {}
        layout = QVBoxLayout()
        layout.addWidget(self._detail_table)
        self.setLayout(layout)

        self._ref_state = QuestionRefRuntimeState()
        self._last_files_text: str = ""
        self._thread: QThread | None = None
        self._worker: AiQuestionRefWorker | None = None
        self._running = False
        self._stopped = False
        self._finished = False
        self._failed = False
        self._phase_text = "准备开始解析…"
        self._detail_row_map = {}
        self._deepseek_ready = False
        self._qwen_ready = False
        self._kimi_ready = False
        self._wait_timer = QTimer(self)
        self._wait_timer.setInterval(1000)
        self._wait_timer.timeout.connect(self._refresh_waiting_detail_rows)

    def initializePage(self) -> None:
        self._sync_wizard_buttons()
        if self._state.ai_source_files:
            self._ref_state.cur_source_name = self._state.ai_source_files[0].name
        if self._state.ai_source_files_text and self._state.ai_source_files_text != self._last_files_text:
            self._last_files_text = self._state.ai_source_files_text
            self._detail_table.setRowCount(0)
            self._detail_row_map = {}
            self._wait_timer.stop()
            self._ref_state.reset(source_name=self._ref_state.cur_source_name)
            self._progress.setRange(0, 0)
            self._progress.setValue(0)
            self._reset_progress_meta()
            self._phase_text = "正在解析题号与题型，请稍候…"
            self._status_label.setText(self._phase_text)
            self._state.ai_question_refs_by_source = {}
            self._running = True
            self._stopped = False
            self._finished = False
            self._failed = False
            self._render_status()
            self.completeChanged.emit()
            self.repaint()
            QApplication.processEvents()
            QTimer.singleShot(30, self._start_import)

    def nextId(self) -> int:
        return PAGE_AI_IMPORT_CONTENT

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
        elif self._ref_state.question_refs_by_source:
            next_text = "进入题目内容解析"
        wizard.setButtonText(QWizard.WizardButton.NextButton, next_text)

    def _sync_custom_wizard_button(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        button = wizard.button(QWizard.WizardButton.CustomButton2)
        if button is None:
            return
        button.setText("打开文档")
        button.setVisible(True)
        button.setEnabled(self._detail_table.currentRow() >= 0)
        try:
            button.clicked.disconnect()
        except TypeError:
            pass
        button.clicked.connect(self._open_selected_source_doc)
        self._sync_open_doc_button()

    def _sync_open_doc_button(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        button = wizard.button(QWizard.WizardButton.CustomButton2)
        if button is None:
            return
        button.setEnabled(self._detail_table.currentRow() >= 0)

    def isComplete(self) -> bool:
        if self._running or self._failed:
            return False
        if not (self._finished or self._stopped):
            return False
        if self._ref_state.has_unresolved_manual_types():
            return False
        return self._ref_state.accepted_count > 0

    def validatePage(self) -> bool:
        if self._running:
            return False
        if question_ref_total_count(self._ref_state.question_refs_by_source) <= 0:
            show_message_box(self, title="暂无结果", text="当前没有可进入下一步的题号。", icon=QMessageBox.Icon.Warning)
            return False
        if self._ref_state.has_unresolved_manual_types():
            show_message_box(
                self,
                title="请选择题型",
                text="当前仍有“对比结论不一致”的题目未选择题型，请先手动选择后再继续。",
                icon=QMessageBox.Icon.Warning,
            )
            return False
        self._state.ai_question_refs_by_source = dict(self._ref_state.question_refs_by_source)
        self._state.ai_question_refs_version = int(getattr(self._state, "ai_question_refs_version", 0)) + 1
        self._state.ai_import_questions = None
        self._state.draft_questions = []
        self._state.dedupe_hits = None
        self._state.reset_db_import()
        self.completeChanged.emit()
        return True

    def _start_import(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        paths = self._state.ai_source_files or []
        if not paths:
            show_message_box(self, title="未选择文件", text="请先选择待处理的资料文件。", icon=QMessageBox.Icon.Warning)
            return

        cfg = load_deepseek_config()
        kimi_cfg = load_kimi_config()
        qwen_cfg = load_qwen_config()
        self._deepseek_ready = cfg.is_ready()
        self._kimi_ready = kimi_cfg.is_ready()
        self._qwen_ready = qwen_cfg.is_ready()
        self._render_status()
        ready_map = {
            "deepseek": cfg.is_ready(),
            "kimi": kimi_cfg.is_ready(),
            "qwen": qwen_cfg.is_ready(),
        }
        required_providers = {
            str(item.get("provider") or "").strip().lower()
            for item in question_ref_model_specs()
            if str(item.get("provider") or "").strip()
        }
        missing_labels = [
            {"deepseek": "DeepSeek", "kimi": "Kimi", "qwen": "千问"}.get(provider, provider)
            for provider in required_providers
            if not ready_map.get(provider, False)
        ]
        if missing_labels:
            self._sync_wizard_buttons()
            show_message_box(
                self,
                title="未配置",
                text="题号与题型解析所需模型尚未全部可用：" + "、".join(missing_labels),
                icon=QMessageBox.Icon.Warning,
            )
            return

        self._status_label.setText("正在解析题号与题型，请稍候…")
        self._reset_progress_meta()
        self._wait_timer.stop()
        self._ref_state.stop_waiting_feedback()
        self._phase_text = "正在解析题号与题型，请稍候…"
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
        worker = AiQuestionRefWorker(
            cfg=cfg,
            paths=paths,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.scan_progress.connect(self._on_scan_progress)
        worker.progress_count.connect(self._on_progress_count)
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
        self._ref_state.update_progress(cur, total)
        if total <= 0:
            self._progress.setRange(0, 0)
            self._progress.setFormat("正在统计资料…")
            self._render_status()
            return
        if self._progress.maximum() != total:
            self._progress.setRange(0, total)
        self._progress.setValue(max(0, min(cur, total)))
        self._progress.setFormat("资料 %v/%m个")
        self._render_status()

    def _on_compare(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        source_name = self._ref_state.apply_compare_payload(payload)
        if not source_name:
            return
        self._render_question_ref_detail_rows()
        self._phase_text = f"题号与题型解析中：{source_name} 已返回结果"
        self._render_status()

    def _schedule_detail_row_resize(self) -> None:
        if getattr(self, "_detail_row_resize_pending", False):
            return
        self._detail_row_resize_pending = True
        QTimer.singleShot(0, self._resize_detail_rows_to_contents)

    def _resize_detail_rows_to_contents(self) -> None:
        self._detail_row_resize_pending = False
        self._detail_table.resizeRowsToContents()

    def _on_done(self, result: object) -> None:
        self._wait_timer.stop()
        self._ref_state.stop_waiting_feedback()
        self._ref_state.apply_done_payload(result)
        self._render_question_ref_detail_rows()
        total = self._ref_state.accepted_count
        if self._stopped and not self._failed:
            self._phase_text = f"已停止：当前已识别 {total} 个题号（可继续进入下一步）"
        else:
            self._phase_text = f"题号与题型解析完成：共 {total} 个题号（可继续进入下一步）"
        if self._progress.maximum() > 0:
            self._progress.setValue(self._progress.maximum())
            self._ref_state.update_progress(self._progress.maximum(), self._progress.maximum())
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
        self._wait_timer.stop()
        self._ref_state.stop_waiting_feedback()
        show_message_box(self, title="解析失败", text=msg, icon=QMessageBox.Icon.Critical)
        self._phase_text = f"题号与题型解析失败：{msg}"
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
        values = ["失败"] + [""] * (self._detail_table.columnCount() - 3) + [f"失败：{msg}", ""]
        for col, value in enumerate(values):
            self._detail_table.setItem(row, col, QTableWidgetItem(value))
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
        self._wait_timer.stop()
        self._ref_state.reset(source_name=self._ref_state.cur_source_name)
        self._detail_table.setRowCount(0)
        self._detail_row_map = {}
        self._progress.setRange(0, 0)
        self._progress.setValue(0)
        self._progress.setFormat("正在统计资料…")
        self._reset_progress_meta()
        self._phase_text = "准备重试题号与题型解析…"
        self._stopped = False
        self._finished = False
        self._failed = False
        self._retry_btn.setEnabled(False)
        self._render_status()
        self._sync_wizard_buttons()
        self.completeChanged.emit()
        QTimer.singleShot(0, self._start_import)

    def _reset_progress_meta(self) -> None:
        self._ref_state.reset_progress()
        self._render_status()

    def _update_detail_from_progress(self, msg: str) -> None:
        self._ref_state.update_source_from_progress(msg)

    def _render_status(self) -> None:
        self._status_label.setText(self._ref_state.status_text())

    def _build_question_progress_text(self) -> str:
        return self._ref_state.question_progress_text()

    def _render_question_ref_detail_rows(self) -> None:
        model_specs = question_ref_detail_model_specs(self._ref_state.question_ref_payloads)
        header_labels = question_ref_detail_headers(model_specs)
        self._detail_table.setColumnCount(len(header_labels))
        self._detail_table.setHorizontalHeaderLabels(header_labels)
        self._apply_question_ref_detail_column_widths()
        self._detail_table.clearContents()
        rows = build_question_ref_detail_rows(
            payloads=self._ref_state.question_ref_payloads,
            model_specs=model_specs,
            resolve_manual_type=self._ref_state.resolve_manual_question_type,
        )
        waiting_rows = self._ref_state.build_waiting_detail_rows(model_specs)
        rows.extend(waiting_rows)
        manual_col = self._detail_table.columnCount() - 1
        populate_question_ref_detail_table(
            table=self._detail_table,
            rows=rows,
            manual_col=manual_col,
            overridden_pairs=set(self._ref_state.manual_question_type_overrides.keys()),
            build_combo=self._build_manual_type_combo,
        )
        self._schedule_detail_row_resize()

    def _apply_question_ref_detail_column_widths(self) -> None:
        header = self._detail_table.horizontalHeader()
        column_count = self._detail_table.columnCount()
        if column_count <= 0:
            return
        for col in range(max(0, column_count - 1)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        for col in range(max(0, column_count - 1), column_count):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self._detail_table.setColumnWidth(col, 120)

    def _build_manual_type_combo(
        self,
        *,
        source_key: str,
        number: str,
        current_type: str,
        background: QBrush | None,
    ) -> QComboBox:
        return build_manual_type_combo(
            table=self._detail_table,
            current_type=current_type,
            background=background,
            on_changed=lambda text, sk=source_key, num=number: self._on_manual_question_type_changed(sk, num, text),
        )

    def _open_source_doc(self, source_key: str) -> None:
        path = Path(str(source_key or "").strip())
        if not path.exists():
            show_message_box(self, title="文件不存在", text=f"未找到文档：{path}", icon=QMessageBox.Icon.Warning)
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            show_message_box(self, title="打开失败", text=f"无法打开 Word 文档：{e}", icon=QMessageBox.Icon.Critical)

    def _open_selected_source_doc(self) -> None:
        self._sync_open_doc_button()
        row = self._detail_table.currentRow()
        if row < 0:
            show_message_box(self, title="未选择题目", text="请先在表格中选择一行。", icon=QMessageBox.Icon.Warning)
            return
        item = self._detail_table.item(row, 0)
        source_key = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
        if not source_key:
            show_message_box(self, title="无法打开", text="当前行没有可打开的来源文档。", icon=QMessageBox.Icon.Warning)
            return
        self._open_source_doc(source_key)

    def _on_manual_question_type_changed(self, source_key: str, number: str, value: str) -> None:
        self._ref_state.set_manual_question_type(source_key, number, value)
        self._render_status()
        self._sync_wizard_buttons()
        self.completeChanged.emit()
        self._render_question_ref_detail_rows()

    def _on_scan_progress(self, payload: object) -> None:
        action = self._ref_state.handle_scan_progress(payload)
        if action == "start_waiting":
            self._render_question_ref_detail_rows()
            self._wait_timer.start()
            return
        if action == "refresh_waiting":
            self._refresh_waiting_detail_rows()

    def _refresh_waiting_detail_rows(self) -> None:
        if not self._ref_state.waiting_model_specs:
            return
        self._render_question_ref_detail_rows()
        self._ref_state.tick_waiting_feedback()



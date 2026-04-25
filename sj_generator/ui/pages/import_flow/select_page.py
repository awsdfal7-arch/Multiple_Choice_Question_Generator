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

class AiSelectFilesPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("确认导入资料")
        self.setAcceptDrops(True)

        self._files_table = CopyableTableWidget()
        self._files_table.setColumnCount(6)
        self._files_table.setHorizontalHeaderLabels(["名称", "版本", "层级", "图片", "表格", "操作"])
        header = self._files_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._files_table.setColumnWidth(5, 220)
        self._files_table.verticalHeader().setVisible(False)
        self._files_table.setShowGrid(True)
        self._files_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._files_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._files_table.setStyleSheet(
            """
            QTableWidget {
                gridline-color: black;
                selection-background-color: rgba(0, 0, 0, 0);
                selection-color: black;
            }
            """
        )
        self._files_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
        self._files_table.setItemDelegate(PreserveCellBackgroundDelegate(self._files_table))
        self._files_table.setItemDelegateForColumn(2, LevelPathItemDelegate(self._files_table))
        self._files_table.itemChanged.connect(self._handle_file_table_item_changed)
        self._opened_doc_sessions: dict[str, dict[str, object]] = {}
        self._doc_refresh_timer = QTimer(self)
        self._doc_refresh_timer.setInterval(1000)
        self._doc_refresh_timer.timeout.connect(self._poll_opened_docs)

        layout = QVBoxLayout()
        layout.addWidget(self._files_table, 1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._sync_wizard_buttons()
        if self._state.ai_source_files_text:
            paths = self._state.ai_source_files or [
                Path(p.strip()) for p in self._state.ai_source_files_text.split(";") if p.strip()
            ]
            self._set_selected_paths(paths)
            self._update_import_reminder(paths)
        else:
            self._set_selected_paths([])
            self._update_import_reminder([])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = extract_paths_from_drop_event(event)
        paths = [p for p in paths if p.suffix.lower() == ".docx"]
        if paths:
            merged = merge_paths_text(self._serialize_paths_text(), paths)
            merged_paths = [Path(p.strip()) for p in merged.split(";") if p.strip()]
            self._set_selected_paths(merged_paths, selected_path=paths[0])
            self._update_import_reminder(merged_paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def validatePage(self) -> bool:
        raw = self._serialize_paths_text()
        if not raw:
            show_message_box(self, title="未选择文件", text="请选择待处理的资料文件。", icon=QMessageBox.Icon.Warning)
            return False
        paths = [Path(p.strip()) for p in raw.split(";") if p.strip()]
        paths = [p for p in paths if p.exists()]
        if not paths:
            show_message_box(self, title="文件不存在", text="请选择存在的资料文件。", icon=QMessageBox.Icon.Warning)
            return False
        invalid_levels = self._find_invalid_level_paths()
        if invalid_levels:
            names = "、".join(invalid_levels)
            show_message_box(
                self,
                title="层级格式无效",
                text=f"以下文件的层级不是三级数字格式：{names}\n请输入如 3.2.2 的形式。",
                icon=QMessageBox.Icon.Warning,
            )
            return False
        items = self._collect_table_items()
        level_paths = [item.level_path.strip() for item in items if item.level_path.strip()]
        if not level_paths:
            show_message_box(self, title="未填写层级", text="请在“确认导入资料”页填写层级。", icon=QMessageBox.Icon.Warning)
            return False
        self._state.ai_source_files = paths
        self._state.ai_source_files_text = raw
        self._state.ai_source_file_items = items
        self._state.ai_import_level_path = level_paths[0] if len(set(level_paths)) == 1 else ""
        if len(paths) > 1:
            capture_import_cost_before(self._state)
            split_states = self._build_split_import_states(paths=paths, items=items)
            wizard = self.wizard()
            open_split = getattr(wizard, "open_split_import_flow_windows", None)
            if not callable(open_split) or (not open_split(split_states, start_page_id=PAGE_AI_IMPORT)):
                show_message_box(
                    self,
                    title="打开窗口失败",
                    text="无法打开拆分后的导入窗口。",
                    icon=QMessageBox.Icon.Warning,
                )
                return False
            if isinstance(wizard, QWizard):
                QTimer.singleShot(0, wizard.close)
            return False
        capture_import_cost_before(self._state)
        if self._state.project_name_is_placeholder and self._state.project_dir is not None:
            rename_project(self._state, new_name=paths[0].stem)
        return True

    def nextId(self) -> int:
        return PAGE_AI_IMPORT

    def _sync_wizard_buttons(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        back_button = wizard.button(QWizard.WizardButton.BackButton)
        if back_button is not None:
            back_button.setVisible(False)
        wizard.setButtonText(QWizard.WizardButton.NextButton, "开始导题")

    def _sync_custom_wizard_button(self) -> None:
        wizard = self.wizard()
        if not isinstance(wizard, QWizard):
            return
        add_button = wizard.button(QWizard.WizardButton.CustomButton1)
        if add_button is None:
            return
        add_button.setText("添加文档")
        add_button.setVisible(True)
        try:
            add_button.clicked.disconnect()
        except TypeError:
            pass
        add_button.clicked.connect(self._choose_and_add_docs)

    def _choose_and_add_docs(self) -> None:
        start_dir = (self._state.import_source_dir_text or "").strip()
        if not start_dir:
            start_dir = os.getcwd()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择资料文档",
            start_dir,
            "Word 文档 (*.docx)",
        )
        if not files:
            return
        paths = [Path(value) for value in files if value]
        if not paths:
            return
        merged = merge_paths_text(self._serialize_paths_text(), paths)
        merged_paths = [Path(p.strip()) for p in merged.split(";") if p.strip()]
        self._state.import_source_dir_text = str(paths[0].parent)
        self._set_selected_paths(merged_paths, selected_path=paths[0])
        self._update_import_reminder(merged_paths)

    def _serialize_paths_text(self) -> str:
        parts: list[str] = []
        for i in range(self._files_table.rowCount()):
            item = self._files_table.item(i, 0)
            raw = item.data(Qt.ItemDataRole.UserRole)
            if raw:
                parts.append(str(raw))
        return "; ".join(parts)

    def _set_selected_paths(self, paths: list[Path], *, selected_path: Path | None = None) -> None:
        existing_map = {item.path: item for item in self._state.ai_source_file_items}
        self._files_table.blockSignals(True)
        self._files_table.setRowCount(0)
        selected_row = 0
        updated_items: list[AiSourceFileItem] = []
        for index, path in enumerate(paths):
            raw_path = str(path)
            existing = existing_map.get(raw_path, AiSourceFileItem(path=raw_path))
            default_version = existing.version or self._state.preferred_textbook_version
            updated_items.append(
                AiSourceFileItem(path=raw_path, version=default_version, level_path=existing.level_path)
            )
            self._files_table.insertRow(index)
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(raw_path)
            name_item.setData(Qt.ItemDataRole.UserRole, raw_path)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            version_item = QTableWidgetItem(default_version)
            version_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            level_item = QTableWidgetItem(existing.level_path)
            level_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            image_item = QTableWidgetItem("")
            image_item.setFlags(image_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            image_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            table_item = QTableWidgetItem("")
            table_item.setFlags(table_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            open_button = QPushButton("打开并编辑")
            style_dialog_button(open_button)
            remove_button = QPushButton("移除")
            style_dialog_button(remove_button)
            open_button.clicked.connect(lambda _checked=False, row_index=index: self._open_saved_doc_for_row(row_index))
            remove_button.clicked.connect(lambda _checked=False, row_index=index: self._remove_file_row(row_index))
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(6)
            action_layout.addWidget(open_button)
            action_layout.addWidget(remove_button)
            action_widget = QWidget(self._files_table)
            action_widget.setLayout(action_layout)
            self._files_table.setItem(index, 0, name_item)
            self._files_table.setItem(index, 1, version_item)
            self._files_table.setItem(index, 2, level_item)
            self._files_table.setItem(index, 3, image_item)
            self._files_table.setItem(index, 4, table_item)
            self._files_table.setCellWidget(index, 5, action_widget)
            if selected_path is not None and path == selected_path:
                selected_row = index
        self._state.ai_source_file_items = updated_items
        self._files_table.blockSignals(False)
        if self._files_table.rowCount() == 0:
            return
        self._files_table.selectRow(selected_row)

    def _handle_file_table_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        path_item = self._files_table.item(row, 0)
        if path_item is None:
            return
        raw_path = str(path_item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not raw_path:
            return
        version_item = self._files_table.item(row, 1)
        level_item = self._files_table.item(row, 2)
        version = (version_item.text() if version_item else "").strip() or self._state.preferred_textbook_version
        if version_item is not None and version_item.text().strip() != version:
            self._files_table.blockSignals(True)
            version_item.setText(version)
            self._files_table.blockSignals(False)
        level_path = (level_item.text() if level_item else "").strip()
        if item.column() == 2 and level_path and not self._is_valid_level_path(level_path):
            self._files_table.blockSignals(True)
            item.setText("")
            self._files_table.blockSignals(False)
            show_message_box(
                self,
                title="层级格式无效",
                text="层级只允许输入三级点连接的数字形式，例如 3.2.2。",
                icon=QMessageBox.Icon.Warning,
            )
            level_path = ""
        self._upsert_source_file_item(raw_path, version=version, level_path=level_path)

    def _collect_table_items(self) -> list[AiSourceFileItem]:
        items: list[AiSourceFileItem] = []
        for row in range(self._files_table.rowCount()):
            name_item = self._files_table.item(row, 0)
            if name_item is None:
                continue
            raw_path = str(name_item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not raw_path:
                continue
            version = (
                (self._files_table.item(row, 1).text() if self._files_table.item(row, 1) else "").strip()
                or self._state.preferred_textbook_version
            )
            level_path = (self._files_table.item(row, 2).text() if self._files_table.item(row, 2) else "").strip()
            items.append(AiSourceFileItem(path=raw_path, version=version, level_path=level_path))
        return items

    def _upsert_source_file_item(self, raw_path: str, *, version: str, level_path: str) -> None:
        updated = False
        for item in self._state.ai_source_file_items:
            if item.path == raw_path:
                item.version = version
                item.level_path = level_path
                updated = True
                break
        if not updated:
            self._state.ai_source_file_items.append(
                AiSourceFileItem(path=raw_path, version=version, level_path=level_path)
            )

    def _find_invalid_level_paths(self) -> list[str]:
        invalid_names: list[str] = []
        for row in range(self._files_table.rowCount()):
            name_item = self._files_table.item(row, 0)
            level_item = self._files_table.item(row, 2)
            if name_item is None or level_item is None:
                continue
            level_path = level_item.text().strip()
            if level_path and (not self._is_valid_level_path(level_path)):
                invalid_names.append(name_item.text().strip() or f"第{row + 1}行")
        return invalid_names

    def _is_valid_level_path(self, value: str) -> bool:
        return is_valid_level_path(value)

    def _build_split_import_states(
        self,
        *,
        paths: list[Path],
        items: list[AiSourceFileItem],
    ) -> list[WizardState]:
        item_map = {item.path: item for item in items if str(item.path or "").strip()}
        split_states: list[WizardState] = []
        for path in paths:
            raw_path = str(path)
            source_item = item_map.get(raw_path, AiSourceFileItem(path=raw_path, version=self._state.preferred_textbook_version))
            child_state = build_import_flow_state(
                self._state,
                source_files=[path],
                source_items=[source_item],
                import_level_path=source_item.level_path,
            )
            child_state.preferred_textbook_version = source_item.version or self._state.preferred_textbook_version
            copy_import_cost_before(self._state, child_state)
            if child_state.project_name_is_placeholder and child_state.project_dir is not None:
                rename_project(child_state, new_name=path.stem)
            split_states.append(child_state)
        return split_states

    def _update_import_reminder(self, paths: list[Path]) -> None:
        self._files_table.blockSignals(True)
        populate_import_reminder_columns(self._files_table, paths, image_col=3, table_col=4)
        self._files_table.blockSignals(False)

    def _open_saved_doc_for_row(self, row: int) -> None:
        path = self._reminder_doc_path(row)
        if path is None:
            return
        if not path.exists():
            show_message_box(self, title="文件不存在", text=f"未找到文档：{path}", icon=QMessageBox.Icon.Warning)
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            show_message_box(self, title="打开失败", text=f"无法打开 Word 文档：{e}", icon=QMessageBox.Icon.Critical)
            return
        session = build_opened_doc_session(path)
        self._opened_doc_sessions[str(path.resolve())] = session
        if not self._doc_refresh_timer.isActive():
            self._doc_refresh_timer.start()

    def _remove_file_row(self, row: int) -> None:
        if row < 0 or row >= self._files_table.rowCount():
            return
        remaining_paths: list[Path] = []
        removed_path: Path | None = None
        for row_index in range(self._files_table.rowCount()):
            item = self._files_table.item(row_index, 0)
            if item is None:
                continue
            raw = item.data(Qt.ItemDataRole.UserRole)
            if not raw:
                continue
            path = Path(str(raw))
            if row_index == row:
                removed_path = path
                continue
            remaining_paths.append(path)
        if removed_path is None:
            return
        self._opened_doc_sessions.pop(str(removed_path.resolve()), None)
        next_selected = None
        if remaining_paths:
            next_selected = remaining_paths[min(row, len(remaining_paths) - 1)]
        self._set_selected_paths(remaining_paths, selected_path=next_selected)
        self._update_import_reminder(remaining_paths)

    def _reminder_doc_path(self, row: int) -> Path | None:
        if row < 0 or row >= self._files_table.rowCount():
            return None
        item = self._files_table.item(row, 0)
        if item is None:
            return None
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            return None
        return Path(str(raw))

    def _poll_opened_docs(self) -> None:
        if not self._opened_doc_sessions:
            self._doc_refresh_timer.stop()
            return
        closed_paths = poll_opened_doc_sessions(self._opened_doc_sessions)
        if closed_paths:
            self._refresh_after_external_doc_edit(closed_paths)
        if not self._opened_doc_sessions:
            self._doc_refresh_timer.stop()

    def _refresh_after_external_doc_edit(self, changed_paths: list[Path]) -> None:
        paths = [Path(item.path) for item in self._collect_table_items()]
        self._update_import_reminder(paths)
        select_first_changed_row(self._files_table, changed_paths)


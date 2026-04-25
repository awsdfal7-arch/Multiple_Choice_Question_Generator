from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import shutil
from uuid import uuid4

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMenuBar,
    QMessageBox,
    QStackedLayout,
    QSplitter,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from sj_generator.application.settings import (
    load_welcome_table_font_point_size,
    load_welcome_table_column_visibility,
    load_welcome_tree_expanded_prefixes,
    save_welcome_table_font_point_size,
    save_welcome_table_column_visibility,
    save_welcome_tree_expanded_prefixes,
)
from sj_generator.infrastructure.persistence.excel_repo import load_db_question_records
from sj_generator.infrastructure.persistence.sqlite_repo import (
    append_questions,
    DbQuestionRecord,
    count_questions_by_level_prefix,
    delete_question_by_id,
    delete_questions_by_level_prefix,
    list_level_paths,
    load_all_questions,
    load_questions_by_level_path,
    update_question,
)
from sj_generator.domain.entities import Question
from sj_generator.shared.paths import app_paths
from sj_generator.ui.api_config_dialog import ApiConfigDialog
from sj_generator.ui.message_box import show_message_box
from sj_generator.ui.question_edit_dialog import QuestionEditDialog
from sj_generator.ui.table_copy import CopyableTableWidget
from sj_generator.ui.welcome_export import (
    default_export_dir,
    display_export_level_name,
    export_current_level_to_markdown,
    export_current_level_to_pdf,
    export_db_records_to_xlsx,
    sanitize_export_name,
)
from sj_generator.ui.welcome_table import (
    populate_db_records_table,
    populate_questions_table,
)
from sj_generator.ui.welcome_tree import (
    build_level_tree,
    collect_expanded_level_prefixes,
    delete_action_text,
    delete_scope_text,
    expand_item_ancestors,
    load_questions_for_tree_level,
    tree_level_key_for_path,
)
from sj_generator.ui.program_settings_dialog import (
    ProgramSettingsDialog,
    SECTION_EXPORT,
    SECTION_GENERAL,
    SECTION_IMPORT,
)
from sj_generator.application.state import (
    WizardState,
    build_import_flow_state,
    library_db_path_from_repo_parent_dir_text,
)
from sj_generator.ui.styles import rounded_panel_stylesheet
from sj_generator.ui.constants import PAGE_AI_SELECT

LAST_COLUMN_MIN_WIDTH = 140
TABLE_FONT_POINT_SIZE_MIN = 8
TABLE_FONT_POINT_SIZE_MAX = 28
TREE_ROLE_LEVEL_PATH = Qt.ItemDataRole.UserRole
TREE_ROLE_LEVEL_PREFIX = int(Qt.ItemDataRole.UserRole) + 1
TREE_ROLE_LEVEL_DEPTH = int(Qt.ItemDataRole.UserRole) + 2
TABLE_COLUMNS = [
    ("stem", "题目", True),
    ("options", "选项", True),
    ("answer", "答案", True),
    ("analysis", "解析", True),
    ("id", "题目id", False),
    ("question_type", "类型", False),
    ("choice_1", "组合A", False),
    ("choice_2", "组合B", False),
    ("choice_3", "组合C", False),
    ("choice_4", "组合D", False),
    ("level_path", "所属层级", False),
    ("textbook_version", "教材版本", False),
    ("source", "来源", False),
    ("difficulty_score", "难度评级", False),
    ("knowledge_points", "考察知识点", False),
    ("abilities", "考察能力", False),
    ("created_at", "入库时间", False),
    ("updated_at", "最后修改时间", False),
]


class _ZoomableTableWidget(CopyableTableWidget):
    def __init__(self, *args, zoom_callback=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._zoom_callback = zoom_callback

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self._zoom_callback is not None:
                delta = event.angleDelta().y()
                if delta != 0:
                    self._zoom_callback(1 if delta > 0 else -1)
                    event.accept()
                    return
        super().wheelEvent(event)


class WelcomePage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self._db_path = self._current_db_path()
        self._import_flow_windows: list[QWizard] = []
        self._current_db_records: list[DbQuestionRecord] = []
        self._row_resize_pending = False
        self._row_resize_followup_pending = False
        self._column_defs = TABLE_COLUMNS
        self._column_visibility = self._load_column_visibility()
        self._table_font_point_size = self._load_table_font_point_size()
        loaded_expanded_prefixes = load_welcome_tree_expanded_prefixes()
        self._expanded_level_prefixes = set(loaded_expanded_prefixes or [])
        self._has_saved_tree_state = loaded_expanded_prefixes is not None
        self._syncing_tree_expand_state = False
        self.setTitle("开始")

        menu_bar = QMenuBar(self)
        file_menu = menu_bar.addMenu("文件")
        import_menu = file_menu.addMenu("导入")
        self._doc_import_action = import_menu.addAction("从文档文件解析导入")
        self._doc_import_action.triggered.connect(self._enter_main_flow)
        self._table_import_action = import_menu.addAction("从表格文件直接导入")
        self._table_import_action.triggered.connect(self._import_from_table_file)
        export_menu = file_menu.addMenu("导出")
        self._export_md_action = export_menu.addAction("导出当前页面为 Markdown")
        self._export_md_action.triggered.connect(self._export_current_level_to_markdown)
        self._export_pdf_action = export_menu.addAction("导出当前页面为 PDF")
        self._export_pdf_action.triggered.connect(self._export_current_level_to_pdf)
        export_xlsx_menu = export_menu.addMenu("导出为 xlsx")
        self._export_current_level_xlsx_action = export_xlsx_menu.addAction("当前页面题目")
        self._export_current_level_xlsx_action.triggered.connect(self._export_current_level_to_xlsx)
        self._export_db_table_xlsx_action = export_xlsx_menu.addAction("所有题目")
        self._export_db_table_xlsx_action.triggered.connect(self._export_db_table_to_xlsx)
        edit_menu = menu_bar.addMenu("编辑")
        self._add_question_action = edit_menu.addAction("新增题目")
        self._add_question_action.triggered.connect(self._add_question_manually)
        view_menu = menu_bar.addMenu("视图")
        column_menu = view_menu.addMenu("表格列显示")
        settings_menu = menu_bar.addMenu("设置")
        self._general_settings_action = settings_menu.addAction("常规设定")
        self._general_settings_action.triggered.connect(self._open_general_settings)
        self._import_settings_action = settings_menu.addAction("导入设定")
        self._import_settings_action.triggered.connect(self._open_import_settings)
        self._export_settings_action = settings_menu.addAction("导出设定")
        self._export_settings_action.triggered.connect(self._open_export_settings)
        self._api_settings_action = settings_menu.addAction("API 配置")
        self._api_settings_action.triggered.connect(self._open_api_cfg)

        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.setAlternatingRowColors(True)
        self._folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._folder_tree.currentItemChanged.connect(self._on_level_selection_changed)
        self._folder_tree.customContextMenuRequested.connect(self._show_folder_tree_context_menu)
        self._folder_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self._folder_tree.itemCollapsed.connect(self._on_tree_item_collapsed)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._folder_tree, 1)
        left_panel = QWidget()
        left_panel.setStyleSheet(rounded_panel_stylesheet(background="#ffffff"))
        left_panel.setLayout(left_layout)

        self._table = _ZoomableTableWidget(0, len(self._column_defs), zoom_callback=self._adjust_table_font_size)
        self._table.setHorizontalHeaderLabels([title for _key, title, _visible in self._column_defs])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setWordWrap(True)
        self._table.setStyleSheet(
            "QTableWidget {"
            "border: 1px solid #000000; border-radius: 0px; background: #ffffff; gridline-color: #000000; outline: none;"
            "}"
            "QTableWidget > QWidget {"
            "background: #ffffff; border: none;"
            "}"
            "QHeaderView {"
            "background: transparent; border: none;"
            "}"
            "QHeaderView::section {"
            "background: #ffffff; border: none; border-right: 1px solid #000000; border-bottom: 1px solid #000000;"
            "padding: 4px 6px; font-weight: 600;"
            "}"
        )
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.horizontalHeader().sectionResized.connect(self._schedule_table_row_resize)
        self._table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        self._column_actions: dict[int, object] = {}
        for idx, (key, title, _visible) in enumerate(self._column_defs):
            visible = self._column_visibility.get(key, False)
            action = column_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(visible)
            action.toggled.connect(lambda checked, col=idx: self._set_column_visible(col, checked))
            self._column_actions[idx] = action
            self._table.setColumnHidden(idx, not visible)
        self._rebalance_visible_columns()

        self._table_placeholder = QLabel("请先从左侧层级中选择，以显示题目信息")
        self._table_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table_placeholder.setWordWrap(True)
        self._apply_table_font_size()

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._table_stack = QStackedLayout()
        self._table_stack.addWidget(self._table_placeholder)
        self._table_stack.addWidget(self._table)
        right_layout.addLayout(self._table_stack, 1)
        right_panel = QWidget()
        right_panel.setStyleSheet(rounded_panel_stylesheet(background="#ffffff"))
        right_panel.setLayout(right_layout)

        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(left_panel)
        self._content_splitter.addWidget(right_panel)
        self._content_splitter.setChildrenCollapsible(False)
        self._content_splitter.setHandleWidth(6)
        self._content_splitter.setStyleSheet("QSplitter::handle { background-color: transparent; }")
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 4)
        self._content_splitter.setSizes([180, 720])
        self._content_splitter.splitterMoved.connect(self._schedule_table_row_resize)

        layout = QVBoxLayout()
        layout.setMenuBar(menu_bar)
        layout.addWidget(self._content_splitter, 1)
        self.setLayout(layout)
        self._set_table_placeholder_visible(True)

    def initializePage(self) -> None:
        self._db_path = self._current_db_path()
        self._refresh_level_tree()
        if self._folder_tree.currentItem() is None:
            self._current_db_records = []
            self._table.setRowCount(0)
            self._schedule_table_row_resize()
            self._set_table_placeholder_visible(True)

    def _enter_main_flow(self) -> None:
        self._state.start_mode = "wizard"
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择资料文件",
            self._effective_import_source_dir(),
            "Word (*.docx);;All Files (*)",
        )
        if not files:
            return
        self._open_import_flow_windows([Path(p) for p in files], message_parent=self)

    def _open_import_flow_windows(self, paths: list[Path], *, message_parent: QWidget | None = None) -> bool:
        selected_paths = [Path(p) for p in paths if str(p).strip()]
        if not selected_paths:
            return False
        self._state.import_source_dir_text = str(selected_paths[0].parent)
        try:
            copied_paths = self._backup_import_source_files(selected_paths)
        except Exception as e:
            show_message_box(
                message_parent or self,
                title="备份失败",
                text=f"复制资料到 doc 目录失败：{e}",
                icon=QMessageBox.Icon.Critical,
            )
            return False
        if not copied_paths:
            return False
        shared_state = build_import_flow_state(self._state, source_files=copied_paths)
        return self._open_import_flow_states([shared_state], start_page_id=PAGE_AI_SELECT)

    def _open_import_flow_states(self, states: list[WizardState], *, start_page_id: int) -> bool:
        window_states = [state for state in states if isinstance(state, WizardState)]
        if not window_states:
            return False

        from sj_generator.presentation.qt.import_flow import ImportFlowWizard

        opened_windows: list[QWizard] = []
        owner = self.window()
        for window_state in window_states:
            dlg = ImportFlowWizard(window_state, owner, launcher=self, start_page_id=start_page_id)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dlg.finished.connect(
                lambda _result, state=window_state: self._handle_import_flow_window_finished(state)
            )
            dlg.destroyed.connect(lambda _obj=None, wizard=dlg: self._forget_import_flow_window(wizard))
            self._import_flow_windows.append(dlg)
            opened_windows.append(dlg)
        for index, dlg in enumerate(opened_windows):
            should_activate = index == (len(opened_windows) - 1)
            QTimer.singleShot(
                0,
                lambda wizard=dlg, activate=should_activate: self._show_import_flow_window(
                    wizard,
                    activate=activate,
                ),
            )
        return True

    def _handle_import_flow_window_finished(self, state: WizardState) -> None:
        if not state.db_import_completed:
            return
        self._state.last_export_dir = state.last_export_dir
        self._refresh_level_tree(preferred_level_path=state.ai_import_level_path)

    def _open_split_import_flow_windows(self, states: list[WizardState], *, start_page_id: int) -> bool:
        return self._open_import_flow_states(states, start_page_id=start_page_id)

    def _forget_import_flow_window(self, wizard: QWizard) -> None:
        self._import_flow_windows = [item for item in self._import_flow_windows if item is not wizard]

    def _show_import_flow_window(self, wizard: QWizard, *, activate: bool) -> None:
        if wizard not in self._import_flow_windows:
            return
        wizard.show()
        if activate:
            wizard.raise_()
            wizard.activateWindow()

    def _effective_import_source_dir(self) -> str:
        configured = Path(self._state.import_source_dir_text).expanduser()
        if configured.exists() and configured.is_dir():
            return str(configured)
        return str(Path.home() / "Downloads")

    def _backup_import_source_files(self, paths: list[Path]) -> list[Path]:
        if not paths:
            return []
        target_dir = self._db_path.parent / "doc"
        target_dir.mkdir(parents=True, exist_ok=True)
        known_hash_paths = self._build_doc_hash_index(target_dir)
        copied_paths: list[Path] = []
        seen_targets: set[Path] = set()
        for src in paths:
            if not src.exists() or not src.is_file():
                raise FileNotFoundError(src)
            file_hash = self._file_sha256(src)
            target_path = known_hash_paths.get(file_hash)
            if target_path is None:
                target_path = self._next_doc_backup_path(target_dir, src.name)
                shutil.copy2(src, target_path)
                known_hash_paths[file_hash] = target_path
            resolved_target = target_path.resolve()
            if resolved_target in seen_targets:
                continue
            seen_targets.add(resolved_target)
            copied_paths.append(target_path)
        return copied_paths

    def _build_doc_hash_index(self, target_dir: Path) -> dict[str, Path]:
        hash_index: dict[str, Path] = {}
        for path in sorted(target_dir.glob("*.docx"), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            try:
                file_hash = self._file_sha256(path)
            except Exception:
                continue
            hash_index.setdefault(file_hash, path)
        return hash_index

    def _file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _next_doc_backup_path(self, target_dir: Path, file_name: str) -> Path:
        candidate = target_dir / file_name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while True:
            numbered = target_dir / f"{stem}_{index}{suffix}"
            if not numbered.exists():
                return numbered
            index += 1

    def _open_api_cfg(self) -> None:
        dlg = ApiConfigDialog(self, state=self._state)
        dlg.exec()

    def _open_program_settings(self) -> None:
        dlg = ProgramSettingsDialog(self._state, self, section=SECTION_GENERAL)
        if dlg.exec():
            self._db_path = self._current_db_path()
            self._refresh_level_tree()

    def _open_general_settings(self) -> None:
        self._open_program_settings()

    def _open_import_settings(self) -> None:
        dlg = ProgramSettingsDialog(self._state, self, section=SECTION_IMPORT)
        dlg.exec()

    def _open_export_settings(self) -> None:
        dlg = ProgramSettingsDialog(self._state, self, section=SECTION_EXPORT)
        dlg.exec()

    def _import_from_table_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "选择表格文件", "", "Excel (*.xlsx);;All Files (*)")
        if not file_path:
            return
        path = Path(file_path)
        try:
            records = load_db_question_records(path)
        except Exception as e:
            show_message_box(self, title="导入失败", text=f"读取表格文件失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        if not records:
            show_message_box(self, title="无法导入", text="当前 xlsx 中没有可写入数据库的记录。", icon=QMessageBox.Icon.Warning)
            return
        try:
            append_questions(self._db_path, records)
        except Exception as e:
            show_message_box(self, title="导入失败", text=f"写入数据库失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        preferred_level_path = next((record.level_path for record in records if record.level_path.strip()), "")
        self._refresh_level_tree(preferred_level_path=preferred_level_path)
        show_message_box(self, title="导入完成", text=f"已从数据库字段表 xlsx 导入 {len(records)} 道题。", icon=QMessageBox.Icon.Information)

    def _export_current_level_to_markdown(self) -> None:
        if not self._current_db_records:
            show_message_box(self, title="无法导出", text="当前页面没有可导出的题目。", icon=QMessageBox.Icon.Warning)
            return

        current = self._folder_tree.currentItem()
        if current is None:
            show_message_box(self, title="无法导出", text="请先选择要导出的层级。", icon=QMessageBox.Icon.Warning)
            return

        level_path = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
        if not level_path:
            show_message_box(self, title="无法导出", text="当前页面无效，无法导出。", icon=QMessageBox.Icon.Warning)
            return

        safe_level_name = sanitize_export_name(display_export_level_name(level_path))
        suggested = str(self._db_path.parent / f"{safe_level_name}.md")
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", suggested, "Markdown (*.md)")
        if not file_path:
            return

        md_path = Path(file_path)
        try:
            export_current_level_to_markdown(
                records=list(self._current_db_records),
                level_path=level_path,
                target_path=md_path,
                convertible_multi_mode=self._state.export_convertible_multi_mode,
                include_answers=self._state.export_include_answers,
                include_analysis=self._state.export_include_analysis,
            )
        except Exception as e:
            show_message_box(self, title="导出失败", text=f"写入 Markdown 失败：{e}", icon=QMessageBox.Icon.Critical)
            return

        self._state.last_export_dir = md_path.parent
        show_message_box(self, title="导出完成", text=f"已导出 Markdown：\n{md_path}", icon=QMessageBox.Icon.Information)

    def _export_current_level_to_pdf(self) -> None:
        if not self._current_db_records:
            show_message_box(self, title="无法导出", text="当前页面没有可导出的题目。", icon=QMessageBox.Icon.Warning)
            return

        current = self._folder_tree.currentItem()
        if current is None:
            show_message_box(self, title="无法导出", text="请先选择要导出的层级。", icon=QMessageBox.Icon.Warning)
            return

        level_path = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
        if not level_path:
            show_message_box(self, title="无法导出", text="当前页面无效，无法导出。", icon=QMessageBox.Icon.Warning)
            return

        safe_level_name = sanitize_export_name(display_export_level_name(level_path))
        suggested = str(default_export_dir(self._state.last_export_dir, self._db_path) / f"{safe_level_name}.pdf")
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", suggested, "PDF (*.pdf)")
        if not file_path:
            return

        target_path = Path(file_path)
        try:
            export_current_level_to_pdf(
                records=list(self._current_db_records),
                level_path=level_path,
                target_path=target_path,
                convertible_multi_mode=self._state.export_convertible_multi_mode,
                include_answers=self._state.export_include_answers,
                include_analysis=self._state.export_include_analysis,
            )
        except Exception as e:
            show_message_box(self, title="导出失败", text=f"写入 PDF 失败：{e}", icon=QMessageBox.Icon.Critical)
            return

        self._state.last_export_dir = target_path.parent
        show_message_box(self, title="导出完成", text=f"已导出 PDF：\n{target_path}", icon=QMessageBox.Icon.Information)

    def _export_current_level_to_xlsx(self) -> None:
        if not self._current_db_records:
            show_message_box(self, title="无法导出", text="当前页面没有可导出的题目。", icon=QMessageBox.Icon.Warning)
            return
        current = self._folder_tree.currentItem()
        if current is None:
            show_message_box(self, title="无法导出", text="请先选择要导出的层级。", icon=QMessageBox.Icon.Warning)
            return
        level_path = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
        if not level_path:
            show_message_box(self, title="无法导出", text="当前页面无效，无法导出。", icon=QMessageBox.Icon.Warning)
            return
        safe_level_name = sanitize_export_name(display_export_level_name(level_path))
        suggested = str(default_export_dir(self._state.last_export_dir, self._db_path) / f"{safe_level_name}.xlsx")
        file_path, _ = QFileDialog.getSaveFileName(self, "导出当前页面 xlsx", suggested, "Excel (*.xlsx)")
        if not file_path:
            return
        target_path = Path(file_path)
        try:
            export_db_records_to_xlsx(records=list(self._current_db_records), target_path=target_path)
        except Exception as e:
            show_message_box(self, title="导出失败", text=f"写入 xlsx 失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        self._state.last_export_dir = target_path.parent
        show_message_box(self, title="导出完成", text=f"已导出当前页面 xlsx：\n{target_path}", icon=QMessageBox.Icon.Information)

    def _export_db_table_to_xlsx(self) -> None:
        if not self._db_path.exists():
            show_message_box(self, title="无法导出", text="当前数据库文件不存在。", icon=QMessageBox.Icon.Warning)
            return
        records = load_all_questions(self._db_path)
        if not records:
            show_message_box(self, title="无法导出", text="当前数据库表没有可导出的题目。", icon=QMessageBox.Icon.Warning)
            return
        suggested_name = f"{sanitize_export_name(self._db_path.stem)}_整体数据库表.xlsx"
        suggested = str(default_export_dir(self._state.last_export_dir, self._db_path) / suggested_name)
        file_path, _ = QFileDialog.getSaveFileName(self, "导出整体数据库表 xlsx", suggested, "Excel (*.xlsx)")
        if not file_path:
            return
        target_path = Path(file_path)
        try:
            export_db_records_to_xlsx(records=records, target_path=target_path)
        except Exception as e:
            show_message_box(self, title="导出失败", text=f"写入 xlsx 失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        self._state.last_export_dir = target_path.parent
        show_message_box(self, title="导出完成", text=f"已导出整体数据库表 xlsx：\n{target_path}", icon=QMessageBox.Icon.Information)

    def _selected_tree_level_path_for_create(self) -> str:
        current = self._folder_tree.currentItem()
        if current is None:
            return ""
        exact_level_path = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
        if exact_level_path:
            return exact_level_path
        return str(current.data(0, TREE_ROLE_LEVEL_PREFIX) or "").strip()

    def _add_question_manually(self, *, default_level_path: str | None = None) -> None:
        if default_level_path is None:
            default_level_path = self._selected_tree_level_path_for_create()
        default_level_path = str(default_level_path or "").strip()
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_record = DbQuestionRecord(
            id=str(uuid4()),
            stem="",
            option_1="",
            option_2="",
            option_3="",
            option_4="",
            choice_1="",
            choice_2="",
            choice_3="",
            choice_4="",
            answer="",
            analysis="",
            question_type="单选",
            textbook_version=self._state.preferred_textbook_version,
            source="录入",
            level_path=default_level_path,
            difficulty_score=None,
            knowledge_points="",
            abilities="",
            created_at=now_text,
            updated_at=now_text,
        )
        dlg = QuestionEditDialog(new_record, self, create_mode=True)
        if dlg.exec() != QuestionEditDialog.DialogCode.Accepted:
            return
        created = dlg.updated_record()
        if not created.stem.strip():
            show_message_box(self, title="无法新增", text="题目内容不能为空。", icon=QMessageBox.Icon.Warning)
            return
        try:
            append_questions(self._db_path, [created])
        except Exception as e:
            show_message_box(self, title="新增失败", text=f"写入题目失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        self._refresh_level_tree(preferred_level_path=created.level_path)
        show_message_box(self, title="新增完成", text="题目已新增。", icon=QMessageBox.Icon.Information)

    def _refresh_level_tree(self, preferred_level_path: str | None = None) -> None:
        current_item = self._folder_tree.currentItem()
        current_prefix = ""
        if current_item is not None:
            current_prefix = str(current_item.data(0, TREE_ROLE_LEVEL_PREFIX) or "").strip()
        if self._folder_tree.topLevelItemCount() > 0:
            self._expanded_level_prefixes = collect_expanded_level_prefixes(
                self._folder_tree,
                TREE_ROLE_LEVEL_PREFIX,
            )
        self._folder_tree.clear()
        if not self._db_path.exists():
            return

        self._syncing_tree_expand_state = True
        preferred_item, selected_prefix_item = build_level_tree(
            tree_widget=self._folder_tree,
            level_paths=list_level_paths(self._db_path),
            expanded_prefixes=self._expanded_level_prefixes,
            current_prefix=current_prefix,
            preferred_level_key=tree_level_key_for_path(preferred_level_path),
            role_level_prefix=TREE_ROLE_LEVEL_PREFIX,
            role_level_depth=TREE_ROLE_LEVEL_DEPTH,
            role_level_path=TREE_ROLE_LEVEL_PATH,
        )

        self._syncing_tree_expand_state = False
        selected_item = preferred_item or selected_prefix_item
        if selected_item is not None:
            self._folder_tree.setCurrentItem(selected_item)
            if preferred_item is not None:
                expand_item_ancestors(selected_item)
        else:
            self._folder_tree.setCurrentItem(None)
            self._folder_tree.clearSelection()
            self._table.setRowCount(0)
            self._current_db_records = []
            self._schedule_table_row_resize()
            self._set_table_placeholder_visible(True)
        self._sync_expanded_level_prefixes_from_tree()

    def _on_level_selection_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            self._current_db_records = []
            self._table.setRowCount(0)
            self._set_table_placeholder_visible(True)
            return
        level_key = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
        if not level_key:
            self._current_db_records = []
            self._table.setRowCount(0)
            self._set_table_placeholder_visible(True)
            return
        records = load_questions_for_tree_level(self._db_path, level_key)
        self._current_db_records = records
        self._populate_db_records_table(records)

    def _show_folder_tree_context_menu(self, pos) -> None:
        item = self._folder_tree.itemAt(pos)
        if item is None:
            return
        level_prefix = str(item.data(0, TREE_ROLE_LEVEL_PREFIX) or "").strip()
        if not level_prefix:
            return
        depth = int(item.data(0, TREE_ROLE_LEVEL_DEPTH) or 0)
        self._folder_tree.setCurrentItem(item)
        menu = QMenu(self)
        add_action = menu.addAction("新增题目")
        delete_action = menu.addAction(delete_action_text(depth))
        chosen = menu.exec(self._folder_tree.viewport().mapToGlobal(pos))
        if chosen == add_action:
            self._add_question_manually(default_level_path=str(item.data(0, TREE_ROLE_LEVEL_PATH) or item.data(0, TREE_ROLE_LEVEL_PREFIX) or "").strip())
            return
        if chosen == delete_action:
            self._delete_level_subtree(
                level_prefix=level_prefix,
                depth=depth,
                display_label=item.text(0).strip() or level_prefix,
            )

    def _on_tree_item_expanded(self, _item: QTreeWidgetItem) -> None:
        if self._syncing_tree_expand_state:
            return
        self._sync_expanded_level_prefixes_from_tree()

    def _on_tree_item_collapsed(self, _item: QTreeWidgetItem) -> None:
        if self._syncing_tree_expand_state:
            return
        self._sync_expanded_level_prefixes_from_tree()

    def _delete_level_subtree(self, *, level_prefix: str, depth: int, display_label: str) -> None:
        level_prefix = (level_prefix or "").strip()
        if not level_prefix:
            return
        total = count_questions_by_level_prefix(self._db_path, level_prefix)
        if total <= 0:
            show_message_box(self, title="无需删除", text="当前节点下没有可删除的题目。", icon=QMessageBox.Icon.Information)
            self._refresh_level_tree()
            return
        scope_text = delete_scope_text(depth)
        answer = show_message_box(
            self,
            title="确认删除",
            text=f"确定删除{scope_text}{display_label}（层级 {level_prefix}）下的 {total} 道题吗？此操作不可撤销。",
            icon=QMessageBox.Icon.Warning,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default_button=QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted_count = delete_questions_by_level_prefix(self._db_path, level_prefix)
        self._current_db_records = []
        self._table.setRowCount(0)
        self._schedule_table_row_resize()
        self._refresh_level_tree()
        show_message_box(
            self,
            title="删除完成",
            text=f"已删除{scope_text}{display_label}下的 {deleted_count} 道题。",
            icon=QMessageBox.Icon.Information,
        )

    def _sync_expanded_level_prefixes_from_tree(self) -> None:
        self._expanded_level_prefixes = collect_expanded_level_prefixes(
            self._folder_tree,
            TREE_ROLE_LEVEL_PREFIX,
        )
        self._has_saved_tree_state = True
        save_welcome_tree_expanded_prefixes(sorted(self._expanded_level_prefixes))

    def _populate_questions_table(self, questions: list[Question]) -> None:
        self._current_db_records = []
        populate_questions_table(self._table, questions, self._column_defs)
        self._schedule_table_row_resize()
        self._set_table_placeholder_visible(False)

    def _populate_db_records_table(self, records: list[DbQuestionRecord]) -> None:
        populate_db_records_table(self._table, records, self._column_defs)
        self._schedule_table_row_resize()
        self._set_table_placeholder_visible(False)

    def _set_table_placeholder_visible(self, visible: bool) -> None:
        self._table_stack.setCurrentWidget(self._table_placeholder if visible else self._table)

    def _current_db_path(self) -> Path:
        return library_db_path_from_repo_parent_dir_text(self._state.default_repo_parent_dir_text)

    def _set_column_visible(self, column: int, visible: bool) -> None:
        if visible:
            self._table.setColumnHidden(column, False)
            self._save_column_visibility()
            self._refresh_table_layout_after_column_change()
            return

        visible_columns = [idx for idx in range(self._table.columnCount()) if not self._table.isColumnHidden(idx)]
        if len(visible_columns) <= 1:
            action = self._column_actions.get(column)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(True)
                action.blockSignals(False)
            return

        self._table.setColumnHidden(column, True)
        self._save_column_visibility()
        self._refresh_table_layout_after_column_change()

    def _schedule_table_row_resize(self, *_args) -> None:
        if self._row_resize_pending:
            return
        self._row_resize_pending = True
        QTimer.singleShot(0, self._apply_table_row_resize)

    def _apply_table_row_resize(self) -> None:
        self._row_resize_pending = False
        if self._table.rowCount() == 0:
            return
        self._table.doItemsLayout()
        self._table.resizeRowsToContents()
        if not self._row_resize_followup_pending:
            self._row_resize_followup_pending = True
            QTimer.singleShot(30, self._apply_table_row_resize_followup)

    def _apply_table_row_resize_followup(self) -> None:
        self._row_resize_followup_pending = False
        if self._table.rowCount() == 0:
            return
        self._table.doItemsLayout()
        self._table.viewport().update()
        self._table.resizeRowsToContents()

    def _refresh_table_layout_after_column_change(self) -> None:
        self._rebalance_visible_columns()
        self._table.doItemsLayout()
        self._table.viewport().update()
        self._schedule_table_row_resize()

    def _rebalance_visible_columns(self) -> None:
        visible_columns = [idx for idx in range(self._table.columnCount()) if not self._table.isColumnHidden(idx)]
        if not visible_columns:
            return
        available_width = max(1, self._table.viewport().width())
        if len(visible_columns) == 1:
            self._table.setColumnWidth(visible_columns[0], available_width)
            return
        fixed_columns = visible_columns[:-1]
        reserved_last_width = min(available_width, LAST_COLUMN_MIN_WIDTH)
        distributable_width = max(len(fixed_columns), available_width - reserved_last_width)
        base_width = max(1, distributable_width // len(fixed_columns))
        consumed_width = 0
        for column in fixed_columns:
            self._table.setColumnWidth(column, base_width)
            consumed_width += base_width
        self._table.setColumnWidth(visible_columns[-1], max(1, available_width - consumed_width))

    def _load_column_visibility(self) -> dict[str, bool]:
        saved = load_welcome_table_column_visibility()
        defaults = {key: visible for key, _title, visible in self._column_defs}
        merged = defaults | {key: value for key, value in saved.items() if key in defaults}
        if not any(merged.values()):
            first_key = self._column_defs[0][0]
            merged[first_key] = True
        return merged

    def _save_column_visibility(self) -> None:
        visibility = {
            key: (not self._table.isColumnHidden(idx))
            for idx, (key, _title, _visible) in enumerate(self._column_defs)
        }
        save_welcome_table_column_visibility(visibility)

    def _load_table_font_point_size(self) -> int:
        saved = load_welcome_table_font_point_size()
        default_size = self.font().pointSize()
        if default_size <= 0:
            default_size = 10
        if saved is None:
            return default_size
        return max(TABLE_FONT_POINT_SIZE_MIN, min(TABLE_FONT_POINT_SIZE_MAX, saved))

    def _apply_table_font_size(self) -> None:
        font = self._table.font()
        font.setPointSize(self._table_font_point_size)
        self._table.setFont(font)
        self._table.horizontalHeader().setFont(font)
        self._table_placeholder.setFont(font)
        self._table.doItemsLayout()
        self._table.viewport().update()
        self._table.horizontalHeader().viewport().update()
        self._schedule_table_row_resize()

    def _adjust_table_font_size(self, step: int) -> None:
        new_size = max(
            TABLE_FONT_POINT_SIZE_MIN,
            min(TABLE_FONT_POINT_SIZE_MAX, self._table_font_point_size + int(step)),
        )
        if new_size == self._table_font_point_size:
            return
        self._table_font_point_size = new_size
        self._apply_table_font_size()
        save_welcome_table_font_point_size(self._table_font_point_size)

    def _on_table_cell_double_clicked(self, row: int, _column: int) -> None:
        if row < 0 or row >= len(self._current_db_records):
            return
        record = self._current_db_records[row]
        dlg = QuestionEditDialog(record, self)
        result = dlg.exec()
        if result == QuestionEditDialog.DELETE_RESULT:
            deleted_count = delete_question_by_id(self._db_path, record.id)
            if deleted_count <= 0:
                show_message_box(self, title="删除失败", text="未找到要删除的题目，可能已被移除。", icon=QMessageBox.Icon.Warning)
                self._refresh_level_tree()
                return
            self._current_db_records = []
            self._table.setRowCount(0)
            self._schedule_table_row_resize()
            current = self._folder_tree.currentItem()
            preferred_level_path = ""
            if current is not None:
                preferred_level_path = str(current.data(0, TREE_ROLE_LEVEL_PATH) or "").strip()
            self._refresh_level_tree(preferred_level_path=preferred_level_path)
            show_message_box(self, title="删除完成", text="题目已删除。", icon=QMessageBox.Icon.Information)
            return
        if result != QuestionEditDialog.DialogCode.Accepted:
            return
        updated = dlg.updated_record()
        try:
            update_question(self._db_path, updated)
        except Exception as e:
            show_message_box(self, title="保存失败", text=f"更新题目失败：{e}", icon=QMessageBox.Icon.Critical)
            return
        self._current_db_records[row] = updated
        self._refresh_level_tree(preferred_level_path=updated.level_path)

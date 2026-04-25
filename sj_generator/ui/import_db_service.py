from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QWidget

from sj_generator.infrastructure.persistence.draft_db_import import import_draft_questions_to_db
from sj_generator.ui.import_costs import freeze_import_cost_result
from sj_generator.ui.message_box import show_message_box
from sj_generator.application.state import WizardState, library_db_path_from_repo_parent_dir_text


def commit_draft_questions_to_db(parent: QWidget | None, state: WizardState) -> bool:
    if state.db_import_completed:
        return True
    freeze_import_cost_result(state)

    questions = list(state.draft_questions)
    if not questions:
        show_message_box(parent, title="无法导入数据库", text="当前草稿为空，无法写入数据库。", icon=QMessageBox.Icon.Warning)
        return False

    level_path = state.ai_import_level_path.strip()
    if not level_path:
        show_message_box(parent, title="无法导入数据库", text="未填写层级归属，无法写入数据库。", icon=QMessageBox.Icon.Warning)
        return False

    try:
        count = import_draft_questions_to_db(
            db_path=library_db_path_from_repo_parent_dir_text(state.default_repo_parent_dir_text),
            questions=questions,
            level_path=level_path,
            source_files=state.ai_source_files or [],
            textbook_version=state.preferred_textbook_version,
        )
    except Exception as e:
        state.db_import_error = str(e)
        show_message_box(parent, title="导入数据库失败", text=str(e), icon=QMessageBox.Icon.Critical)
        return False

    state.mark_db_import_completed(count)
    return True

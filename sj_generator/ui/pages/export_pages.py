from datetime import date
from pathlib import Path
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from sj_generator.io.excel_repo import save_questions
from sj_generator.io.export_md import export_questions_to_markdown
from sj_generator.ui.state import WizardState


class ExportPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("导出")
        self.setFinalPage(True)

        self._md_path_edit = QLineEdit()
        self._md_path_edit.setPlaceholderText("选择导出 Markdown 路径（.md）")
        md_browse = QPushButton("另存为…")
        md_browse.clicked.connect(self._browse_md)

        self._exported = False

        grid = QGridLayout()
        grid.addWidget(QLabel("Markdown："), 0, 0)
        grid.addWidget(self._md_path_edit, 0, 1)
        grid.addWidget(md_browse, 0, 2)

        layout = QVBoxLayout()
        layout.addLayout(grid)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._exported = False
        w = self.wizard()
        if isinstance(w, QWizard):
            w.setButtonText(QWizard.WizardButton.NextButton, "导出并打开文件夹")
            w.setButtonText(QWizard.WizardButton.FinishButton, "导出并打开文件夹")
        project_dir = self._state.project_dir
        if project_dir is None:
            return
        base_name = project_dir.name
        self._md_path_edit.setText(str(project_dir / f"{base_name}.md"))

    def cleanupPage(self) -> None:
        w = self.wizard()
        if isinstance(w, QWizard):
            w.setButtonText(QWizard.WizardButton.NextButton, "下一步")

    def nextId(self) -> int:
        return -1

    def validatePage(self) -> bool:
        if self._exported:
            return True
        ok = self._export()
        self._exported = ok
        return ok

    def _browse_md(self) -> None:
        project_dir = self._state.project_dir
        suggested = ""
        if project_dir is not None:
            suggested = str(project_dir / f"{project_dir.name}.md")
        path, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", suggested, "Markdown (*.md)")
        if path:
            self._md_path_edit.setText(path)

    def _export(self) -> bool:
        repo = self._state.repo_path
        project_dir = self._state.project_dir
        if repo is None or project_dir is None:
            QMessageBox.warning(self, "未选择题库", "请先选择题库。")
            return False
        questions = list(self._state.draft_questions)
        if not questions:
            QMessageBox.warning(self, "没有可导出内容", "当前题库草稿为空，请先录入题目。")
            return False

        md_raw = self._md_path_edit.text().strip()
        if not md_raw:
            QMessageBox.warning(self, "路径为空", "请选择 Markdown 导出路径。")
            return False
        md_path = Path(md_raw)
        if not md_path.name:
            QMessageBox.warning(self, "路径为空", "请选择 Markdown 导出路径。")
            return False

        md_text = export_questions_to_markdown(
            excel_file_name=project_dir.name,
            export_date=date.today(),
            questions=questions,
        )
        if project_dir.exists():
            QMessageBox.warning(self, "目录已存在", f"目标题库目录已存在，请返回修改名称或保存位置：\n{project_dir}")
            return False
        try:
            project_dir.mkdir(parents=True, exist_ok=False)
            save_questions(repo, questions)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_text, encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return False
        self._state.last_export_dir = md_path.parent

        QMessageBox.information(self, "导出完成", "已导出完成。")
        return True

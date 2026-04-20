import sys
import os

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox, QWizard

from sj_generator.ui.state import WizardState
from sj_generator.ui.constants import *
from sj_generator.ui.pages import (
    IntroPage,
    WelcomePage,
    NamingPage,
    RepoPage,
    ModePage,
    ManualEntryPage,
    AiSelectFilesPage,
    AiImportPage,
    AiImportEditPage,
    ReviewPage,
    DedupeOptionPage,
    DedupeSetupPage,
    DedupeResultPage,
    AiAnalysisOptionPage,
    AiAnalysisPage,
    ExportPage,
)


class GeneratorWizard(QWizard):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("思政智题云枢")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        self.setButtonText(QWizard.WizardButton.BackButton, "上一步")
        self.setButtonText(QWizard.WizardButton.NextButton, "下一步")
        self.setButtonText(QWizard.WizardButton.CancelButton, "取消")
        self.setButtonText(QWizard.WizardButton.FinishButton, "导出并打开文件夹")

        self._state = WizardState()
        self.setPage(PAGE_INTRO, IntroPage())
        self.setPage(PAGE_WELCOME, WelcomePage(self._state))
        self.setPage(PAGE_REPO, RepoPage(self._state))
        self.setPage(PAGE_MODE, ModePage(self._state))
        self.setPage(PAGE_MANUAL, ManualEntryPage(self._state))
        self.setPage(PAGE_AI_SELECT, AiSelectFilesPage(self._state))
        self.setPage(PAGE_AI_IMPORT, AiImportPage(self._state))
        self.setPage(PAGE_AI_IMPORT_EDIT, AiImportEditPage(self._state))
        self.setPage(PAGE_REVIEW, ReviewPage(self._state))
        self.setPage(PAGE_DEDUPE_OPTION, DedupeOptionPage(self._state))
        self.setPage(PAGE_DEDUPE_SETUP, DedupeSetupPage(self._state))
        self.setPage(PAGE_DEDUPE_RESULT, DedupeResultPage(self._state))
        self.setPage(PAGE_AI_ANALYSIS_OPTION, AiAnalysisOptionPage(self._state))
        self.setPage(PAGE_AI_ANALYSIS, AiAnalysisPage(self._state))
        self.setPage(PAGE_NAME, NamingPage(self._state))
        self.setPage(PAGE_EXPORT, ExportPage(self._state))
        self._cache_and_hide_page_titles()
        self.setStartId(PAGE_INTRO)
        self.currentIdChanged.connect(self._update_window_title)
        self._update_window_title(self.startId())

    def _cache_and_hide_page_titles(self) -> None:
        for page_id in self.pageIds():
            page = self.page(page_id)
            if page is None:
                continue
            page.setProperty("_window_title_text", page.title())
            page.setTitle("")

    def _update_window_title(self, page_id: int) -> None:
        page = self.page(page_id)
        page_title = page.property("_window_title_text").strip() if page is not None and page.property("_window_title_text") else ""
        if page_title:
            self.setWindowTitle(page_title)
            return
        self.setWindowTitle("思政智题云枢")

    def accept(self) -> None:
        folder = None
        if self._state.last_export_dir is not None:
            folder = self._state.last_export_dir
        elif self._state.project_dir is not None:
            folder = self._state.project_dir
        elif self._state.repo_path is not None:
            folder = self._state.repo_path.parent
        if folder is not None:
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            if not ok:
                try:
                    os.startfile(str(folder))
                except Exception:
                    QMessageBox.warning(self, "打开失败", f"无法打开文件夹：\n{folder}")
        if not self._state.auto_close_after_finish:
            return
        super().accept()


def main() -> None:
    app = QApplication(sys.argv)
    w = GeneratorWizard()
    w.resize(980, 680)
    w.show()
    raise SystemExit(app.exec())

from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QWizard

from sj_generator.application.state import WizardState
from sj_generator.ui.constants import (
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    PAGE_AI_ANALYSIS,
    PAGE_AI_IMPORT,
    PAGE_AI_IMPORT_CONTENT,
    PAGE_AI_SELECT,
    PAGE_DEDUPE_RESULT,
    PAGE_IMPORT_SUCCESS,
    QT_MAX_WINDOW_SIZE,
)
from sj_generator.ui.wizard_base import AppWizardBase
from sj_generator.ui.pages.analysis_pages import AiAnalysisPage
from sj_generator.ui.pages.dedupe_pages import DedupeResultPage
from sj_generator.ui.pages.export_pages import ImportSuccessPage
from sj_generator.ui.pages.import_flow import AiImportContentPage, AiImportPage, AiSelectFilesPage

BUTTON_MIN_WIDTH = 128
BUTTON_MIN_HEIGHT = 40


def configure_import_flow_pages(wizard: QWizard, state: WizardState) -> None:
    wizard.setPage(PAGE_AI_SELECT, AiSelectFilesPage(state))
    wizard.setPage(PAGE_AI_IMPORT, AiImportPage(state))
    wizard.setPage(PAGE_AI_IMPORT_CONTENT, AiImportContentPage(state))
    wizard.setPage(PAGE_DEDUPE_RESULT, DedupeResultPage(state))
    wizard.setPage(PAGE_AI_ANALYSIS, AiAnalysisPage(state))
    wizard.setPage(PAGE_IMPORT_SUCCESS, ImportSuccessPage(state))


class ImportFlowWizard(AppWizardBase):
    def __init__(self, state: WizardState, parent=None, *, launcher=None, start_page_id: int = PAGE_AI_SELECT) -> None:
        super().__init__(parent)
        self._state = state
        self._launcher = launcher
        self._start_page_id = start_page_id
        self.apply_button_texts(
            {
                QWizard.WizardButton.BackButton: "返回",
                QWizard.WizardButton.CustomButton1: "添加文档",
                QWizard.WizardButton.CustomButton2: "打开文档",
                QWizard.WizardButton.NextButton: "开始导题",
                QWizard.WizardButton.FinishButton: "完成",
            }
        )
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self.setButtonLayout(
            [
                QWizard.WizardButton.Stretch,
                QWizard.WizardButton.CustomButton1,
                QWizard.WizardButton.CustomButton2,
                QWizard.WizardButton.BackButton,
                QWizard.WizardButton.NextButton,
                QWizard.WizardButton.FinishButton,
            ]
        )
        configure_import_flow_pages(self, self._state)
        self.cache_and_hide_page_titles()
        self.setStartId(start_page_id)
        self.currentIdChanged.connect(self.update_window_title)
        self.currentIdChanged.connect(self._sync_window_resizability)
        self.currentIdChanged.connect(self._style_navigation_buttons)
        self.currentIdChanged.connect(self._sync_page_navigation_buttons)
        self.update_window_title(self.startId())
        self._sync_window_resizability(self.startId())
        self._style_navigation_buttons(self.startId())
        self._sync_page_navigation_buttons(self.startId())
        self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)

    def _sync_window_resizability(self, _page_id: int) -> None:
        self.setMinimumSize(0, 0)
        self.setMaximumSize(QT_MAX_WINDOW_SIZE, QT_MAX_WINDOW_SIZE)

    def _style_navigation_buttons(self, _page_id: int) -> None:
        for which in (
            QWizard.WizardButton.CustomButton1,
            QWizard.WizardButton.CustomButton2,
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
        ):
            button = self.button(which)
            if button is None:
                continue
            button.setMinimumSize(BUTTON_MIN_WIDTH, BUTTON_MIN_HEIGHT)
            button.setStyleSheet("padding: 6px 18px;")

    def _sync_page_navigation_buttons(self, _page_id: int) -> None:
        page = self.currentPage()
        sync = getattr(page, "_sync_wizard_buttons", None)
        if callable(sync):
            sync()
        button = self.button(QWizard.WizardButton.CustomButton1)
        if button is not None:
            button.setVisible(False)
        button2 = self.button(QWizard.WizardButton.CustomButton2)
        if button2 is not None:
            button2.setVisible(False)
        handler = getattr(page, "_sync_custom_wizard_button", None)
        if callable(handler):
            handler()

    def accept(self) -> None:
        super().accept()

    def reject(self) -> None:
        if not self._can_close_current_page():
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._can_close_current_page():
            event.ignore()
            return
        super().closeEvent(event)

    def _can_close_current_page(self) -> bool:
        page = self.currentPage()
        guard = getattr(page, "prepare_to_close", None)
        if callable(guard):
            return bool(guard())
        return True

    def open_additional_documents(self, source_paths, *, message_parent=None) -> bool:
        handler = getattr(self._launcher, "_open_import_flow_windows", None)
        if not callable(handler):
            return False
        return bool(handler(source_paths, message_parent=message_parent))

    def open_split_import_flow_windows(self, states, *, start_page_id: int) -> bool:
        handler = getattr(self._launcher, "_open_split_import_flow_windows", None)
        if not callable(handler):
            return False
        return bool(handler(states, start_page_id=start_page_id))

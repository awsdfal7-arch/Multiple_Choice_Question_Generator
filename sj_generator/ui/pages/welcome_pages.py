from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QRadioButton, QVBoxLayout, QWizard, QWizardPage

from sj_generator.ai.balance import load_provider_balance_statuses
from sj_generator.config import load_deepseek_config, load_kimi_config, load_qwen_config
from sj_generator.ui.constants import PAGE_REPO
from sj_generator.ui.api_config_dialog import ApiConfigDialog
from sj_generator.ui.program_settings_dialog import ProgramSettingsDialog
from sj_generator.ui.state import WizardState


class WelcomePage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("开始")

        self._status = QLabel("")
        self._status.setWordWrap(True)

        api_btn = QPushButton("配置 API…")
        api_btn.clicked.connect(self._open_api_cfg)
        settings_btn = QPushButton("程序设定…")
        settings_btn.clicked.connect(self._open_program_settings)

        self._wizard_radio = QRadioButton("进入常规向导")
        self._wizard_radio.setChecked(True)
        self._batch_radio = QRadioButton("进入批量处理（暂未实现）")
        self._batch_radio.setEnabled(False)

        self._deepseek_cfg = load_deepseek_config()
        self._kimi_cfg = load_kimi_config()
        self._qwen_cfg = load_qwen_config()
        self._balance_texts = {
            "deepseek": "未配置",
            "kimi": "未配置",
            "qwen": "未配置",
        }
        self._balance_thread: QThread | None = None
        self._balance_worker: _BalanceWorker | None = None

        title_label = QLabel("欢迎使用思政智题云枢")
        title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        title_label.setStyleSheet("font-size: 22px; font-weight: 600;")

        layout = QVBoxLayout()
        layout.addWidget(title_label)
        layout.addWidget(self._status)
        layout.addWidget(self._wizard_radio)
        layout.addWidget(self._batch_radio)
        layout.addWidget(api_btn)
        layout.addWidget(settings_btn)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        wizard = self.wizard()
        if wizard is not None:
            wizard.button(QWizard.WizardButton.NextButton).show()
            wizard.setButtonText(QWizard.WizardButton.NextButton, "下一步")
        self._refresh_status()
        self._wizard_radio.setChecked(True)
        self._refresh_balances()

    def nextId(self) -> int:
        self._state.start_mode = "wizard"
        return PAGE_REPO

    def _open_api_cfg(self) -> None:
        dlg = ApiConfigDialog(self)
        if dlg.exec():
            self._refresh_status()
            self._refresh_balances()

    def _open_program_settings(self) -> None:
        dlg = ProgramSettingsDialog(self._state, self)
        dlg.exec()

    def _refresh_status(self) -> None:
        self._deepseek_cfg = load_deepseek_config()
        self._kimi_cfg = load_kimi_config()
        self._qwen_cfg = load_qwen_config()
        self._balance_texts = {
            "deepseek": "已配置，余额待查询" if self._deepseek_cfg.is_ready() else "未配置",
            "kimi": "已配置，余额待查询" if self._kimi_cfg.is_ready() else "未配置",
            "qwen": _describe_qwen_status(self._qwen_cfg),
        }
        self._render_status()

    def _render_status(self) -> None:
        lines = [
            f"DeepSeek：{self._balance_texts['deepseek']}",
            f"Kimi：{self._balance_texts['kimi']}",
            f"千问：{self._balance_texts['qwen']}",
        ]
        self._status.setText("\n".join(lines))

    def _refresh_balances(self) -> None:
        if self._balance_thread is not None:
            return

        if self._deepseek_cfg.is_ready():
            self._balance_texts["deepseek"] = "已配置，正在查询余额..."
        if self._kimi_cfg.is_ready():
            self._balance_texts["kimi"] = "已配置，正在查询余额..."
        if self._qwen_cfg.has_account_balance_credentials():
            self._balance_texts["qwen"] = "已配置，正在查询阿里云账户余额..."
        self._render_status()

        worker = _BalanceWorker(
            deepseek_cfg=self._deepseek_cfg,
            kimi_cfg=self._kimi_cfg,
            qwen_cfg=self._qwen_cfg,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.updated.connect(self._on_balance_updated)
        worker.done.connect(self._on_balance_done)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_balance_thread_finished)
        self._balance_worker = worker
        self._balance_thread = thread
        thread.start()

    def _on_balance_updated(self, provider: str, detail: str) -> None:
        self._balance_texts[provider] = detail
        self._render_status()

    def _on_balance_done(self) -> None:
        return

    def _on_balance_thread_finished(self) -> None:
        self._balance_worker = None
        self._balance_thread = None


class _BalanceWorker(QObject):
    updated = pyqtSignal(str, str)
    done = pyqtSignal()

    def __init__(self, *, deepseek_cfg, kimi_cfg, qwen_cfg) -> None:
        super().__init__()
        self._deepseek_cfg = deepseek_cfg
        self._kimi_cfg = kimi_cfg
        self._qwen_cfg = qwen_cfg

    def run(self) -> None:
        statuses = load_provider_balance_statuses(
            deepseek_cfg=self._deepseek_cfg,
            kimi_cfg=self._kimi_cfg,
            qwen_cfg=self._qwen_cfg,
        )
        for item in statuses:
            self.updated.emit(item.provider, item.detail)
        self.done.emit()


def _describe_qwen_status(cfg) -> str:
    if cfg.has_account_balance_credentials():
        return "已配置，阿里云账户余额待查询"
    if cfg.is_ready():
        return "已配置模型 API；未配置阿里云 AccessKey"
    return "未配置"

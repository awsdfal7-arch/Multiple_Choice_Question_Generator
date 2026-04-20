from PyQt6.QtWidgets import QLabel, QPushButton, QRadioButton, QVBoxLayout, QWizardPage

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

        layout = QVBoxLayout()
        layout.addWidget(QLabel("欢迎使用思政题目生成器。"))
        layout.addWidget(self._status)
        layout.addWidget(self._wizard_radio)
        layout.addWidget(self._batch_radio)
        layout.addWidget(api_btn)
        layout.addWidget(settings_btn)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._refresh_status()
        self._wizard_radio.setChecked(True)

    def nextId(self) -> int:
        self._state.start_mode = "wizard"
        return PAGE_REPO

    def _open_api_cfg(self) -> None:
        dlg = ApiConfigDialog(self)
        if dlg.exec():
            self._refresh_status()

    def _open_program_settings(self) -> None:
        dlg = ProgramSettingsDialog(self._state, self)
        dlg.exec()

    def _refresh_status(self) -> None:
        deepseek = load_deepseek_config()
        kimi = load_kimi_config()
        qwen = load_qwen_config()
        a = "DeepSeek：已配置" if deepseek.is_ready() else "DeepSeek：未配置"
        b = "Kimi：已配置" if kimi.is_ready() else "Kimi：未配置"
        c = "千问：已配置" if qwen.is_ready() else "千问：未配置"
        self._status.setText(a + "；" + b + "；" + c)

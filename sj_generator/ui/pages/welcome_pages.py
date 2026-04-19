from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWizardPage

from sj_generator.config import load_deepseek_config, load_kimi_config, load_qwen_config
from sj_generator.ui.constants import PAGE_REPO
from sj_generator.ui.api_config_dialog import ApiConfigDialog
from sj_generator.ui.state import AI_CONCURRENCY_OPTIONS, WizardState, normalize_ai_concurrency


class WelcomePage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("开始")

        self._status = QLabel("")
        self._status.setWordWrap(True)

        api_btn = QPushButton("配置 API…")
        api_btn.clicked.connect(self._open_api_cfg)

        self._wizard_radio = QRadioButton("进入常规向导")
        self._wizard_radio.setChecked(True)

        self._concurrency_combo = QComboBox()
        for value in AI_CONCURRENCY_OPTIONS:
            self._concurrency_combo.addItem(str(value), value)
        self._concurrency_combo.currentIndexChanged.connect(self._save_concurrency)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("欢迎使用思政题目生成器。"))
        layout.addWidget(self._status)
        layout.addWidget(self._wizard_radio)
        concurrency_row = QHBoxLayout()
        concurrency_row.addWidget(QLabel("统一并发数："))
        concurrency_row.addWidget(self._concurrency_combo)
        concurrency_row.addWidget(QLabel("同时用于 AI 导题与 AI 生成解析"))
        concurrency_row.addStretch(1)
        layout.addLayout(concurrency_row)
        layout.addWidget(api_btn)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        self._refresh_status()
        self._wizard_radio.setChecked(True)
        value = normalize_ai_concurrency(self._state.ai_concurrency)
        idx = self._concurrency_combo.findData(value)
        if idx >= 0:
            self._concurrency_combo.setCurrentIndex(idx)
        self._save_concurrency()

    def nextId(self) -> int:
        self._state.start_mode = "wizard"
        return PAGE_REPO

    def _open_api_cfg(self) -> None:
        dlg = ApiConfigDialog(self)
        if dlg.exec():
            self._refresh_status()

    def _save_concurrency(self) -> None:
        value = self._concurrency_combo.currentData()
        self._state.ai_concurrency = normalize_ai_concurrency(value)

    def _refresh_status(self) -> None:
        deepseek = load_deepseek_config()
        kimi = load_kimi_config()
        qwen = load_qwen_config()
        a = "DeepSeek：已配置" if deepseek.is_ready() else "DeepSeek：未配置"
        b = "Kimi：已配置" if kimi.is_ready() else "Kimi：未配置"
        c = "千问：已配置" if qwen.is_ready() else "千问：未配置"
        self._status.setText(a + "；" + b + "；" + c)

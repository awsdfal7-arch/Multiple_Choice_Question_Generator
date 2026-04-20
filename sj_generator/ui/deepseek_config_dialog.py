from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from sj_generator.ai.client import LlmClient
from sj_generator.config import DeepSeekConfig, load_deepseek_config, save_deepseek_config, to_llm_config


class DeepSeekConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("DeepSeek 配置")

        cfg = load_deepseek_config()
        self._env_api_key = cfg.api_key.strip()

        self._base_url_edit = QLineEdit(cfg.base_url)
        self._model_edit = QLineEdit(cfg.model)
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setReadOnly(True)
        self._api_key_edit.setPlaceholderText(
            "已从环境变量 DEEPSEEK_API_KEY 读取" if self._env_api_key else "未设置环境变量 DEEPSEEK_API_KEY"
        )
        self._api_key_hint = QLineEdit("API Key 仅从环境变量 DEEPSEEK_API_KEY 读取")
        self._api_key_hint.setReadOnly(True)
        self._timeout_edit = QLineEdit(str(cfg.timeout_s))

        self._save_checkbox = QCheckBox("保存非敏感配置到本机")
        self._save_checkbox.setChecked(True)
        self._test_btn = QPushButton("测试 API")
        self._test_btn.clicked.connect(self._on_test_api)
        self._tested_key = ""

        form = QFormLayout()
        form.addRow("Base URL：", self._base_url_edit)
        form.addRow("Model：", self._model_edit)
        form.addRow("API Key：", self._api_key_edit)
        form.addRow("", self._api_key_hint)
        form.addRow("超时(秒)：", self._timeout_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._test_btn)
        layout.addWidget(self._save_checkbox)
        layout.addWidget(buttons)
        self.setLayout(layout)

        self._cfg = cfg
        self._base_url_edit.textChanged.connect(self._reset_tested)
        self._model_edit.textChanged.connect(self._reset_tested)
        self._timeout_edit.textChanged.connect(self._reset_tested)

    def config(self) -> DeepSeekConfig:
        return self._cfg

    def _on_accept(self) -> None:
        cfg = self._collect_cfg()
        if cfg is None:
            return
        if self._tested_key != self._cfg_key(cfg):
            QMessageBox.warning(self, "请先测试", "请先点击“测试 API”并通过后再保存。")
            return

        self._cfg = cfg
        if self._save_checkbox.isChecked():
            try:
                save_deepseek_config(cfg)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))
                return

        self.accept()

    def _collect_cfg(self) -> DeepSeekConfig | None:
        try:
            timeout_s = float(self._timeout_edit.text().strip() or "0")
        except Exception:
            QMessageBox.warning(self, "参数不合法", "超时(秒)需要是数字。")
            return None
        if timeout_s <= 0:
            QMessageBox.warning(self, "参数不合法", "超时(秒)必须大于 0。")
            return None
        cfg = DeepSeekConfig(
            base_url=self._base_url_edit.text().strip(),
            api_key=self._env_api_key,
            model=self._model_edit.text().strip(),
            timeout_s=timeout_s,
        )
        if not cfg.base_url or not cfg.model:
            QMessageBox.warning(self, "信息不完整", "Base URL 和 Model 不能为空。")
            return None
        return cfg

    def _on_test_api(self) -> None:
        cfg = self._collect_cfg()
        if cfg is None:
            return
        try:
            client = LlmClient(to_llm_config(cfg))
            text = client.chat_text(system="你是连通性测试助手。", user="请只返回 OK")
            if "OK" not in (text or "").upper():
                QMessageBox.warning(self, "测试失败", f"API 可用性测试未通过，返回：{text}")
                return
        except Exception as e:
            QMessageBox.critical(self, "测试失败", f"API 可用性测试失败：{e}")
            return
        self._tested_key = self._cfg_key(cfg)
        QMessageBox.information(self, "测试通过", "DeepSeek API 可用性测试通过。")

    def _cfg_key(self, cfg: DeepSeekConfig) -> str:
        return "|".join([cfg.base_url, cfg.api_key, cfg.model, str(cfg.timeout_s)])

    def _reset_tested(self, *_args) -> None:
        self._tested_key = ""

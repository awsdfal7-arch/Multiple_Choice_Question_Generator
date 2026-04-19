from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sj_generator.ai.client import LlmClient
from sj_generator.config import (
    DeepSeekConfig,
    KimiConfig,
    QwenConfig,
    load_deepseek_config,
    load_kimi_config,
    load_qwen_config,
    save_deepseek_config,
    save_kimi_config,
    save_qwen_config,
    to_analysis_llm_config,
    to_kimi_llm_config,
    to_llm_config,
    to_qwen_llm_config,
)


class ApiConfigDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API 配置")
        self.resize(560, 320)

        self._deepseek_tab = _ApiConfigTab(
            title="DeepSeek",
            cfg=load_deepseek_config(),
            save_fn=save_deepseek_config,
            to_llm_fn=to_llm_config,
            analysis_to_llm_fn=to_analysis_llm_config,
            cfg_type=DeepSeekConfig,
        )
        self._kimi_tab = _ApiConfigTab(
            title="Kimi",
            cfg=load_kimi_config(),
            save_fn=save_kimi_config,
            to_llm_fn=to_kimi_llm_config,
            cfg_type=KimiConfig,
        )
        self._qwen_tab = _ApiConfigTab(
            title="千问",
            cfg=load_qwen_config(),
            save_fn=save_qwen_config,
            to_llm_fn=to_qwen_llm_config,
            cfg_type=QwenConfig,
        )

        tabs = QTabWidget()
        tabs.addTab(self._deepseek_tab, "DeepSeek")
        tabs.addTab(self._kimi_tab, "Kimi")
        tabs.addTab(self._qwen_tab, "千问")

        hint = QLabel("可以在一个窗口内统一配置三个模型；修改过的配置需先测试通过后才能保存。")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(tabs)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _on_accept(self) -> None:
        try:
            self._deepseek_tab.save_if_needed()
            self._kimi_tab.save_if_needed()
            self._qwen_tab.save_if_needed()
        except _ConfigValidationError as e:
            QMessageBox.warning(self, "配置未完成", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.accept()


class _ConfigValidationError(Exception):
    pass


class _ApiConfigTab(QWidget):
    def __init__(
        self,
        *,
        title: str,
        cfg,
        save_fn: Callable[[object], None],
        to_llm_fn: Callable[[object], object],
        cfg_type,
        analysis_to_llm_fn: Callable[[object], object] | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._cfg = cfg
        self._save_fn = save_fn
        self._to_llm_fn = to_llm_fn
        self._analysis_to_llm_fn = analysis_to_llm_fn
        self._cfg_type = cfg_type

        self._base_url_edit = QLineEdit(cfg.base_url)
        self._model_edit = QComboBox()
        self._model_edit.setEditable(True)
        self._model_edit.addItems(_model_candidates(title))
        self._model_edit.setCurrentText(cfg.model)
        self._analysis_model_edit: QComboBox | None = None
        if isinstance(cfg, DeepSeekConfig):
            self._analysis_model_edit = QComboBox()
            self._analysis_model_edit.setEditable(True)
            self._analysis_model_edit.addItems(_analysis_model_candidates(title))
            self._analysis_model_edit.setCurrentText(cfg.analysis_model)
        self._api_key_edit = QLineEdit(cfg.api_key)
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._timeout_edit = QLineEdit(str(cfg.timeout_s))
        self._save_checkbox = QCheckBox("保存到本机配置")
        self._save_checkbox.setChecked(True)
        self._test_btn = QPushButton("测试 API")
        self._test_btn.clicked.connect(self._on_test_api)
        self._status = QLabel("未测试")
        self._status.setWordWrap(True)
        self._tested_key = self._cfg_key(cfg) if getattr(cfg, "is_ready", lambda: False)() else ""

        form = QFormLayout()
        form.addRow("Base URL：", self._base_url_edit)
        form.addRow("Model：", self._model_edit)
        if self._analysis_model_edit is not None:
            form.addRow("解析生成模型：", self._analysis_model_edit)
        form.addRow("API Key：", self._api_key_edit)
        form.addRow("超时(秒)：", self._timeout_edit)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._test_btn)
        layout.addWidget(self._save_checkbox)
        layout.addWidget(self._status)
        layout.addStretch(1)
        self.setLayout(layout)

        self._base_url_edit.textChanged.connect(self._reset_tested)
        self._model_edit.currentTextChanged.connect(self._reset_tested)
        if self._analysis_model_edit is not None:
            self._analysis_model_edit.currentTextChanged.connect(self._reset_tested)
        self._api_key_edit.textChanged.connect(self._reset_tested)
        self._timeout_edit.textChanged.connect(self._reset_tested)

        if self._tested_key:
            self._status.setText("当前已加载可用配置。")

    def save_if_needed(self) -> None:
        cfg = self._collect_cfg()
        cfg_key = self._cfg_key(cfg)
        if cfg_key != self._tested_key:
            raise _ConfigValidationError(f"{self._title} 配置已修改，请先点击“测试 API”并通过。")
        self._cfg = cfg
        if self._save_checkbox.isChecked():
            self._save_fn(cfg)

    def _collect_cfg(self):
        try:
            timeout_s = float(self._timeout_edit.text().strip() or "0")
        except Exception as e:
            raise _ConfigValidationError(f"{self._title} 的超时(秒)需要是数字。") from e
        if timeout_s <= 0:
            raise _ConfigValidationError(f"{self._title} 的超时(秒)必须大于 0。")
        kwargs = dict(
            base_url=self._base_url_edit.text().strip(),
            api_key=self._api_key_edit.text().strip(),
            model=self._model_edit.currentText().strip(),
            timeout_s=timeout_s,
        )
        if self._analysis_model_edit is not None:
            kwargs["analysis_model"] = self._analysis_model_edit.currentText().strip()
        cfg = self._cfg_type(**kwargs)
        if not cfg.base_url or not cfg.model:
            raise _ConfigValidationError(f"{self._title} 的 Base URL 和 Model 不能为空。")
        if self._analysis_model_edit is not None and not getattr(cfg, "analysis_model", "").strip():
            raise _ConfigValidationError(f"{self._title} 的解析生成模型不能为空。")
        return cfg

    def _on_test_api(self) -> None:
        try:
            cfg = self._collect_cfg()
            client = LlmClient(self._to_llm_fn(cfg))
            text = client.chat_text(system="你是连通性测试助手。", user="请只返回 OK")
            if "OK" not in (text or "").upper():
                QMessageBox.warning(self, "测试失败", f"{self._title} API 测试未通过，返回：{text}")
                self._status.setText("最近一次测试失败。")
                return
            if self._analysis_model_edit is not None and self._analysis_to_llm_fn is not None:
                analysis_client = LlmClient(self._analysis_to_llm_fn(cfg))
                analysis_text = analysis_client.chat_text(system="你是连通性测试助手。", user="请只返回 OK")
                if "OK" not in (analysis_text or "").upper():
                    QMessageBox.warning(self, "测试失败", f"{self._title} 解析生成模型测试未通过，返回：{analysis_text}")
                    self._status.setText("最近一次测试失败。")
                    return
        except _ConfigValidationError as e:
            QMessageBox.warning(self, "参数不合法", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "测试失败", f"{self._title} API 测试失败：{e}")
            self._status.setText("最近一次测试失败。")
            return
        self._tested_key = self._cfg_key(cfg)
        self._status.setText(f"{self._title} API 测试通过。")
        QMessageBox.information(
            self,
            "测试通过",
            f"{self._title} API 可用性测试通过。"
            if self._analysis_model_edit is None
            else f"{self._title} 普通模型与解析生成模型均测试通过。",
        )

    def _cfg_key(self, cfg) -> str:
        parts = [cfg.base_url.strip(), cfg.api_key.strip(), cfg.model.strip()]
        if hasattr(cfg, "analysis_model"):
            parts.append(getattr(cfg, "analysis_model").strip())
        parts.append(str(float(cfg.timeout_s)))
        return "|".join(parts)

    def _reset_tested(self, *_args) -> None:
        self._tested_key = ""
        self._status.setText("配置已变更，需重新测试。")


def _model_candidates(title: str) -> list[str]:
    if title == "DeepSeek":
        return [
            "deepseek-chat",
            "deepseek-reasoner",
        ]
    if title == "Kimi":
        return [
            "kimi-k2-turbo-preview",
            "moonshot-v1-32k",
            "moonshot-v1-8k",
            "moonshot-v1-128k",
        ]
    if title == "千问":
        return [
            "qwen-max",
            "qwen3.5-plus",
            "qwen-plus",
            "qwen-turbo",
        ]
    return []


def _analysis_model_candidates(title: str) -> list[str]:
    if title == "DeepSeek":
        return [
            "deepseek-reasoner",
            "deepseek-chat",
        ]
    return []

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from sj_generator.ai.client import LlmConfig


@dataclass(frozen=True)
class DeepSeekConfig:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    analysis_model: str = "deepseek-reasoner"
    timeout_s: float = 120.0

    def is_ready(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip() and self.analysis_model.strip())


@dataclass(frozen=True)
class KimiConfig:
    base_url: str = "https://api.moonshot.cn/v1"
    api_key: str = ""
    model: str = "kimi-k2-turbo-preview"
    timeout_s: float = 120.0

    def is_ready(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())


@dataclass(frozen=True)
class QwenConfig:
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen-max"
    timeout_s: float = 120.0

    def is_ready(self) -> bool:
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())


def load_deepseek_config() -> DeepSeekConfig:
    env_base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    env_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    env_model = os.getenv("DEEPSEEK_MODEL", "").strip()
    env_analysis_model = os.getenv("DEEPSEEK_ANALYSIS_MODEL", "").strip()
    env_timeout = os.getenv("DEEPSEEK_TIMEOUT_S", "").strip()

    file_cfg = _load_json_config_file(_config_path())
    file_timeout = file_cfg.get("timeout_s")
    timeout_s = DeepSeekConfig.timeout_s
    try:
        if env_timeout:
            timeout_s = float(env_timeout)
        elif file_timeout is not None:
            timeout_s = float(file_timeout)
    except Exception:
        timeout_s = DeepSeekConfig.timeout_s

    cfg = DeepSeekConfig(
        base_url=_clean_base_url(env_base_url or file_cfg.get("base_url") or DeepSeekConfig.base_url),
        api_key=(env_api_key or file_cfg.get("api_key") or "").strip(),
        model=(env_model or file_cfg.get("model") or DeepSeekConfig.model).strip(),
        analysis_model=(env_analysis_model or file_cfg.get("analysis_model") or DeepSeekConfig.analysis_model).strip(),
        timeout_s=timeout_s,
    )
    return cfg


def save_deepseek_config(cfg: DeepSeekConfig) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "base_url": cfg.base_url.strip(),
        "api_key": cfg.api_key.strip(),
        "model": cfg.model.strip(),
        "analysis_model": cfg.analysis_model.strip(),
        "timeout_s": cfg.timeout_s,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def to_llm_config(cfg: DeepSeekConfig) -> LlmConfig:
    return LlmConfig(
        base_url=cfg.base_url.strip(),
        api_key=cfg.api_key.strip(),
        model=cfg.model.strip(),
        timeout_s=float(cfg.timeout_s),
    )


def to_analysis_llm_config(cfg: DeepSeekConfig) -> LlmConfig:
    return LlmConfig(
        base_url=cfg.base_url.strip(),
        api_key=cfg.api_key.strip(),
        model=cfg.analysis_model.strip(),
        timeout_s=float(cfg.timeout_s),
    )


def load_kimi_config() -> KimiConfig:
    env_base_url = os.getenv("KIMI_BASE_URL", "").strip()
    env_api_key = os.getenv("KIMI_API_KEY", "").strip()
    env_model = os.getenv("KIMI_MODEL", "").strip()
    env_timeout = os.getenv("KIMI_TIMEOUT_S", "").strip()

    file_cfg = _load_json_config_file(_kimi_config_path())
    file_timeout = file_cfg.get("timeout_s")
    timeout_s = KimiConfig.timeout_s
    try:
        if env_timeout:
            timeout_s = float(env_timeout)
        elif file_timeout is not None:
            timeout_s = float(file_timeout)
    except Exception:
        timeout_s = KimiConfig.timeout_s

    return KimiConfig(
        base_url=_clean_base_url(env_base_url or file_cfg.get("base_url") or KimiConfig.base_url),
        api_key=(env_api_key or file_cfg.get("api_key") or "").strip(),
        model=(env_model or file_cfg.get("model") or KimiConfig.model).strip(),
        timeout_s=timeout_s,
    )


def save_kimi_config(cfg: KimiConfig) -> None:
    path = _kimi_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "base_url": _clean_base_url(cfg.base_url),
        "api_key": cfg.api_key.strip(),
        "model": cfg.model.strip(),
        "timeout_s": cfg.timeout_s,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def to_kimi_llm_config(cfg: KimiConfig) -> LlmConfig:
    return LlmConfig(
        base_url=_clean_base_url(cfg.base_url),
        api_key=cfg.api_key.strip(),
        model=cfg.model.strip(),
        timeout_s=float(cfg.timeout_s),
    )


def load_qwen_config() -> QwenConfig:
    env_base_url = os.getenv("QWEN_BASE_URL", "").strip()
    env_api_key = os.getenv("QWEN_API_KEY", "").strip()
    env_model = os.getenv("QWEN_MODEL", "").strip()
    env_timeout = os.getenv("QWEN_TIMEOUT_S", "").strip()

    file_cfg = _load_json_config_file(_qwen_config_path())
    file_timeout = file_cfg.get("timeout_s")
    timeout_s = QwenConfig.timeout_s
    try:
        if env_timeout:
            timeout_s = float(env_timeout)
        elif file_timeout is not None:
            timeout_s = float(file_timeout)
    except Exception:
        timeout_s = QwenConfig.timeout_s

    return QwenConfig(
        base_url=_clean_base_url(env_base_url or file_cfg.get("base_url") or QwenConfig.base_url),
        api_key=(env_api_key or file_cfg.get("api_key") or "").strip(),
        model=(env_model or file_cfg.get("model") or QwenConfig.model).strip(),
        timeout_s=timeout_s,
    )


def save_qwen_config(cfg: QwenConfig) -> None:
    path = _qwen_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "base_url": _clean_base_url(cfg.base_url),
        "api_key": cfg.api_key.strip(),
        "model": cfg.model.strip(),
        "timeout_s": cfg.timeout_s,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def to_qwen_llm_config(cfg: QwenConfig) -> LlmConfig:
    return LlmConfig(
        base_url=_clean_base_url(cfg.base_url),
        api_key=cfg.api_key.strip(),
        model=cfg.model.strip(),
        timeout_s=float(cfg.timeout_s),
    )


def _config_path() -> Path:
    env = os.getenv("SJ_GENERATOR_CONFIG_PATH", "").strip()
    if env:
        return Path(env)
    return _default_config_dir() / "deepseek.json"


def _kimi_config_path() -> Path:
    env = os.getenv("SJ_GENERATOR_KIMI_CONFIG_PATH", "").strip()
    if env:
        return Path(env)
    return _default_config_dir() / "kimi.json"


def _qwen_config_path() -> Path:
    env = os.getenv("SJ_GENERATOR_QWEN_CONFIG_PATH", "").strip()
    if env:
        return Path(env)
    return _default_config_dir() / "qwen.json"


def _load_json_config_file(path: Path) -> dict:
    if not path.exists():
        legacy = _legacy_config_path(path.name)
        if legacy is not None and legacy.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            data = _read_json_dict(legacy)
            if data:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data
        return {}
    return _read_json_dict(path)


def _clean_base_url(url: str) -> str:
    s = (url or "").strip()
    s = s.strip("`").strip('"').strip("'").strip()
    if s.endswith("/"):
        s = s[:-1]
    return s


def _default_config_dir() -> Path:
    app_data = os.getenv("APPDATA", "").strip()
    if app_data:
        return Path(app_data) / "sj_generator"
    return Path.home() / ".sj_generator"


def _legacy_config_path(file_name: str) -> Path | None:
    base_dir = Path(__file__).resolve().parents[1]
    path = base_dir / ".local" / file_name
    return path if path.exists() else None


def _read_json_dict(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}

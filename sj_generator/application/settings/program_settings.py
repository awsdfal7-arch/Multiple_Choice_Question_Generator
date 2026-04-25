from __future__ import annotations

from sj_generator.application.settings.storage import (
    load_json_config_file,
    program_settings_path,
    save_json_config_file,
)


def load_program_settings() -> dict:
    data = load_json_config_file(program_settings_path())
    return data if isinstance(data, dict) else {}


def save_program_settings(settings: dict) -> None:
    save_json_config_file(program_settings_path(), dict(settings))


def save_program_settings_merged(settings: dict) -> dict:
    data = load_program_settings()
    data.update(settings)
    save_program_settings(data)
    return data


def save_program_analysis_target(*, provider: str, model_name: str) -> dict:
    return save_program_settings_merged(
        {
            "analysis_provider": str(provider or "").strip().lower() or "deepseek",
            "analysis_model_name": str(model_name or "").strip() or "deepseek-reasoner",
        }
    )

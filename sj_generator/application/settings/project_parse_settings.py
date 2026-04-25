from __future__ import annotations

from sj_generator.application.settings.storage import (
    load_json_config_file,
    program_settings_path,
    save_json_config_file,
)

_PROJECT_PARSE_ROW_KEYS = ("question_number_parse", "question_content_parse")


def default_project_parse_model_rows() -> list[dict]:
    return [
        {"key": "question_number_parse", "round": "1", "ratio": "1/4", "models": []},
        {"key": "question_content_parse", "round": "2", "ratio": "1/4", "models": []},
    ]


def load_project_parse_model_rows() -> list[dict]:
    data = load_json_config_file(program_settings_path())
    return normalize_project_parse_model_rows(data.get("project_parse_model_rows"))


def save_project_parse_model_rows(rows: list[dict]) -> dict:
    data = load_json_config_file(program_settings_path())
    data["project_parse_model_rows"] = normalize_project_parse_model_rows(rows)
    save_json_config_file(program_settings_path(), data)
    return data


def normalize_project_parse_model_rows(rows: object) -> list[dict]:
    raw_rows = rows if isinstance(rows, list) else []
    normalized: list[dict] = []
    defaults = default_project_parse_model_rows()
    for index, key in enumerate(_PROJECT_PARSE_ROW_KEYS):
        raw = raw_rows[index] if index < len(raw_rows) and isinstance(raw_rows[index], dict) else {}
        default_row = defaults[index]
        round_value = str(raw.get("round") or default_row["round"]).strip()
        if round_value not in {"1", "2", "3", "4", "5", "6", "7", "8"}:
            round_value = default_row["round"]
        if key == "question_number_parse":
            round_value = "1"
        ratio_value = str(raw.get("ratio") or default_row["ratio"]).strip() or default_row["ratio"]
        normalized.append(
            {
                "key": key,
                "round": round_value,
                "ratio": ratio_value,
                "models": _normalize_project_parse_models(raw.get("models")),
            }
        )
    return normalized


def project_parse_model_override(row_key: str, provider: str) -> str:
    provider_key = _normalize_project_model_provider(provider)
    for row in load_project_parse_model_rows():
        if str(row.get("key") or "").strip() != row_key:
            continue
        for item in row.get("models") or []:
            if str(item.get("provider") or "").strip() == provider_key:
                return str(item.get("model_name") or "").strip()
    return ""


def _normalize_project_parse_models(models: object) -> list[dict]:
    raw_models = models if isinstance(models, list) else []
    normalized: list[dict] = []
    for raw in raw_models[:8]:
        if not isinstance(raw, dict):
            continue
        provider = _normalize_project_model_provider(raw.get("provider"))
        model_name = str(raw.get("model_name") or "").strip()
        if not provider or not model_name:
            continue
        normalized.append({"provider": provider, "model_name": model_name})
    return normalized


def _normalize_project_model_provider(value: object) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "deepseek": "deepseek",
        "deep seek": "deepseek",
        "kimi": "kimi",
        "moonshot": "kimi",
        "qwen": "qwen",
        "千问": "qwen",
    }
    return aliases.get(raw, "")

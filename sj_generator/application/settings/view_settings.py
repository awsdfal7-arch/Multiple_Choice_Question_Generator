from __future__ import annotations

from sj_generator.application.settings.storage import (
    load_json_config_file,
    save_json_config_file,
    welcome_view_config_path,
)


def load_welcome_table_column_visibility() -> dict[str, bool]:
    data = load_json_config_file(welcome_view_config_path())
    raw = data.get("table_column_visibility")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, bool] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            result[key] = bool(value)
    return result


def save_welcome_table_column_visibility(visibility: dict[str, bool]) -> None:
    _save_welcome_view_config_values(
        {"table_column_visibility": {str(key): bool(value) for key, value in visibility.items()}}
    )


def load_welcome_table_font_point_size() -> int | None:
    data = load_json_config_file(welcome_view_config_path())
    raw = data.get("table_font_point_size")
    try:
        value = int(raw)
    except Exception:
        return None
    return value if value > 0 else None


def save_welcome_table_font_point_size(point_size: int) -> None:
    try:
        value = int(point_size)
    except Exception:
        return
    if value <= 0:
        return
    _save_welcome_view_config_values({"table_font_point_size": value})


def load_welcome_tree_expanded_prefixes() -> list[str] | None:
    data = load_json_config_file(welcome_view_config_path())
    raw = data.get("tree_expanded_prefixes")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    return [str(value).strip() for value in raw if str(value).strip()]


def save_welcome_tree_expanded_prefixes(prefixes: list[str]) -> None:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in prefixes:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    _save_welcome_view_config_values({"tree_expanded_prefixes": normalized})


def _save_welcome_view_config_values(values: dict) -> None:
    path = welcome_view_config_path()
    data = load_json_config_file(path)
    data.update(values)
    save_json_config_file(path, data)

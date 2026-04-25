from __future__ import annotations

from decimal import Decimal

from sj_generator.infrastructure.llm.import_questions import question_content_model_specs
from sj_generator.infrastructure.llm.question_ref_scan import question_ref_model_specs
from sj_generator.infrastructure.llm.balance import query_provider_balance_snapshots
from sj_generator.application.settings import load_deepseek_config, load_kimi_config, load_qwen_config
from sj_generator.application.state import WizardState


def _format_cost_amount_text(currency: str, amount: Decimal) -> str:
    prefix = {"CNY": "¥", "USD": "$"}.get((currency or "").upper(), "")
    if prefix:
        return f"{prefix}{amount:.4f}"
    return f"{(currency or 'UNKNOWN').upper()} {amount:.4f}"


def capture_import_cost_before(state: WizardState) -> None:
    state.reset_import_cost_tracking()
    if not bool(getattr(state, "import_show_costs", True)):
        return
    snapshots = query_provider_balance_snapshots(
        deepseek_cfg=load_deepseek_config(),
        kimi_cfg=load_kimi_config(),
        qwen_cfg=load_qwen_config(),
    )
    state.import_cost_before_amounts = {
        snapshot.provider: f"{snapshot.currency}|{snapshot.amount}"
        for snapshot in snapshots
    }
    state.import_cost_before_details = {snapshot.provider: snapshot.detail for snapshot in snapshots}


def copy_import_cost_before(source: WizardState, target: WizardState) -> None:
    target.reset_import_cost_tracking()
    target.import_show_costs = bool(getattr(source, "import_show_costs", True))
    if not target.import_show_costs:
        return
    target.import_cost_before_amounts = dict(getattr(source, "import_cost_before_amounts", {}) or {})
    target.import_cost_before_details = dict(getattr(source, "import_cost_before_details", {}) or {})


def freeze_import_cost_result(state: WizardState) -> None:
    if state.import_cost_ready:
        return
    state.import_cost_ready = True
    state.import_cost_total_text = ""
    state.import_cost_summary_text = ""
    state.import_cost_detail_text = ""
    if not bool(getattr(state, "import_show_costs", True)):
        return
    used_models = _collect_import_used_models()
    before_amounts = dict(getattr(state, "import_cost_before_amounts", {}) or {})
    if not before_amounts:
        state.import_cost_detail_text = "未记录导入前余额，无法计算本次 docx 解析费用。"
        state.import_cost_rows = _build_unavailable_rows(
            used_models,
            before_text="未记录",
            current_text="未查询",
            cost_text="无法计算",
        )
        return
    after_snapshots = query_provider_balance_snapshots(
        deepseek_cfg=load_deepseek_config(),
        kimi_cfg=load_kimi_config(),
        qwen_cfg=load_qwen_config(),
    )
    if not after_snapshots:
        state.import_cost_detail_text = "未获取到导入后余额，无法计算本次 docx 解析费用。"
        state.import_cost_rows = _build_unavailable_rows(
            used_models,
            before_text="已记录",
            current_text="未获取",
            cost_text="无法计算",
        )
        return
    after_map = {snapshot.provider: snapshot for snapshot in after_snapshots}
    deltas: list[tuple[str, str, Decimal]] = []
    detail_lines: list[str] = []
    provider_labels = {"deepseek": "DeepSeek", "kimi": "Kimi", "qwen": "千问"}
    rows: list[dict[str, str]] = []
    for provider, models in used_models.items():
        snapshot = after_map.get(provider)
        raw_before = str(before_amounts.get(provider) or "").strip()
        if "|" not in raw_before:
            rows.append(
                {
                    "provider": provider_labels.get(provider, provider),
                    "models": "\n".join(models) if models else "-",
                    "before_balance": "未记录",
                    "current_balance": snapshot.detail if snapshot is not None else "未查询",
                    "cost": "无法计算",
                }
            )
            continue
        before_currency, _, before_amount_text = raw_before.partition("|")
        if snapshot is None:
            rows.append(
                {
                    "provider": provider_labels.get(provider, provider),
                    "models": "\n".join(models) if models else "-",
                    "before_balance": _format_cost_amount_text(before_currency.strip().upper() or "CNY", Decimal(before_amount_text.strip() or "0")),
                    "current_balance": "未查询",
                    "cost": "无法计算",
                }
            )
            continue
        before_currency = before_currency.strip().upper() or snapshot.currency
        try:
            before_amount = Decimal(before_amount_text.strip())
        except Exception:
            rows.append(
                {
                    "provider": provider_labels.get(provider, provider),
                    "models": "\n".join(models) if models else "-",
                    "before_balance": "格式异常",
                    "current_balance": snapshot.detail,
                    "cost": "无法计算",
                }
            )
            continue
        if before_currency != snapshot.currency:
            rows.append(
                {
                    "provider": provider_labels.get(provider, provider),
                    "models": "\n".join(models) if models else "-",
                    "before_balance": _format_cost_amount_text(before_currency, before_amount),
                    "current_balance": _format_cost_amount_text(snapshot.currency, snapshot.amount),
                    "cost": "币种不一致",
                }
            )
            continue
        delta = before_amount - snapshot.amount
        if delta < Decimal("0"):
            delta = Decimal("0")
        deltas.append((provider, snapshot.currency, delta))
        detail_lines.append(f"{provider_labels.get(provider, provider)}：{_format_cost_amount_text(snapshot.currency, delta)}")
        rows.append(
            {
                "provider": provider_labels.get(provider, provider),
                "models": "\n".join(models) if models else "-",
                "before_balance": _format_cost_amount_text(snapshot.currency, before_amount),
                "current_balance": _format_cost_amount_text(snapshot.currency, snapshot.amount),
                "cost": _format_cost_amount_text(snapshot.currency, delta),
            }
        )
    state.import_cost_rows = rows
    if not deltas:
        state.import_cost_detail_text = "导入前后余额币种不一致或缺少可比较快照，无法计算本次 docx 解析费用。"
        return
    totals_by_currency: dict[str, Decimal] = {}
    for _provider, currency, delta in deltas:
        totals_by_currency[currency] = totals_by_currency.get(currency, Decimal("0")) + delta
    non_zero_deltas = [item for item in deltas if item[2] > Decimal("0")]
    summary_parts = [
        f"{provider_labels.get(provider, provider)} {_format_cost_amount_text(currency, delta)}"
        for provider, currency, delta in non_zero_deltas
    ]
    total_parts = [
        _format_cost_amount_text(currency, amount)
        for currency, amount in sorted(totals_by_currency.items(), key=lambda item: item[0])
    ]
    state.import_cost_total_text = "；".join(total_parts) if total_parts else "0"
    state.import_cost_summary_text = "；".join(summary_parts) if summary_parts else "0"
    state.import_cost_detail_text = "；".join(detail_lines)


def _collect_import_used_models() -> dict[str, list[str]]:
    provider_map: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    def add(provider: str, model_name: str, stage_label: str) -> None:
        provider_key = str(provider or "").strip().lower()
        model_text = str(model_name or "").strip()
        if not provider_key or not model_text:
            return
        if provider_key not in provider_map:
            provider_map[provider_key] = []
            seen[provider_key] = set()
        value = f"{stage_label}：{model_text}"
        if value in seen[provider_key]:
            return
        seen[provider_key].add(value)
        provider_map[provider_key].append(value)

    for spec in question_ref_model_specs():
        add(str(spec.get("provider") or ""), str(spec.get("model_name") or ""), "题号题型")
    for spec in question_content_model_specs():
        add(str(spec.get("provider") or ""), str(spec.get("model_name") or ""), "题目内容")
    return provider_map


def _build_unavailable_rows(
    used_models: dict[str, list[str]],
    *,
    before_text: str,
    current_text: str,
    cost_text: str,
) -> list[dict[str, str]]:
    provider_labels = {"deepseek": "DeepSeek", "kimi": "Kimi", "qwen": "千问"}
    rows: list[dict[str, str]] = []
    for provider, models in used_models.items():
        rows.append(
            {
                "provider": provider_labels.get(provider, provider),
                "models": "\n".join(models) if models else "-",
                "before_balance": before_text,
                "current_balance": current_text,
                "cost": cost_text,
            }
        )
    return rows

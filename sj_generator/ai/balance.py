from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime
import hashlib
import hmac
from urllib.parse import quote
import uuid

import requests

from sj_generator.config import DeepSeekConfig, KimiConfig, QwenConfig


@dataclass(frozen=True)
class ProviderBalanceStatus:
    provider: str
    detail: str


def load_provider_balance_statuses(
    *,
    deepseek_cfg: DeepSeekConfig,
    kimi_cfg: KimiConfig,
    qwen_cfg: QwenConfig,
) -> list[ProviderBalanceStatus]:
    return [
        ProviderBalanceStatus("deepseek", _safe_describe(describe_deepseek_balance, deepseek_cfg)),
        ProviderBalanceStatus("kimi", _safe_describe(describe_kimi_balance, kimi_cfg)),
        ProviderBalanceStatus("qwen", _safe_describe(describe_qwen_balance, qwen_cfg)),
    ]


def describe_deepseek_balance(cfg: DeepSeekConfig) -> str:
    if not cfg.is_ready():
        return "未配置"

    url = _build_deepseek_balance_url(cfg.base_url)
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {cfg.api_key.strip()}"},
        timeout=cfg.timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()

    parts = _format_deepseek_balance_infos(data.get("balance_infos") or [])
    if not parts:
        detail = "已配置，余额接口已响应，但未返回可展示余额"
    else:
        detail = "已配置，余额 " + "；".join(parts)
    if data.get("is_available") is False:
        detail += "（当前账户不可用）"
    return detail


def describe_kimi_balance(cfg: KimiConfig) -> str:
    if not cfg.is_ready():
        return "未配置"

    url = _build_kimi_balance_url(cfg.base_url)
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {cfg.api_key.strip()}"},
        timeout=cfg.timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    return _describe_kimi_balance_payload(data)


def describe_qwen_balance(cfg: QwenConfig) -> str:
    if cfg.has_account_balance_credentials():
        data = _query_aliyun_account_balance(cfg)
        return _describe_aliyun_account_balance_payload(data)
    if cfg.is_ready():
        return "已配置模型 API；未配置阿里云 AccessKey，无法查询账户余额"
    if not cfg.is_ready():
        return "未配置"
    return "未配置"


def _build_deepseek_balance_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + "/user/balance"


def _build_kimi_balance_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/users/me/balance"


def _query_aliyun_account_balance(cfg: QwenConfig) -> dict:
    params = {
        "Action": "QueryAccountBalance",
        "Version": "2017-12-14",
        "Format": "JSON",
        "AccessKeyId": cfg.account_access_key_id.strip(),
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
    }
    params["Signature"] = _sign_aliyun_rpc_params(params, cfg.account_access_key_secret.strip())
    resp = requests.get("https://business.aliyuncs.com/", params=params, timeout=cfg.timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("Success") is False:
        raise RuntimeError(data.get("Message") or data.get("Code") or "阿里云账户余额查询失败")
    return data


def _format_deepseek_balance_infos(balance_infos: list[dict]) -> list[str]:
    parts: list[str] = []
    for item in balance_infos:
        currency = str(item.get("currency") or "").strip().upper() or "UNKNOWN"
        total = _format_money(item.get("total_balance"), currency)
        granted = _format_money(item.get("granted_balance"), currency)
        topped_up = _format_money(item.get("topped_up_balance"), currency)
        parts.append(f"{currency} {total}（赠送 {granted}，充值 {topped_up}）")
    return parts


def _format_money(value: object, currency: str) -> str:
    text = str(value or "0").strip().replace(",", "")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return text or "0"

    prefix = {"CNY": "¥", "USD": "$"}.get(currency, "")
    return f"{prefix}{amount:.2f}" if prefix else f"{amount:.2f}"


def _describe_kimi_balance_payload(data: object) -> str:
    payload = data
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        payload = data.get("data")

    if not isinstance(payload, dict):
        return "已配置，余额接口已响应，但返回格式无法识别"

    balances = payload.get("balances")
    if isinstance(balances, list):
        parts = _format_deepseek_balance_infos([item for item in balances if isinstance(item, dict)])
        if parts:
            return "已配置，余额 " + "；".join(parts)

    currency = str(payload.get("currency") or "CNY").strip().upper() or "CNY"
    for key in ("available_balance", "balance", "total_balance", "remaining_balance"):
        if key in payload and str(payload.get(key, "")).strip():
            amount = _format_money(payload.get(key), currency)
            return f"已配置，余额 {currency} {amount}"

    return "已配置，余额接口已响应，但未返回可展示余额"


def _describe_aliyun_account_balance_payload(data: object) -> str:
    payload = data.get("Data") if isinstance(data, dict) and isinstance(data.get("Data"), dict) else data
    if not isinstance(payload, dict):
        return "已配置，账户余额接口已响应，但返回格式无法识别"

    currency = str(payload.get("Currency") or "CNY").strip().upper() or "CNY"
    available = payload.get("AvailableAmount")
    if available is not None and str(available).strip():
        amount = _format_money(available, currency)
        return f"已配置，阿里云账户余额 {currency} {amount}"
    return "已配置，账户余额接口已响应，但未返回可展示余额"


def _sign_aliyun_rpc_params(params: dict[str, object], access_key_secret: str) -> str:
    canonicalized = "&".join(
        f"{_percent_encode(key)}={_percent_encode(str(value))}" for key, value in sorted(params.items(), key=lambda item: item[0])
    )
    string_to_sign = "GET&%2F&" + _percent_encode(canonicalized)
    digest = hmac.new((access_key_secret + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def _percent_encode(value: str) -> str:
    return quote(value, safe="~")


def _safe_describe(fn, cfg) -> str:
    try:
        return fn(cfg)
    except Exception as e:
        if getattr(cfg, "is_ready", lambda: False)() or getattr(cfg, "has_account_balance_credentials", lambda: False)():
            return f"已配置，余额查询失败：{e}"
        return "未配置"

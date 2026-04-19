from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from sj_generator.ai.client import LlmClient
from sj_generator.models import Question


@dataclass(frozen=True)
class ImportResult:
    questions: list[Question]
    raw_items: list[dict[str, Any]]


_WS_RE = re.compile(r"\s+")
_CIRCLED_RE = re.compile(r"[\u2460-\u2473]")
_COMBO_RE = re.compile(r"([A-D])\s*[\.．、]\s*([\u2460-\u2473\s]+)")
_LETTER_ONLY_RE = re.compile(r"^[A-D]+$")
_STEM_NUMBER_PATTERNS = [
    re.compile(r"^\s*(\d+)\s*[\.\．、\):：]\s*(.+)$", re.S),
    re.compile(r"^\s*[\(（]\s*(\d+)\s*[\)）]\s*(.+)$", re.S),
    re.compile(r"^\s*第\s*(\d+)\s*题\s*[\.\．、:：]?\s*(.+)$", re.S),
]


def import_questions_from_sources(
    *,
    client: LlmClient,
    kimi_client: LlmClient | None = None,
    qwen_client: LlmClient | None = None,
    client_factory: Callable[[], LlmClient] | None = None,
    kimi_client_factory: Callable[[], LlmClient] | None = None,
    qwen_client_factory: Callable[[], LlmClient] | None = None,
    sources: list[tuple[Path, str]],
    max_chars_per_chunk: int = 6000,
    strategy: str = "per_question",
    max_question_workers: int = 1,
    progress_cb: Callable[[str], None] | None = None,
    question_cb: Callable[[Question], None] | None = None,
    compare_cb: Callable[[dict[str, Any]], None] | None = None,
    progress_count_cb: Callable[[int, int], None] | None = None,
    stop_cb: Callable[[], bool] | None = None,
) -> ImportResult:
    if strategy == "per_question":
        return _import_questions_per_question(
            client=client,
            kimi_client=kimi_client,
            qwen_client=qwen_client,
            client_factory=client_factory,
            kimi_client_factory=kimi_client_factory,
            qwen_client_factory=qwen_client_factory,
            sources=sources,
            max_chars_per_chunk=max_chars_per_chunk,
            max_question_workers=max_question_workers,
            progress_cb=progress_cb,
            question_cb=question_cb,
            compare_cb=compare_cb,
            progress_count_cb=progress_count_cb,
            stop_cb=stop_cb,
        )

    items: list[dict[str, Any]] = []
    for path, text in sources:
        text = text.strip()
        if not text:
            continue
        for chunk in _split_text(text, max_chars_per_chunk=max_chars_per_chunk):
            chunk_items = _extract_questions_with_fallback(
                client=client,
                source_name=path.name,
                chunk_text=chunk,
                depth=3,
            )
            items.extend(chunk_items)

    normalized: list[Question] = []
    seen: set[str] = set()
    for obj in items:
        q = _to_question(obj)
        key = _normalize_key(q.stem)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        normalized.append(q)

    return ImportResult(questions=normalized, raw_items=items)

def _import_questions_per_question(
    *,
    client: LlmClient,
    kimi_client: LlmClient | None,
    qwen_client: LlmClient | None,
    client_factory: Callable[[], LlmClient] | None,
    kimi_client_factory: Callable[[], LlmClient] | None,
    qwen_client_factory: Callable[[], LlmClient] | None,
    sources: list[tuple[Path, str]],
    max_chars_per_chunk: int,
    max_question_workers: int,
    progress_cb: Callable[[str], None] | None,
    question_cb: Callable[[Question], None] | None,
    compare_cb: Callable[[dict[str, Any]], None] | None,
    progress_count_cb: Callable[[int, int], None] | None,
    stop_cb: Callable[[], bool] | None,
) -> ImportResult:
    items: list[dict[str, Any]] = []
    normalized: list[Question] = []
    seen: set[str] = set()
    for path, text in sources:
        if stop_cb and stop_cb():
            break
        text = text.strip()
        if not text:
            continue
        if progress_cb:
            progress_cb(f"{path.name}：统计题数…")
        n = _count_questions_with_fallback(
            client=client,
            source_name=path.name,
            chunk_text=text,
            depth=3,
        )
        if n <= 0:
            continue
        total_steps = n
        step = 0
        if progress_count_cb:
            progress_count_cb(0, total_steps)
        worker_count = max(1, int(max_question_workers))
        if worker_count <= 1:
            for i in range(1, n + 1):
                if stop_cb and stop_cb():
                    break
                obj, err, meta = _process_one_question(
                    client=client,
                    kimi_client=kimi_client,
                    qwen_client=qwen_client,
                    source_name=path.name,
                    chunk_text=text,
                    index=i,
                    total=n,
                    progress_cb=progress_cb,
                    compare_cb=compare_cb,
                )
                step += 1
                if progress_count_cb:
                    progress_count_cb(step, total_steps)
                _collect_question_result(
                    index=i,
                    obj=obj,
                    err=err,
                    progress_cb=progress_cb,
                    source_name=path.name,
                    items=items,
                    normalized=normalized,
                    seen=seen,
                    question_cb=question_cb,
                )
            continue

        if client_factory is None or kimi_client_factory is None or qwen_client_factory is None:
            raise ValueError("题级并发需要提供三模型客户端工厂。")

        with ThreadPoolExecutor(max_workers=min(worker_count, n)) as ex:
            fut_map = {
                ex.submit(
                    _process_one_question,
                    client=client_factory(),
                    kimi_client=kimi_client_factory(),
                    qwen_client=qwen_client_factory(),
                    source_name=path.name,
                    chunk_text=text,
                    index=i,
                    total=n,
                    progress_cb=progress_cb,
                    compare_cb=compare_cb,
                ): i
                for i in range(1, n + 1)
                if not (stop_cb and stop_cb())
            }
            results_by_index: dict[int, tuple[dict[str, Any], bool, dict[str, Any]]] = {}
            for fut in as_completed(fut_map):
                i = fut_map[fut]
                obj, err, meta = fut.result()
                results_by_index[i] = (obj, err, meta)
                step += 1
                if progress_count_cb:
                    progress_count_cb(step, total_steps)
            for i in sorted(results_by_index.keys()):
                obj, err, meta = results_by_index[i]
                _collect_question_result(
                    index=i,
                    obj=obj,
                    err=err,
                    progress_cb=progress_cb,
                    source_name=path.name,
                    items=items,
                    normalized=normalized,
                    seen=seen,
                    question_cb=question_cb,
                )

    return ImportResult(questions=normalized, raw_items=items)


def _process_one_question(
    *,
    client: LlmClient,
    kimi_client: LlmClient | None,
    qwen_client: LlmClient | None,
    source_name: str,
    chunk_text: str,
    index: int,
    total: int,
    progress_cb: Callable[[str], None] | None,
    compare_cb: Callable[[dict[str, Any]], None] | None,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    def bump(model_name: str, round_no: int, check_no: int) -> None:
        if progress_cb:
            progress_cb(f"{source_name}：第 {index}/{total} 题（第 {round_no}/3 轮，{model_name}）…")

    def mark_round(round_no: int, status: str) -> None:
        if not progress_cb:
            return
        if status == "start":
            progress_cb(f"{source_name}：第 {index}/{total} 题（第 {round_no}/3 轮，三模型并行请求中）")
        elif status == "consistent":
            progress_cb(f"{source_name}：第 {index}/{total} 题（第 {round_no}/3 轮，达到一致阈值）")
        elif status == "inconsistent":
            progress_cb(f"{source_name}：第 {index}/{total} 题（第 {round_no}/3 轮，未达到一致阈值）")

    return _get_question_n_verified(
        client=client,
        kimi_client=kimi_client,
        qwen_client=qwen_client,
        source_name=source_name,
        chunk_text=chunk_text,
        index=index,
        depth=2,
        attempt_cb=bump,
        round_cb=mark_round,
        compare_cb=compare_cb,
    )


def _collect_question_result(
    *,
    index: int,
    obj: dict[str, Any],
    err: bool,
    progress_cb: Callable[[str], None] | None,
    source_name: str,
    items: list[dict[str, Any]],
    normalized: list[Question],
    seen: set[str],
    question_cb: Callable[[Question], None] | None,
) -> None:
    if err:
        if progress_cb:
            progress_cb(f"{source_name}：第 {index} 题解析有误，已跳过。")
        return
    if not obj:
        return
    items.append(obj)
    q = _to_question(obj)
    key = _normalize_key(q.stem)
    if key and key in seen:
        return
    if key:
        seen.add(key)
    normalized.append(q)
    if question_cb:
        question_cb(q)


def _extract_questions_from_chunk(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
) -> list[dict[str, Any]]:
    sys_prompt = (
        "你是一个题库整理助手。你必须仅输出严格 JSON（不要输出任何解释/markdown/多余文本）。\n"
        "任务：从提供的资料文本中抽取选择题。\n"
        "题目可能是单选（选项标识 A/B/C/D 或 A. / A、）或多选（选项标识 ①②③④ 等）。\n"
        "如果原文没有明确答案，answer 允许为空字符串。\n"
        "不要生成解析，也不要输出任何解析字段。\n"
        "如果题目出现“组合选项”形式（例如先给出 ①②③④ 四个表述，然后给出 A/B/C/D 代表不同组合，如“ A．①② B．①④ … ”），请按以下方式转换：\n"
        "- options 只输出 ①②③④ 对应的每条表述（不要包含 A/B/C/D 这些组合项）\n"
        "- answer 必须输出圆圈数字序号组合（例如 ①④），不要输出 A/B/C/D\n"
        "不要凭空编造不存在的题目或选项；尽量忠实于原文。\n"
        "输出格式：JSON 数组，每项为对象：\n"
        "{\n"
        '  "number": "编号(可为空)",\n'
        '  "stem": "题干",\n'
        '  "options": "选项原文(包含标识，允许换行)",\n'
        '  "answer": "答案(单选如 A 或 ①；多选如 ACD 或 ①②③)"\n'
        "}\n"
    )

    user_prompt = (
        f"来源文件：{source_name}\n"
        f"导入日期：{date.today().isoformat()}\n"
        "资料文本如下：\n"
        "-----\n"
        f"{chunk_text}\n"
        "-----\n"
        "请输出严格 JSON 数组。"
    )

    data = client.chat_json(system=sys_prompt, user=user_prompt)
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for it in data:
            if isinstance(it, dict):
                out.append(it)
        return out
    return []

def _extract_questions_with_fallback(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
    depth: int,
) -> list[dict[str, Any]]:
    try:
        return _extract_questions_from_chunk(client=client, source_name=source_name, chunk_text=chunk_text)
    except Exception as e:
        msg = str(e)
        if depth <= 0 or len(chunk_text) < 1500:
            raise
        if "timed out" not in msg.lower() and "超时" not in msg:
            raise

        sub_items: list[dict[str, Any]] = []
        for sub in _split_text(chunk_text, max_chars_per_chunk=max(800, len(chunk_text) // 2)):
            sub_items.extend(
                _extract_questions_with_fallback(
                    client=client,
                    source_name=source_name,
                    chunk_text=sub,
                    depth=depth - 1,
                )
            )
        return sub_items

def _count_questions_in_chunk(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
) -> int:
    sys_prompt = (
        "你是一个题库整理助手。你必须严格按要求输出。\n"
        "任务：统计提供的资料文本中存在多少道选择题。\n"
        "只输出阿拉伯数字（例如 12），不要输出任何其他字符、标点、空格、换行。\n"
    )
    user_prompt = (
        f"来源文件：{source_name}\n"
        "资料文本如下：\n"
        "-----\n"
        f"{chunk_text}\n"
        "-----\n"
        "请只输出阿拉伯数字。"
    )
    data = client.chat_json(system=sys_prompt, user=user_prompt)
    if isinstance(data, int):
        return int(data)
    if isinstance(data, str):
        s = re.sub(r"[^0-9]", "", data)
        return int(s) if s else 0
    return 0


def _get_question_n_in_chunk(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
    index: int,
) -> dict[str, Any]:
    sys_prompt = (
        "你是一个题库整理助手。你必须仅输出严格 JSON（不要输出任何解释/markdown/多余文本）。\n"
        "任务：从提供的资料文本中抽取指定序号的选择题。\n"
        "题目可能是单选（选项标识 A/B/C/D 或 A. / A、）或多选（选项标识 ①②③④ 等）。\n"
        "如果原文没有明确答案，answer 允许为空字符串。\n"
        "不要生成解析，也不要输出任何解析字段。\n"
        "如果题目出现“组合选项”形式（例如先给出 ①②③④ 四个表述，然后给出 A/B/C/D 代表不同组合，如“ A．①② B．①④ … ”），请按以下方式转换：\n"
        "- options 只输出 ①②③④ 对应的每条表述（不要包含 A/B/C/D 这些组合项）\n"
        "- answer 必须输出圆圈数字序号组合（例如 ①④），不要输出 A/B/C/D\n"
        "输出格式：JSON 对象：\n"
        "{\n"
        '  "number": "编号(可为空)",\n'
        '  "stem": "题干",\n'
        '  "options": "选项原文(包含标识，允许换行)",\n'
        '  "answer": "答案(单选如 A 或 ①；多选如 ACD 或 ①②③)"\n'
        "}\n"
        "如果无法确定该题，请输出空对象 {}。"
    )
    user_prompt = (
        f"来源文件：{source_name}\n"
        f"请只输出第 {index} 题（从 1 开始计数）的 JSON 对象。\n"
        "资料文本如下：\n"
        "-----\n"
        f"{chunk_text}\n"
        "-----\n"
    )
    data = client.chat_json(system=sys_prompt, user=user_prompt)
    return data if isinstance(data, dict) else {}


def _count_questions_with_fallback(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
    depth: int,
) -> int:
    try:
        return _count_questions_in_chunk(client=client, source_name=source_name, chunk_text=chunk_text)
    except Exception as e:
        msg = str(e).lower()
        if depth <= 0 or len(chunk_text) < 1500:
            raise
        if "timed out" not in msg and "超时" not in msg:
            raise
        total = 0
        for sub in _split_text(chunk_text, max_chars_per_chunk=max(800, len(chunk_text) // 2)):
            total += _count_questions_with_fallback(
                client=client,
                source_name=source_name,
                chunk_text=sub,
                depth=depth - 1,
            )
        return total


def _get_question_n_with_fallback(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
    index: int,
    depth: int,
) -> dict[str, Any]:
    try:
        return _get_question_n_in_chunk(
            client=client, source_name=source_name, chunk_text=chunk_text, index=index
        )
    except Exception as e:
        msg = str(e).lower()
        if depth <= 0 or len(chunk_text) < 1500:
            raise
        if "timed out" not in msg and "超时" not in msg:
            raise
        parts = list(_split_text(chunk_text, max_chars_per_chunk=max(800, len(chunk_text) // 2)))
        consumed = 0
        for sub in parts:
            sub_count = _count_questions_with_fallback(
                client=client,
                source_name=source_name,
                chunk_text=sub,
                depth=max(0, depth - 1),
            )
            if sub_count <= 0:
                continue
            local_index = index - consumed
            if local_index < 1 or local_index > sub_count:
                consumed += sub_count
                continue
            obj = _get_question_n_with_fallback(
                client=client,
                source_name=source_name,
                chunk_text=sub,
                index=local_index,
                depth=depth - 1,
            )
            if obj:
                return obj
        return {}


def _get_question_n_verified(
    *,
    client: LlmClient,
    kimi_client: LlmClient | None,
    qwen_client: LlmClient | None,
    source_name: str,
    chunk_text: str,
    index: int,
    depth: int,
    attempt_cb: Callable[[str, int, int], None] | None = None,
    round_cb: Callable[[int, str], None] | None = None,
    compare_cb: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    if kimi_client is None or qwen_client is None:
        return {}, True, {"index": index, "accepted": False, "reason": "missing_clients"}

    last_meta: dict[str, Any] = {"index": index, "accepted": False, "reason": "inconsistent"}
    cumulative_valid_objs: list[dict[str, Any]] = []
    for round_no in range(1, 4):
        if round_cb:
            round_cb(round_no, "start")
        results: dict[str, dict[str, Any]] = {"DeepSeek": {}, "Kimi": {}, "千问": {}}
        costs_sec: dict[str, float] = {"DeepSeek": 0.0, "Kimi": 0.0, "千问": 0.0}
        with ThreadPoolExecutor(max_workers=3) as ex:
            starts: dict[str, float] = {
                "DeepSeek": time.perf_counter(),
                "Kimi": time.perf_counter(),
                "千问": time.perf_counter(),
            }
            fut_map = {
                ex.submit(
                    _safe_get_question_n_with_fallback,
                    client=client,
                    source_name=source_name,
                    chunk_text=chunk_text,
                    index=index,
                    depth=depth,
                ): ("DeepSeek", 1),
                ex.submit(
                    _safe_get_question_n_with_fallback,
                    client=kimi_client,
                    source_name=source_name,
                    chunk_text=chunk_text,
                    index=index,
                    depth=depth,
                ): ("Kimi", 2),
                ex.submit(
                    _safe_get_question_n_with_fallback,
                    client=qwen_client,
                    source_name=source_name,
                    chunk_text=chunk_text,
                    index=index,
                    depth=depth,
                ): ("千问", 3),
            }
            for fut in as_completed(fut_map):
                model_name, check_no = fut_map[fut]
                costs_sec[model_name] = round(time.perf_counter() - starts[model_name], 3)
                if attempt_cb:
                    attempt_cb(model_name, round_no, check_no)
                try:
                    results[model_name] = fut.result() or {}
                except Exception:
                    results[model_name] = {}

        obj_a = results["DeepSeek"]
        obj_b = results["Kimi"]
        obj_c = results["千问"]
        round_valid_objs = [obj for obj in [obj_a, obj_b, obj_c] if _is_valid_question_obj(obj)]
        _, round_matched_count = _pick_consensus_obj(round_valid_objs, 1)
        cumulative_valid_objs.extend(round_valid_objs)
        required_count = round_no + 1
        accepted_obj, matched_count = _pick_consensus_obj(cumulative_valid_objs, required_count)
        last_meta = {
            "index": index,
            "round": round_no,
            "deepseek": _normalize_question_obj_for_view(obj_a),
            "kimi": _normalize_question_obj_for_view(obj_b),
            "qwen": _normalize_question_obj_for_view(obj_c),
            "deepseek_sec": costs_sec["DeepSeek"],
            "kimi_sec": costs_sec["Kimi"],
            "qwen_sec": costs_sec["千问"],
            "round_matched_count": round_matched_count,
            "round_valid_count": len(round_valid_objs),
            "required_count": required_count,
            "matched_count": matched_count,
            "valid_count": len(cumulative_valid_objs),
            "accepted": False,
            "reason": "inconsistent",
        }
        if compare_cb is not None:
            compare_cb(last_meta)
        if accepted_obj:
            if round_cb:
                round_cb(round_no, "consistent")
            accepted_obj = _normalize_question_obj_for_view(accepted_obj)
            last_meta["accepted"] = True
            last_meta["reason"] = "consistent"
            last_meta["accepted_obj"] = accepted_obj
            if compare_cb is not None:
                compare_cb(last_meta)
            return accepted_obj, False, last_meta
        if round_cb:
            round_cb(round_no, "inconsistent")
    return {}, True, last_meta


def _safe_get_question_n_with_fallback(
    *,
    client: LlmClient,
    source_name: str,
    chunk_text: str,
    index: int,
    depth: int,
) -> dict[str, Any]:
    try:
        return _get_question_n_with_fallback(
            client=client,
            source_name=source_name,
            chunk_text=chunk_text,
            index=index,
            depth=depth,
        )
    except Exception:
        return {}


def _to_question(obj: dict[str, Any]) -> Question:
    number = _as_str(obj.get("number", ""))
    stem = _as_str(obj.get("stem", ""))
    number, stem = _split_number_and_stem(number, stem)
    options = obj.get("options", "")
    if isinstance(options, dict):
        options_str = json.dumps(
            _normalize_options_dict(options),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        options_str = _options_to_string(options)
    answer = _as_str(obj.get("answer", ""))
    analysis = ""
    q = Question(number=number, stem=stem, options=options_str, answer=answer, analysis=analysis)
    return _normalize_combination_question(q)


def _normalize_combination_question(q: Question) -> Question:
    combined = "\n".join([q.stem or "", q.options or ""]).strip()
    if not combined:
        return q

    statements = _extract_circled_statements(combined)
    combos = _extract_combo_map(combined)
    if len(statements) < 3 or len(combos) < 2:
        return q

    answer = (q.answer or "").strip().replace(" ", "")
    if answer and _LETTER_ONLY_RE.fullmatch(answer):
        selected: set[str] = set()
        for ch in answer:
            digits = combos.get(ch, "")
            for d in _CIRCLED_RE.findall(digits):
                selected.add(d)
        answer = _sort_circled("".join(selected))
    else:
        if _CIRCLED_RE.search(answer):
            answer = _sort_circled("".join(_CIRCLED_RE.findall(answer)))

    new_options = "\n".join(statements).strip()
    new_stem = q.stem
    first = _CIRCLED_RE.search(new_stem or "")
    if first is not None:
        prefix = (new_stem or "")[: first.start()].strip()
        if prefix:
            new_stem = prefix

    return Question(
        number=q.number,
        stem=new_stem,
        options=new_options,
        answer=answer,
        analysis=q.analysis,
    )


def _extract_combo_map(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _COMBO_RE.finditer(text):
        letter = m.group(1)
        digits = "".join(_CIRCLED_RE.findall(m.group(2)))
        if digits:
            out[letter] = digits
    return out


def _extract_circled_statements(text: str) -> list[str]:
    circled = list(_CIRCLED_RE.finditer(text))
    if not circled:
        return []

    first_combo = _COMBO_RE.search(text)
    combo_start = first_combo.start() if first_combo is not None else len(text)

    out: list[str] = []
    for i, m in enumerate(circled):
        start = m.start()
        if start >= combo_start:
            break
        end = combo_start
        if i + 1 < len(circled):
            end = min(end, circled[i + 1].start())
        seg = text[m.end() : end].strip()
        if seg:
            out.append(f"{m.group(0)}{seg}")
    return out


def _sort_circled(text: str) -> str:
    order = [chr(c) for c in range(0x2460, 0x2474)]
    present = set(_CIRCLED_RE.findall(text))
    return "".join([c for c in order if c in present])


def _split_text(text: str, *, max_chars_per_chunk: int) -> Iterable[str]:
    if len(text) <= max_chars_per_chunk:
        yield text
        return

    lines = text.splitlines()
    buf: list[str] = []
    size = 0
    for line in lines:
        ln = line.rstrip()
        if not ln:
            if buf:
                buf.append("")
                size += 1
            continue
        if size + len(ln) + 1 > max_chars_per_chunk and buf:
            yield "\n".join(buf).strip() + "\n"
            buf = []
            size = 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        yield "\n".join(buf).strip() + "\n"


def _normalize_key(stem: str) -> str:
    s = stem.strip()
    s = s.replace("\r", "\n")
    s = _WS_RE.sub(" ", s)
    return s.strip().lower()


def _as_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def _all_same(objs: list[dict[str, Any]]) -> bool:
    if not objs:
        return False
    fp0 = _fingerprint_question_obj(objs[0])
    return all(_fingerprint_question_obj(o) == fp0 for o in objs[1:])


def _pick_consensus_obj(objs: list[dict[str, Any]], min_count: int) -> tuple[dict[str, Any], int]:
    if min_count <= 0:
        return {}, 0
    counter: dict[str, tuple[int, dict[str, Any]]] = {}
    for obj in objs:
        fp = _fingerprint_question_obj(obj)
        if not fp:
            continue
        if fp not in counter:
            counter[fp] = (1, obj)
            continue
        count, sample = counter[fp]
        counter[fp] = (count + 1, sample)
    best_obj: dict[str, Any] = {}
    best_count = 0
    for count, sample in counter.values():
        if count > best_count:
            best_count = count
            best_obj = sample
    if best_count >= min_count:
        return best_obj, best_count
    return {}, best_count


def _is_valid_question_obj(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    stem = _normalize_text(_as_str(obj.get("stem", "")))
    options = obj.get("options", "")
    if isinstance(options, dict):
        opt_text = json.dumps(_normalize_options_dict(options), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        opt_text = _normalize_text(_options_to_string(options))
    return bool(stem and opt_text)


def _normalize_options_dict(d: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        ks = _as_str(k)
        vs = _normalize_text(_as_str(v))
        if ks and vs:
            out[ks] = vs
    return out


def _options_to_string(v: Any) -> str:
    if isinstance(v, list):
        lines = [_as_str(it) for it in v]
        return "\n".join([ln for ln in lines if ln]).strip()
    if isinstance(v, tuple):
        lines = [_as_str(it) for it in v]
        return "\n".join([ln for ln in lines if ln]).strip()
    if isinstance(v, str):
        parsed = _parse_options_list_text(v)
        if parsed is not None:
            return _options_to_string(parsed)
    return _as_str(v)


def _parse_options_list_text(text: str) -> list[Any] | None:
    s = _as_str(text)
    if not (s.startswith("[") and s.endswith("]")):
        return None
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    try:
        data = ast.literal_eval(s)
        if isinstance(data, list):
            return data
    except Exception:
        return None
    return None


def _split_number_and_stem(number: str, stem: str) -> tuple[str, str]:
    num = _as_str(number)
    s = _as_str(stem)
    if not s:
        return num, s
    for pat in _STEM_NUMBER_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        candidate_num = _as_str(m.group(1))
        candidate_stem = _as_str(m.group(2))
        if not candidate_stem:
            continue
        if not num:
            return candidate_num, candidate_stem
        if num == candidate_num:
            return num, candidate_stem
    return num, s


def _normalize_question_obj_for_view(obj: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(obj, dict) or not obj:
        return {}
    out = dict(obj)
    number, stem = _split_number_and_stem(_as_str(out.get("number", "")), _as_str(out.get("stem", "")))
    out["number"] = number
    out["stem"] = stem
    options = out.get("options", "")
    if isinstance(options, dict):
        out["options"] = _normalize_options_dict(options)
    else:
        out["options"] = _options_to_string(options)
    out["answer"] = _as_str(out.get("answer", ""))
    return out


def _normalize_text(text: str) -> str:
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n")]
    s = "\n".join([ln for ln in lines if ln])
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _fingerprint_question_obj(obj: dict[str, Any]) -> str:
    if not isinstance(obj, dict):
        return ""
    stem = _normalize_text(_as_str(obj.get("stem", "")))
    answer = _as_str(obj.get("answer", "")).replace(" ", "").upper()
    options = obj.get("options", "")
    if isinstance(options, dict):
        options_norm: Any = _normalize_options_dict(options)
    else:
        options_norm = _normalize_text(_options_to_string(options))
    payload = {"stem": stem, "options": options_norm, "answer": answer}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sj_generator.ai.client import LlmClient


@dataclass(frozen=True)
class ExplanationInputs:
    question_text: str
    answer_text: str
    reference_md_paths: list[Path] | None = None
    include_common_mistakes: bool = True
    root_dir: Path | None = None


_NONEMPTY_LINE_RE = re.compile(r".*\S.*")


def generate_explanation(client: LlmClient, inp: ExplanationInputs) -> str:
    reference_text = ""
    if inp.reference_md_paths:
        reference_text = _read_reference_md_text(inp.reference_md_paths)

    mistakes_text = ""
    if inp.include_common_mistakes and inp.root_dir is not None:
        md = inp.root_dir / "common_mistakes" / "选择题常见错题归因与答题策略分析.md"
        if md.exists():
            mistakes_text = _read_text_limited(md, max_chars=12000)

    system = (
        "你是一个专业的教育助手，擅长讲解选择题。请严格遵循用户的格式要求。"
    )

    user = _build_user_prompt(
        question_text=inp.question_text,
        answer_text=inp.answer_text,
        reference_md_paths=inp.reference_md_paths,
        reference_text=reference_text,
        mistakes_text=mistakes_text,
    )

    raw = client.chat_text(system=system, user=user)
    return postprocess_explanation(raw)


def _build_user_prompt(
    *,
    question_text: str,
    answer_text: str,
    reference_md_paths: list[Path] | None,
    reference_text: str,
    mistakes_text: str,
) -> str:
    parts: list[str] = []

    parts.append("请严格按照以下格式输出解析：")
    parts.append("每行分析一个选项。")
    parts.append("必须沿用题目中出现的选项标识（例如 A/B/C/D 或 ①/②/③/④），不要擅自改成另一种编号方式。")
    parts.append("如果是错误选项：必须先指出“具体的错误原因类型”（可以多个，用中文分号“；”分隔），然后再做详细分析。")
    parts.append("如果是正确选项：必须标记为“正确”，并说明理由。")
    parts.append("同一选项允许多个错误原因，需要一并列出。")
    parts.append("")
    parts.append("示例格式：")
    parts.append("- A：**知识模块归类错误** ...")
    parts.append("- B：**正确** ...")
    parts.append("- C：**偷换概念；范围扩大** ...")
    parts.append("或：")
    parts.append("- ①：**知识模块归类错误** ...")
    parts.append("- ②：**正确** ...")
    parts.append("- ③：**偷换概念；范围扩大** ...")
    parts.append("")

    parts.append("题目文本：")
    parts.append(question_text.strip())
    parts.append("")
    parts.append("答案文本：")
    parts.append(answer_text.strip())

    if reference_text.strip():
        parts.append("")
        if reference_md_paths:
            names = "、".join([p.name for p in reference_md_paths])
            parts.append(f"参考资料（{len(reference_md_paths)} 份：{names}）：")
        else:
            parts.append("参考资料：")
        parts.append(reference_text.strip())

    if mistakes_text.strip():
        parts.append("")
        parts.append("常见错题归因与答题策略参考（md 原文）：")
        parts.append(mistakes_text.strip())

    return "\n".join(parts).strip()


def _read_reference_md_text(paths: list[Path]) -> str:
    max_chars = 30000
    blocks: list[str] = []
    used = 0
    for p in paths:
        try:
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not text:
            continue
        head = f"[文件：{p.name}]"
        block = head + "\n" + text
        if used + len(block) + 2 > max_chars:
            remain = max_chars - used
            if remain <= len(head) + 1:
                break
            block = head + "\n" + block[len(head) + 1 : len(head) + 1 + remain - len(head) - 1].rstrip() + "\n…"
            blocks.append(block)
            break
        blocks.append(block)
        used += len(block) + 2
    return "\n\n".join(blocks).strip()


def postprocess_explanation(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if _NONEMPTY_LINE_RE.fullmatch(ln)]
    cleaned: list[str] = []
    for ln in lines:
        s = ln.lstrip()
        if s.startswith("- "):
            s = s[2:]
        elif s.startswith("-"):
            s = s[1:].lstrip()
        cleaned.append(s)
    return "\n".join(cleaned).strip()

def _read_common_mistakes_md(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    rows = [ln for ln in lines if ln.startswith("|") and ln.count("|") >= 4]
    out: list[tuple[str, str]] = []
    for ln in rows:
        if ":---" in ln or ":--" in ln:
            continue
        parts = [p.strip() for p in ln.strip("|").split("|")]
        if len(parts) < 2:
            continue
        a_raw = parts[0]
        b_raw = parts[1] if len(parts) > 1 else ""
        a = _strip_md_inline(a_raw)
        b = _strip_md_inline(b_raw)
        a = a.replace("\n", " ").strip()
        b = b.replace("\n", " ").strip()
        if not a:
            continue
        if a in ("错题表现 (常见错误现象)", "错题表现", "表现"):
            continue
        title, detail = _extract_type_and_detail(a)
        if title and detail:
            out.append((title, detail))
        elif title:
            out.append((title, b))
        else:
            out.append((a, b))
    return out


def _strip_md_inline(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"<br\\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"\\*\\*(.*?)\\*\\*", r"\\1", s)
    s = re.sub(r"\\*(.*?)\\*", r"\\1", s)
    s = re.sub(r"`([^`]*)`", r"\\1", s)
    return s.strip()


def _extract_type_and_detail(text: str) -> tuple[str, str]:
    s = (text or "").strip()
    if not s:
        return "", ""
    m = re.search(r"(?:^|\\s)([\\u4e00-\\u9fffA-Za-z0-9]+型)(?:\\s|$)", s)
    title = ""
    if m:
        title = m.group(1).strip()
    if "\n" in s:
        first, rest = s.split("\n", 1)
    else:
        first, rest = s, ""
    if not title:
        title = first.strip()
    detail = (rest or "").strip()
    detail = re.sub(r"\\s+", " ", detail)
    if len(detail) > 220:
        detail = detail[:220].rstrip() + "…"
    return title, detail


def _read_text_limited(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n…"

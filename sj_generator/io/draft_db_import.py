from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sj_generator.io.excel_repo import try_parse_options_json
from sj_generator.io.sqlite_repo import DbQuestionRecord, append_questions
from sj_generator.models import Question

LETTER_MARKER_RE = re.compile(r"(?<!\n)(?=(?:[A-D][\.、．]))")
CIRCLED_MARKER_RE = re.compile(r"(?<!\n)(?=(?:[\u2460-\u2473]))")
LETTER_LINE_RE = re.compile(r"^([A-D])[\.\u3001\uFF0E]\s*(.*)$")
CIRCLED_LINE_RE = re.compile(r"^([\u2460-\u2473])\s*(.*)$")
LETTER_ONLY_RE = re.compile(r"^[A-D]+$")
DIGIT_TOKEN_RE = re.compile(r"\d+")
CIRCLED_CHAR_TO_INDEX = {
    "①": "1",
    "②": "2",
    "③": "3",
    "④": "4",
    "⑤": "5",
    "⑥": "6",
    "⑦": "7",
    "⑧": "8",
    "⑨": "9",
    "⑩": "10",
}


@dataclass(frozen=True)
class ParsedQuestion:
    stem: str
    options: list[str]


def import_draft_questions_to_db(
    *,
    db_path: Path,
    questions: list[Question],
    level_path: str,
    source_files: Iterable[Path] | None = None,
) -> int:
    records = draft_questions_to_db_records(
        questions=questions,
        level_path=level_path,
        source_files=source_files,
    )
    append_questions(db_path, records)
    return len(records)


def draft_questions_to_db_records(
    *,
    questions: list[Question],
    level_path: str,
    source_files: Iterable[Path] | None = None,
) -> list[DbQuestionRecord]:
    normalized_level_path = (level_path or "").strip()
    if not normalized_level_path:
        raise ValueError("level_path 不能为空。")
    if not questions:
        raise ValueError("没有可写入数据库的题目。")

    source_filename = _resolve_source_filename(source_files)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        _convert_question(
            question,
            source_filename=source_filename,
            level_path=normalized_level_path,
            now_text=now_text,
        )
        for question in questions
    ]


def _resolve_source_filename(source_files: Iterable[Path] | None) -> str:
    if source_files is None:
        return ""
    names: list[str] = []
    seen: set[str] = set()
    for path in source_files:
        name = path.name.strip()
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "; ".join(names)


def _convert_question(
    question: Question,
    *,
    source_filename: str,
    level_path: str,
    now_text: str,
) -> DbQuestionRecord:
    parsed = _parse_question_and_options(question)
    question_type = _detect_question_type(parsed.options, question.answer)
    answer = _normalize_answer(question.answer, question_type)
    option_values = (parsed.options + ["", "", "", ""])[:4]
    return DbQuestionRecord(
        id=str(uuid.uuid4()),
        stem=parsed.stem,
        option_1=option_values[0],
        option_2=option_values[1],
        option_3=option_values[2],
        option_4=option_values[3],
        answer=answer,
        analysis=(question.analysis or "").strip(),
        question_type=question_type,
        textbook_version="",
        source_filename=source_filename,
        level_path=level_path,
        difficulty_score=None,
        knowledge_points="",
        abilities="",
        created_at=now_text,
        updated_at=now_text,
    )


def _parse_question_and_options(question: Question) -> ParsedQuestion:
    parsed_json = try_parse_options_json(question.options or "")
    if parsed_json is not None:
        keys = sorted(parsed_json.keys())
        return ParsedQuestion(
            stem=(question.stem or "").strip(),
            options=[(parsed_json.get(key) or "").strip() for key in keys],
        )

    combined = _join_stem_and_options(question.stem, question.options)
    normalized = _force_newline_before_markers(combined)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]

    stem_lines: list[str] = []
    options: list[str] = []
    current_option_parts: list[str] | None = None
    for line in lines:
        letter_match = LETTER_LINE_RE.match(line)
        circled_match = CIRCLED_LINE_RE.match(line)
        if letter_match:
            if current_option_parts is not None:
                options.append(" ".join(part for part in current_option_parts if part).strip())
            current_option_parts = [letter_match.group(2).strip()]
            continue
        if circled_match:
            if current_option_parts is not None:
                options.append(" ".join(part for part in current_option_parts if part).strip())
            current_option_parts = [circled_match.group(2).strip()]
            continue
        if current_option_parts is None:
            stem_lines.append(line)
        else:
            current_option_parts.append(line)

    if current_option_parts is not None:
        options.append(" ".join(part for part in current_option_parts if part).strip())

    if options:
        return ParsedQuestion(stem="\n".join(stem_lines).strip(), options=options)
    return ParsedQuestion(stem=(combined or "").strip(), options=[])


def _join_stem_and_options(stem: str, options: str) -> str:
    stem = (stem or "").strip()
    options = (options or "").strip()
    if stem and options:
        return f"{stem}\n{options}"
    return stem or options


def _force_newline_before_markers(text: str) -> str:
    if not text.strip():
        return ""
    text = CIRCLED_MARKER_RE.sub("\n", text)
    return LETTER_MARKER_RE.sub("\n", text)


def _detect_question_type(options: list[str], answer: str) -> str:
    compact_answer = _compact_answer(answer)
    if _is_multi_answer(compact_answer):
        return "多选"
    if _is_convertible_multi(options):
        return "可转多选"
    return "单选"


def _is_multi_answer(answer: str) -> bool:
    if "," in answer:
        numbers = [part.strip() for part in answer.split(",") if part.strip()]
        return len(numbers) > 1
    if LETTER_ONLY_RE.fullmatch(answer):
        return len(answer) > 1
    circled_digits = [CIRCLED_CHAR_TO_INDEX[ch] for ch in answer if ch in CIRCLED_CHAR_TO_INDEX]
    return len(circled_digits) > 1


def _is_convertible_multi(options: list[str]) -> bool:
    if len(options) < 2:
        return False
    circled_leading_count = 0
    for option in options:
        text = option.strip()
        if text and text[0] in CIRCLED_CHAR_TO_INDEX:
            circled_leading_count += 1
    return circled_leading_count >= 2


def _normalize_answer(answer: str, question_type: str) -> str:
    compact = _compact_answer(answer)
    if question_type != "多选":
        return compact

    if LETTER_ONLY_RE.fullmatch(compact):
        digits = [str(ord(ch) - ord("A") + 1) for ch in compact]
        return ",".join(digits)

    circled_digits = [CIRCLED_CHAR_TO_INDEX[ch] for ch in compact if ch in CIRCLED_CHAR_TO_INDEX]
    if circled_digits:
        return ",".join(circled_digits)

    digits = DIGIT_TOKEN_RE.findall(compact)
    if digits:
        return ",".join(digits)
    return compact


def _compact_answer(answer: str) -> str:
    return (answer or "").strip().replace(" ", "").replace("，", ",").replace("；", ",").replace(";", ",")

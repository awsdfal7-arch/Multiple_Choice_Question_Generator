from __future__ import annotations

from datetime import date

from openpyxl import Workbook, load_workbook

from sj_generator.ai import import_questions as iq
from sj_generator.ai.import_questions import ImportResult
from sj_generator.io import batch_ai_import as batch_ai
from sj_generator.io.batch_folderize import process_excel_to_folder_mode
from sj_generator.io.export_md import _normalize_numbers
from sj_generator.models import Question
from sj_generator import config as cfg_mod
from sj_generator.ui.compare_highlight import compare_highlight_model_keys


def test_normalize_numbers_fill_after_existing_max() -> None:
    questions = [
        Question(number="5", stem="s1", options="", answer="", analysis=""),
        Question(number="", stem="s2", options="", answer="", analysis=""),
        Question(number="7", stem="s3", options="", answer="", analysis=""),
        Question(number="", stem="s4", options="", answer="", analysis=""),
    ]
    normalized = _normalize_numbers(questions)
    assert [q.number for q in normalized] == ["5", "8", "7", "9"]


def test_get_question_n_with_fallback_maps_to_local_index(monkeypatch) -> None:
    root_text = "root\n" + ("x" * 2000)

    def fake_split(text: str, *, max_chars_per_chunk: int):
        if text == root_text:
            return ["sub1", "sub2"]
        return [text]

    def fake_count(*, client, source_name: str, chunk_text: str, depth: int) -> int:
        if chunk_text == "sub1":
            return 2
        if chunk_text == "sub2":
            return 1
        return 0

    calls: list[tuple[str, int]] = []

    def fake_get_in_chunk(*, client, source_name: str, chunk_text: str, index: int):
        calls.append((chunk_text, index))
        if chunk_text == root_text:
            raise TimeoutError("timed out")
        if chunk_text == "sub2" and index == 1:
            return {"number": "3", "stem": "ok"}
        return {}

    monkeypatch.setattr(iq, "_split_text", fake_split)
    monkeypatch.setattr(iq, "_count_questions_with_fallback", fake_count)
    monkeypatch.setattr(iq, "_get_question_n_in_chunk", fake_get_in_chunk)

    got = iq._get_question_n_with_fallback(
        client=object(),
        source_name="src",
        chunk_text=root_text,
        index=3,
        depth=2,
    )

    assert got.get("number") == "3"
    assert ("sub2", 1) in calls


def test_config_path_defaults_to_appdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("SJ_GENERATOR_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SJ_GENERATOR_KIMI_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SJ_GENERATOR_QWEN_CONFIG_PATH", raising=False)

    assert cfg_mod._config_path() == tmp_path / "sj_generator" / "deepseek.json"
    assert cfg_mod._kimi_config_path() == tmp_path / "sj_generator" / "kimi.json"
    assert cfg_mod._qwen_config_path() == tmp_path / "sj_generator" / "qwen.json"


def test_deepseek_analysis_model_defaults_and_persists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("SJ_GENERATOR_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DEEPSEEK_ANALYSIS_MODEL", raising=False)

    cfg = cfg_mod.load_deepseek_config()
    assert cfg.analysis_model == "deepseek-reasoner"

    updated = cfg_mod.DeepSeekConfig(
        base_url=cfg.base_url,
        api_key="sk-test",
        model="deepseek-chat",
        analysis_model="deepseek-reasoner",
        timeout_s=cfg.timeout_s,
    )
    cfg_mod.save_deepseek_config(updated)
    reloaded = cfg_mod.load_deepseek_config()
    assert reloaded.analysis_model == "deepseek-reasoner"


def test_compare_highlight_marks_minority_model() -> None:
    got = compare_highlight_model_keys(
        model_sigs={"deepseek": '{"answer":"A"}', "kimi": '{"answer":"A"}', "qwen": '{"answer":"B"}'},
        round_no=1,
        round_matched_count=2,
    )
    assert got == {"qwen"}


def test_compare_highlight_marks_all_when_first_round_failed() -> None:
    got = compare_highlight_model_keys(
        model_sigs={"deepseek": '{"answer":"A"}', "kimi": "", "qwen": '{"answer":"B"}'},
        round_no=1,
        round_matched_count=1,
    )
    assert got == {"deepseek", "kimi", "qwen"}


def test_compare_highlight_skips_when_first_round_all_passed() -> None:
    got = compare_highlight_model_keys(
        model_sigs={"deepseek": '{"answer":"A"}', "kimi": '{"answer":"A"}', "qwen": '{"answer":"A"}'},
        round_no=1,
        round_matched_count=3,
    )
    assert got == set()


def test_compare_highlight_skips_non_first_round() -> None:
    got = compare_highlight_model_keys(
        model_sigs={"deepseek": '{"answer":"A"}', "kimi": "", "qwen": '{"answer":"A"}'},
        round_no=2,
        round_matched_count=2,
    )
    assert got == set()


def test_process_excel_to_folder_mode_supports_legacy_headers(tmp_path) -> None:
    src = tmp_path / "1.1.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "questions"
    ws.append(["编号", "题目", "答案", "解析", "提取日期"])
    ws.append([1, "题目一\nA. 甲\nB. 乙", "A", "解析一", "2026-03-31"])
    wb.save(src)

    result = process_excel_to_folder_mode(src, export_date=date(2026, 3, 31))

    assert result.target_dir == tmp_path / "1.1"
    assert result.target_xlsx.exists()
    assert result.target_md.exists()

    saved = load_workbook(result.target_xlsx)
    ws2 = saved["questions"]
    assert [c.value for c in next(ws2.iter_rows(min_row=1, max_row=1))] == ["编号", "题目", "选项", "答案", "解析"]
    assert ws2.cell(2, 3).value in ("", None)


def test_process_source_files_to_folders_outputs_xlsx_and_md(monkeypatch, tmp_path) -> None:
    src = tmp_path / "资料一.txt"
    src.write_text("原始资料", encoding="utf-8")

    monkeypatch.setattr(batch_ai, "read_source_text", lambda path: "资料正文")
    monkeypatch.setattr(batch_ai, "generate_explanation", lambda client, inp: "解析一")
    monkeypatch.setattr(
        batch_ai,
        "import_questions_from_sources",
        lambda **kwargs: ImportResult(
            questions=[Question(number="1", stem="题目一", options="A.甲\nB.乙", answer="A", analysis="")],
            raw_items=[],
        ),
    )

    messages: list[str] = []
    results = batch_ai.process_source_files_to_folders(
        paths=[src],
        client=object(),
        kimi_client=object(),
        qwen_client=object(),
        export_date=date(2026, 3, 31),
        progress_cb=messages.append,
    )

    assert len(results) == 1
    result = results[0]
    assert result.target_dir == tmp_path / "资料一"
    assert result.target_xlsx.exists()
    assert result.target_md.exists()
    assert result.question_count == 1
    assert any("AI 解析准备中" in msg or "统计题数" in msg for msg in messages)
    saved = load_workbook(result.target_xlsx)
    ws = saved["questions"]
    assert ws.cell(2, 5).value == "解析一"

    progress_events: list[batch_ai.BatchAiProgress] = []
    batch_ai.process_source_files_to_folders(
        paths=[src],
        client=object(),
        kimi_client=object(),
        qwen_client=object(),
        export_date=date(2026, 3, 31),
        progress_info_cb=progress_events.append,
    )
    assert any(event.stage == "reading" for event in progress_events)
    assert any(event.stage == "generating_analysis" for event in progress_events)
    assert any(event.stage == "done" and event.question_count == 1 for event in progress_events)


def test_process_source_files_to_folders_supports_controlled_concurrency(monkeypatch, tmp_path) -> None:
    src1 = tmp_path / "资料一.txt"
    src2 = tmp_path / "资料二.txt"
    src1.write_text("原始资料一", encoding="utf-8")
    src2.write_text("原始资料二", encoding="utf-8")

    monkeypatch.setattr(batch_ai, "read_source_text", lambda path: f"{path.stem} 正文")
    monkeypatch.setattr(batch_ai, "generate_explanation", lambda client, inp: "解析一")
    monkeypatch.setattr(
        batch_ai,
        "import_questions_from_sources",
        lambda **kwargs: ImportResult(
            questions=[Question(number="1", stem="题目一", options="A.甲\nB.乙", answer="A", analysis="")],
            raw_items=[],
        ),
    )

    created: list[str] = []

    def make_client(tag: str):
        def factory():
            created.append(tag)
            return object()

        return factory

    results = batch_ai.process_source_files_to_folders(
        paths=[src1, src2],
        client_factory=make_client("deepseek"),
        analysis_client_factory=make_client("analysis"),
        kimi_client_factory=make_client("kimi"),
        qwen_client_factory=make_client("qwen"),
        max_workers=2,
        export_date=date(2026, 3, 31),
    )

    assert [item.target_dir.name for item in results] == ["资料一", "资料二"]
    assert len(created) == 8


def test_process_source_files_to_folders_supports_analysis_concurrency(monkeypatch, tmp_path) -> None:
    src = tmp_path / "资料一.txt"
    src.write_text("原始资料", encoding="utf-8")

    monkeypatch.setattr(batch_ai, "read_source_text", lambda path: "资料正文")
    monkeypatch.setattr(
        batch_ai,
        "import_questions_from_sources",
        lambda **kwargs: ImportResult(
            questions=[
                Question(number="1", stem="题目一", options="A.甲\nB.乙", answer="A", analysis=""),
                Question(number="2", stem="题目二", options="A.甲\nB.乙", answer="B", analysis=""),
            ],
            raw_items=[],
        ),
    )
    monkeypatch.setattr(batch_ai, "generate_explanation", lambda client, inp: f"解析-{inp.answer_text}")

    created: list[str] = []

    def make_client():
        created.append("deepseek")
        return object()

    results = batch_ai.process_source_files_to_folders(
        paths=[src],
        client=object(),
        kimi_client=object(),
        qwen_client=object(),
        client_factory=make_client,
        max_analysis_workers=2,
        export_date=date(2026, 3, 31),
    )

    assert len(results) == 1
    saved = load_workbook(results[0].target_xlsx)
    ws = saved["questions"]
    assert ws.cell(2, 5).value == "解析-A"
    assert ws.cell(3, 5).value == "解析-B"
    assert len(created) == 2


def test_import_questions_from_sources_supports_question_level_concurrency(monkeypatch, tmp_path) -> None:
    src = tmp_path / "资料一.txt"
    src.write_text("原始资料", encoding="utf-8")

    monkeypatch.setattr(iq, "_count_questions_with_fallback", lambda **kwargs: 3)
    monkeypatch.setattr(
        iq,
        "_process_one_question",
        lambda **kwargs: (
            {"number": str(kwargs["index"]), "stem": f"题目{kwargs['index']}", "options": "", "answer": "A"},
            False,
            {},
        ),
    )

    created: list[str] = []

    def make_client(tag: str):
        def factory():
            created.append(tag)
            return object()

        return factory

    result = iq.import_questions_from_sources(
        client=object(),
        kimi_client=object(),
        qwen_client=object(),
        client_factory=make_client("deepseek"),
        kimi_client_factory=make_client("kimi"),
        qwen_client_factory=make_client("qwen"),
        sources=[(src, "资料正文")],
        strategy="per_question",
        max_question_workers=2,
    )

    assert [q.number for q in result.questions] == ["1", "2", "3"]
    assert len(created) == 9

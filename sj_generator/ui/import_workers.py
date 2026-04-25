from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from sj_generator.infrastructure.llm.client import LlmClient
from sj_generator.infrastructure.llm.import_questions import import_questions_from_sources, question_content_llm_config
from sj_generator.infrastructure.llm.question_ref_scan import resolve_question_refs_with_scan
from sj_generator.infrastructure.document.source_reader import read_source_text
from sj_generator.domain.entities import Question
from sj_generator.application.state import normalize_ai_concurrency


class AiQuestionRefWorker(QObject):
    progress = pyqtSignal(str)
    scan_progress = pyqtSignal(object)
    compare = pyqtSignal(object)
    progress_count = pyqtSignal(int, int)
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, *, cfg, paths: list[Path]) -> None:
        super().__init__()
        self._cfg = cfg
        self._paths = paths
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            sources: list[tuple[Path, str]] = []
            for p in self._paths:
                if p.suffix.lower() != ".docx":
                    raise RuntimeError(f"当前仅支持 Word 文档导入：{p.name}")
                sources.append((p, read_source_text(p)))

            result = resolve_question_refs_with_scan(
                sources=sources,
                progress_cb=self.progress.emit,
                compare_cb=self.compare.emit,
                scan_progress_cb=self.scan_progress.emit,
                progress_count_cb=self.progress_count.emit,
                stop_cb=self._should_stop,
            )
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _should_stop(self) -> bool:
        return self._stop


class AiImportContentWorker(QObject):
    progress = pyqtSignal(str)
    question = pyqtSignal(object)
    compare = pyqtSignal(object)
    progress_count = pyqtSignal(int, int)
    done = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        model_specs: list[dict[str, str]],
        paths: list[Path],
        question_refs_by_source: dict[str, list[dict[str, str]]],
        strategy: str,
        max_question_workers: int,
    ) -> None:
        super().__init__()
        self._model_specs = [item for item in model_specs if isinstance(item, dict)]
        self._paths = paths
        self._question_refs_by_source = question_refs_by_source
        self._strategy = strategy
        self._max_question_workers = normalize_ai_concurrency(max_question_workers)
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            sources: list[tuple[Path, str]] = []
            for p in self._paths:
                if p.suffix.lower() != ".docx":
                    raise RuntimeError(f"当前仅支持 Word 文档导入：{p.name}")
                sources.append((p, read_source_text(p)))

            client_factories = {
                str(spec.get("key") or ""): (
                    lambda provider=str(spec.get("provider") or ""), model_name=str(spec.get("model_name") or ""): LlmClient(
                        question_content_llm_config(provider, model_name)
                    )
                )
                for spec in self._model_specs
                if str(spec.get("key") or "").strip()
            }
            result = import_questions_from_sources(
                model_specs=self._model_specs,
                client_factories=client_factories,
                sources=sources,
                strategy=self._strategy,
                max_question_workers=self._max_question_workers,
                progress_cb=self.progress.emit,
                question_cb=self._emit_question,
                compare_cb=self._emit_compare,
                progress_count_cb=self.progress_count.emit,
                stop_cb=self._should_stop,
                question_refs_by_source=self._question_refs_by_source,
            )
            self.done.emit(len(result.questions))
        except Exception as e:
            self.error.emit(str(e))

    def _emit_question(self, q: Question) -> None:
        self.question.emit(q)

    def _emit_compare(self, payload: dict) -> None:
        self.compare.emit(payload)

    def _should_stop(self) -> bool:
        return self._stop

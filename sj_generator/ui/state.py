from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sj_generator.io.dedupe import DedupeHit
from sj_generator.models import Question

AI_CONCURRENCY_OPTIONS = (1, 3, 5)


def normalize_ai_concurrency(value: int | None) -> int:
    try:
        workers = int(value or 0)
    except Exception:
        workers = 0
    return workers if workers in AI_CONCURRENCY_OPTIONS else 3


@dataclass
class WizardState:
    project_dir: Optional[Path] = None
    repo_path: Optional[Path] = None
    last_export_dir: Optional[Path] = None
    start_mode: str = "wizard"
    project_name_is_placeholder: bool = False
    input_mode: str = "ai"
    batch_source_files: Optional[list[Path]] = None
    batch_source_files_text: str = ""
    ai_source_files: Optional[list[Path]] = None
    ai_source_files_text: str = ""
    ai_import_questions: Optional[list[Question]] = None
    dedupe_hits: Optional[list[DedupeHit]] = None
    dedupe_enabled: bool = True
    dedupe_folder: Optional[Path] = None
    dedupe_threshold: float = 0.85
    analysis_enabled: bool = True
    analysis_use_reference_folder: bool = True
    analysis_use_reference_md: bool = False
    analysis_reference_md_path_text: str = ""
    analysis_include_common_mistakes: bool = True
    ai_concurrency: int = 3

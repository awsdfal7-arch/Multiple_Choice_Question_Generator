from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path

    @property
    def docs_dir(self) -> Path:
        return self.base_dir / "docs"


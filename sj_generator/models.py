from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    number: str
    stem: str
    options: str
    answer: str
    analysis: str


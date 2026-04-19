from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sj_generator.ai.import_questions import _normalize_combination_question
from sj_generator.models import Question


def main() -> None:
    q = Question(
        number="",
        stem="下列说法正确的是（ ） ①强化党参加和管理金融工作的职责 ②金融机构提升自身化解风险的能力 ③不断健全机制以形成金融监管合力 ④将制度优势转化为金融治理的效能",
        options="A． ① ② B． ① ④ C． ② ③ D． ③ ④",
        answer="B",
        analysis="",
    )
    n = _normalize_combination_question(q)
    print(n.stem)
    print(n.options)
    print(n.answer)


if __name__ == "__main__":
    main()


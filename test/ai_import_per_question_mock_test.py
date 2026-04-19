from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sj_generator.ai.import_questions import import_questions_from_sources


class MockLlmClient:
    def __init__(self) -> None:
        self._calls: list[str] = []

    def chat_json(self, *, system: str, user: str):
        self._calls.append(user)
        if "只输出阿拉伯数字" in user:
            return "2"
        m = re.search(r"只输出第\s*(\d+)\s*题", user)
        if m:
            idx = int(m.group(1))
            if idx == 1:
                return {
                    "number": "1",
                    "stem": "下列关于社会主义核心价值观的表述，正确的是（ ）",
                    "options": "A. 富强 B. 自由 C. 爱国 D. 以上都正确",
                    "answer": "D",
                }
            if idx == 2:
                return {
                    "number": "3",
                    "stem": "下列属于中国式现代化特征的有（  ）",
                    "options": "①人口规模巨大②共同富裕③协调④和谐共生",
                    "answer": "①②③④",
                }
        return {}


def main() -> None:
    sample = Path(__file__).parent / "ai_samples" / "资料_示例_混合题型.txt"
    text = sample.read_text(encoding="utf-8")
    client = MockLlmClient()
    result = import_questions_from_sources(
        client=client,
        sources=[(sample, text)],
        max_chars_per_chunk=2000,
        strategy="per_question",
    )
    print(len(result.questions))
    print(
        json.dumps(
            [
                {"number": q.number, "stem": q.stem, "options": q.options, "answer": q.answer}
                for q in result.questions
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

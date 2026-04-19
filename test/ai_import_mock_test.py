from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sj_generator.ai.import_questions import import_questions_from_sources


class MockLlmClient:
    def __init__(self, content: str) -> None:
        self._content = content

    def chat_json(self, *, system: str, user: str):
        return json.loads(self._content)


def main() -> None:
    sample = Path(__file__).parent / "ai_samples" / "资料_示例_混合题型.txt"
    text = sample.read_text(encoding="utf-8")

    content = json.dumps(
        [
            {
                "number": "1",
                "stem": "下列关于社会主义核心价值观的表述，正确的是（ ）",
                "options": "A. 富强、民主、文明、和谐 B. 自由、平等、公正、法治 C. 爱国、敬业、诚信、友善 D. 以上都正确",
                "answer": "D",
            },
            {
                "number": "3",
                "stem": "下列属于中国式现代化特征的有（  ）",
                "options": "①人口规模巨大的现代化②全体人民共同富裕的现代化③物质文明和精神文明相协调的现代化④人与自然和谐共生的现代化",
                "answer": "①②③④",
            },
        ],
        ensure_ascii=False,
    )

    client = MockLlmClient(content)
    result = import_questions_from_sources(
        client=client,
        sources=[(sample, text)],
        max_chars_per_chunk=2000,
        strategy="bulk",
    )
    print(len(result.questions))
    for q in result.questions:
        print(q.number, q.answer, q.stem[:20])


if __name__ == "__main__":
    main()

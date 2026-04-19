from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sj_generator.ai.explanations import postprocess_explanation


def main() -> None:
    raw = "\n- ①：**知识模块归类错误** 说明\n\n- ②：**正确** 理由\n- ③：**范围扩大** 0-1 不应被破坏\n"
    cleaned = postprocess_explanation(raw)
    print(cleaned)


if __name__ == "__main__":
    main()


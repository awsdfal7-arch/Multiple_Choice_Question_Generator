from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sj_generator.ai.client import _extract_json


def main() -> None:
    wrapped = "下面是结果：\n```json\n[{\"a\":1},{\"b\":2}]\n```\n谢谢"
    extracted = _extract_json(wrapped)
    print(extracted)


if __name__ == "__main__":
    main()

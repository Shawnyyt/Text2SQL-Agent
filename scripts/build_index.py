"""索引构建入口：python scripts/build_index.py"""

from __future__ import annotations

import sys

from text2sql.indexing import build_indexes


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    stats = build_indexes()
    for name, info in stats.items():
        if name.startswith("_"):
            continue
        detail = " ".join(f"{k}={v}" for k, v in info.items())
        print(f"[{name}] {detail}")
    print(f"索引已写入 {stats['_index_dir']['path']}")


if __name__ == "__main__":
    main()

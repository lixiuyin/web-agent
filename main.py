"""Legacy CLI entry point.

This file is kept for backward compatibility with older invocations such as
``python3 main.py --task "..." --headless``.  The maintained implementation
lives in :mod:`webagent.cli`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Allow ``python main.py`` from a source checkout before editable install."""
    src_dir = Path(__file__).resolve().parent / "src"
    if src_dir.exists():
        sys.path.insert(0, str(src_dir))


def main() -> None:
    _ensure_src_on_path()
    from webagent.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()

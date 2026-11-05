"""Tiny .env loader (stdlib-only).

We avoid external dependencies (python-dotenv) and load environment variables
from a `.env` file if present.

Search strategy:
- current working directory and its parents
- script directory and its parents

First `.env` found wins.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


def _parse_env_text(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        out[key] = value
    return out


def find_dotenv(start: Path, filename: str = ".env", max_depth: int = 6) -> Optional[Path]:
    current = start.resolve()
    for _ in range(max_depth + 1):
        candidate = current / filename
        if candidate.exists() and candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def load_dotenv(*, filename: str = ".env", override: bool = False) -> Optional[Path]:
    """Load variables from `.env` into `os.environ`.

    - If `override` is False (default), existing environment variables win.
    - Returns the path of the loaded `.env`, or None if none found.
    """

    cwd_path = find_dotenv(Path.cwd(), filename=filename)
    script_path = find_dotenv(Path(__file__).resolve().parent, filename=filename)
    path = cwd_path or script_path
    if path is None:
        return None

    values = _parse_env_text(path.read_text(encoding="utf-8"))
    for k, v in values.items():
        if not override and os.environ.get(k):
            continue
        os.environ[k] = v
    return path


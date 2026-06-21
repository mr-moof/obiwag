"""Shared JSONL append helper.

Consolidates the ``open(path, 'a')`` + ``f.write(json.dumps(obj) + '\\n')``
pattern that was duplicated across memory_reader, quality_signals,
hook_logger, and hook_error_handler.

Why no read-all-then-append? None of the callers dedup by key, so a plain
append is both correct and constant-time. A tempfile+os.replace swap would
actually require reading the whole file first (the opposite of the goal),
so it is intentionally NOT used here.

Why no lock file? These JSONL paths are written by at most one Claude
session at a time. A single ``write()`` of a small JSON line is atomic
enough on POSIX and best-effort on Windows. Adding fcntl/msvcrt locks
would be overkill and platform-fragile.

See issue (118).
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Union

PathLike = Union[str, os.PathLike]


def append_jsonl(path: PathLike, obj: Dict[str, Any]) -> None:
    """Append a single JSON object as one line to a JSONL file.

    Creates the parent directory if it does not exist. Creates the file if
    it does not exist. Does NOT read the existing file, so cost is O(1) in
    the number of existing entries.

    Args:
        path: Destination JSONL file. Parent dirs are created if missing.
        obj: JSON-serializable dict. Serialized with json.dumps defaults.

    Raises:
        OSError: If the file cannot be opened for append.
        TypeError: If obj is not JSON-serializable.
    """
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(obj) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)

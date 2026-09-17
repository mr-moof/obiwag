"""Small, side-effect-free helpers for Git default-branch resolution."""

import re
import subprocess
from pathlib import Path
from typing import Union


_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_FALLBACK_DEFAULT_BRANCH = "main"


def get_remote_default_branch(repo_path: Union[Path, str]) -> str:
    """Return ``origin/HEAD``'s branch, falling back conservatively to ``main``.

    ``origin/HEAD`` is local metadata, so this performs no network request. A missing, malformed,
    or unreadable symbolic ref cannot justify guessing a legacy branch name after migration.
    """
    args = [
        "git", "-C", str(repo_path), "symbolic-ref", "--quiet", "--short",
        "refs/remotes/origin/HEAD",
    ]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return _FALLBACK_DEFAULT_BRANCH

    symbolic_ref = result.stdout.strip() if result.returncode == 0 else ""
    if not symbolic_ref.startswith("origin/"):
        return _FALLBACK_DEFAULT_BRANCH
    branch = symbolic_ref[len("origin/"):]
    if not _SAFE_BRANCH.fullmatch(branch) or ".." in branch or "//" in branch:
        return _FALLBACK_DEFAULT_BRANCH
    return branch

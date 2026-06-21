"""Git sync module for Obi Memory System.

Handles git operations for syncing learnings:
- Configure git email
- Detect repo ownership (personal vs shared)
- Stage, commit, push changes
- Handle failures gracefully
- Auto-deploy to ~/.claude/ after success
"""

import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path
from enum import Enum

from core.paths import get_obi_root

# Constants
GIT_EMAIL = "user@example.com"
GIT_NAME = "the user"
PERSONAL_PATH_PATTERN = r"/user/"


class PushStrategy(Enum):
    """Push strategy based on repo ownership."""
    DIRECT_TO_MASTER = "direct_to_master"
    BRANCH_AND_PR = "branch_and_pr"


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    files_changed: List[str] = field(default_factory=list)
    commit_hash: Optional[str] = None
    branch: str = "master"
    push_strategy: PushStrategy = PushStrategy.DIRECT_TO_MASTER
    error_message: Optional[str] = None
    deployed: bool = False


@dataclass
class FileChange:
    """Represents a file change to be applied."""
    file_path: str
    content: str
    operation: str = "append"  # "append", "replace", "create"


# Re-exported from paths.py; keeps `from core.git_sync import get_obiwag_repo_path`
# working for existing callers and test mock seams.
from .paths import get_obiwag_repo_path  # noqa: F401


def get_repo_remote_url(repo_path: Path) -> Optional[str]:
    """Get the remote URL for a repository.

    Args:
        repo_path: Path to git repository

    Returns:
        Remote URL string, or None if not found
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass

    return None


def get_push_strategy(repo_url: Optional[str]) -> PushStrategy:
    """Determine push strategy based on repo ownership.

    Personal repos (owner matches PERSONAL_PATH_PATTERN) can push directly to
    the default branch. Shared repos require branch + PR.

    Args:
        repo_url: Git remote URL

    Returns:
        PushStrategy enum value
    """
    if not repo_url:
        return PushStrategy.BRANCH_AND_PR  # Default to safe option

    if re.search(PERSONAL_PATH_PATTERN, repo_url, re.IGNORECASE):
        return PushStrategy.DIRECT_TO_MASTER

    return PushStrategy.BRANCH_AND_PR


def run_git(repo_path: Path, *args: str) -> Tuple[int, str, str]:
    """Run a git command in the specified repository.

    Args:
        repo_path: Path to git repository
        *args: Git command arguments

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + list(args),
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except subprocess.SubprocessError as e:
        return -1, "", str(e)


def configure_git_identity(repo_path: Path) -> bool:
    """Configure git user identity for the repository.

    Args:
        repo_path: Path to git repository

    Returns:
        True if successful
    """
    # Set email
    code, _, err = run_git(repo_path, "config", "user.email", GIT_EMAIL)
    if code != 0:
        return False

    # Set name
    code, _, err = run_git(repo_path, "config", "user.name", GIT_NAME)
    if code != 0:
        return False

    return True


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch name.

    Args:
        repo_path: Path to git repository

    Returns:
        Branch name string
    """
    code, stdout, _ = run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if code == 0:
        return stdout
    return "master"


def get_current_commit_hash(repo_path: Path) -> Optional[str]:
    """Get the current commit hash.

    Args:
        repo_path: Path to git repository

    Returns:
        Commit hash string, or None
    """
    code, stdout, _ = run_git(repo_path, "rev-parse", "HEAD")
    if code == 0:
        return stdout[:8]  # Short hash
    return None


def apply_learning_to_file(
    repo_path: Path,
    file_path: str,
    content: str,
    operation: str = "append"
) -> bool:
    """Apply a learning to a file.

    Args:
        repo_path: Path to git repository
        file_path: Relative path within repo
        content: Content to add
        operation: "append", "replace", or "create"

    Returns:
        True if successful
    """
    full_path = repo_path / file_path

    # Ensure parent directory exists
    full_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if operation == "create" or not full_path.exists():
            # Create new file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
        elif operation == "append":
            # Append to existing file
            with open(full_path, 'a', encoding='utf-8') as f:
                f.write("\n\n" + content)
        elif operation == "replace":
            # Replace file content
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

        return True
    except (IOError, OSError):
        return False


def format_commit_message(changes: List[FileChange], session_summary: str = "") -> str:
    """Format a commit message for learnings.

    Args:
        changes: List of FileChange objects
        session_summary: Optional session summary

    Returns:
        Formatted commit message
    """
    # Count by type
    files_affected = set(c.file_path for c in changes)

    if len(files_affected) == 1:
        file_name = list(files_affected)[0].split('/')[-1]
        message = f"docs: Add learnings to {file_name}"
    else:
        message = f"docs: Add learnings to {len(files_affected)} files"

    # Add details
    lines = [message, ""]

    if session_summary:
        lines.append(f"Session: {session_summary[:100]}")
        lines.append("")

    lines.append("Files updated:")
    for file_path in sorted(files_affected):
        lines.append(f"  - {file_path}")

    return "\n".join(lines)


def sync_learnings(
    changes: List[FileChange],
    session_summary: str = ""
) -> SyncResult:
    """Commit and push approved learnings to the remote.

    Args:
        changes: List of FileChange objects to apply
        session_summary: Optional session summary for commit message

    Returns:
        SyncResult with operation details
    """
    # Find repo
    repo_path = get_obiwag_repo_path()
    if not repo_path:
        return SyncResult(
            success=False,
            error_message="Could not find obiwag-agents repository"
        )

    # Get remote URL and determine strategy
    remote_url = get_repo_remote_url(repo_path)
    strategy = get_push_strategy(remote_url)

    # Configure git identity
    if not configure_git_identity(repo_path):
        return SyncResult(
            success=False,
            error_message="Failed to configure git identity"
        )

    # For branch strategy, create a new branch
    branch = "master"
    if strategy == PushStrategy.BRANCH_AND_PR:
        from datetime import datetime
        branch = f"learning/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        code, _, err = run_git(repo_path, "checkout", "-b", branch)
        if code != 0:
            return SyncResult(
                success=False,
                error_message=f"Failed to create branch: {err}"
            )

    # Apply changes to files
    files_changed = []
    for change in changes:
        if apply_learning_to_file(repo_path, change.file_path, change.content, change.operation):
            files_changed.append(change.file_path)

    if not files_changed:
        return SyncResult(
            success=False,
            error_message="No files were changed"
        )

    # Stage changes
    for file_path in files_changed:
        code, _, err = run_git(repo_path, "add", file_path)
        if code != 0:
            return SyncResult(
                success=False,
                error_message=f"Failed to stage {file_path}: {err}"
            )

    # Commit
    commit_message = format_commit_message(changes, session_summary)
    code, _, err = run_git(repo_path, "commit", "-m", commit_message)
    if code != 0:
        return SyncResult(
            success=False,
            error_message=f"Failed to commit: {err}"
        )

    commit_hash = get_current_commit_hash(repo_path)

    # Push
    if strategy == PushStrategy.DIRECT_TO_MASTER:
        code, _, err = run_git(repo_path, "push", "origin", branch)
    else:
        code, _, err = run_git(repo_path, "push", "-u", "origin", branch)

    if code != 0:
        return SyncResult(
            success=False,
            files_changed=files_changed,
            commit_hash=commit_hash,
            branch=branch,
            push_strategy=strategy,
            error_message=f"Failed to push: {err}"
        )

    # Auto-deploy if successful
    deployed = deploy_to_installation(repo_path)

    return SyncResult(
        success=True,
        files_changed=files_changed,
        commit_hash=commit_hash,
        branch=branch,
        push_strategy=strategy,
        deployed=deployed
    )


def deploy_to_installation(repo_path: Path) -> bool:
    """Deploy changes to ~/.claude/ installation.

    Runs the deploy script (PowerShell or bash) to copy source files
    into the Claude Code installation directory.

    Args:
        repo_path: Path to obiwag-agents repository

    Returns:
        True if deployment was successful
    """
    obi_root = get_obi_root()

    if not obi_root.exists():
        return False

    try:
        # Deploy script lives in tools/
        tools_dir = repo_path / "tools"
        deploy_ps1 = tools_dir / "deploy.ps1"

        if deploy_ps1.exists():
            result = subprocess.run(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(deploy_ps1)],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0

        return False

    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return False


def format_sync_result(result: SyncResult) -> str:
    """Format sync result for display.

    Args:
        result: SyncResult object

    Returns:
        Formatted string for display
    """
    lines = []

    if result.success:
        lines.append("✅ Learnings synced successfully!")
        lines.append("")
        lines.append(f"Branch: {result.branch}")
        if result.commit_hash:
            lines.append(f"Commit: {result.commit_hash}")
        lines.append(f"Files changed: {len(result.files_changed)}")
        for f in result.files_changed:
            lines.append(f"  - {f}")

        if result.deployed:
            lines.append("")
            lines.append("✅ Auto-deployed to ~/.claude/")
        else:
            lines.append("")
            lines.append("⚠️ Auto-deploy skipped or failed")

        if result.push_strategy == PushStrategy.BRANCH_AND_PR:
            lines.append("")
            lines.append(f"📝 Create a PR for branch: {result.branch}")
    else:
        lines.append("❌ Sync failed")
        if result.error_message:
            lines.append(f"Error: {result.error_message}")

    return "\n".join(lines)

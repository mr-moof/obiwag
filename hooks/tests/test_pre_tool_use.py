"""Tests for pre_tool_use hook Bash anti-pattern blocking."""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from pre_tool_use import check_bash_anti_patterns


class TestCdAntiPattern:
    """Block cd <path> && ... commands."""

    def test_blocks_cd_and_chain(self):
        assert check_bash_anti_patterns("cd C:/foo && ls") is not None

    def test_blocks_cd_with_spaces(self):
        assert check_bash_anti_patterns("  cd /tmp && echo hi") is not None

    def test_allows_standalone_cd(self):
        """Bare cd without && is not an anti-pattern."""
        assert check_bash_anti_patterns("cd /tmp") is None


class TestFileReadAntiPattern:
    """Block cat/head/tail — use Read tool."""

    def test_blocks_cat(self):
        assert check_bash_anti_patterns("cat /etc/hosts") is not None

    def test_blocks_head(self):
        assert check_bash_anti_patterns("head -20 file.txt") is not None

    def test_blocks_tail(self):
        assert check_bash_anti_patterns("tail -f log.txt") is not None

    def test_allows_cat_in_heredoc(self):
        """cat <<EOF is used in git commit messages — should be allowed."""
        # The pattern matches 'cat ' followed by a path, not 'cat <<'
        result = check_bash_anti_patterns("git commit -m \"$(cat <<'EOF'\nmessage\nEOF\n)\"")
        # This is a git command, not a cat-read command
        assert result is None


class TestGrepAntiPattern:
    """Block grep/rg — use Grep tool."""

    def test_blocks_grep(self):
        assert check_bash_anti_patterns("grep -r 'TODO' src/") is not None

    def test_blocks_rg(self):
        assert check_bash_anti_patterns("rg 'pattern' file.py") is not None


class TestFindAntiPattern:
    """Block find — use Glob tool."""

    def test_blocks_find(self):
        assert check_bash_anti_patterns("find . -name '*.py'") is not None

    def test_blocks_find_with_exec(self):
        assert check_bash_anti_patterns("find /tmp -type f -exec rm {} +") is not None


class TestLsPipedAntiPattern:
    """Block piped ls — use Glob tool."""

    def test_blocks_ls_piped(self):
        assert check_bash_anti_patterns("ls phases/ | sort") is not None

    def test_blocks_ls_path_piped(self):
        assert check_bash_anti_patterns("ls C:/foo/bar/*.md | xargs basename") is not None

    def test_allows_plain_ls(self):
        """Plain ls without pipe is legitimate."""
        assert check_bash_anti_patterns("ls C:/foo/bar/") is None


class TestPowerShellAntiPattern:
    """Block Get-Content/Select-String — use Read/Grep tools."""

    def test_blocks_get_content(self):
        assert check_bash_anti_patterns("powershell.exe -Command \"Get-Content file.txt\"") is not None

    def test_blocks_select_string(self):
        assert check_bash_anti_patterns("powershell.exe -Command \"Select-String 'pattern' file.txt\"") is not None


class TestSedAwkAntiPattern:
    """Block sed/awk — use Edit tool."""

    def test_blocks_sed(self):
        assert check_bash_anti_patterns("sed -i 's/old/new/' file.txt") is not None

    def test_blocks_awk(self):
        assert check_bash_anti_patterns("awk '{print $1}' data.txt") is not None


class TestEchoRedirectAntiPattern:
    """Block echo/printf with redirection — use Write tool."""

    def test_blocks_echo_redirect(self):
        assert check_bash_anti_patterns("echo 'content' > file.txt") is not None

    def test_blocks_printf_redirect(self):
        assert check_bash_anti_patterns("printf 'content' >> file.txt") is not None

    def test_allows_echo_no_redirect(self):
        """Plain echo (no redirect) is fine for debugging."""
        assert check_bash_anti_patterns("echo 'hello'") is None


class TestHeredocHashLineBlock:
    """Block heredocs with #-prefixed lines that trigger Claude Code's safety prompt."""

    def test_blocks_markdown_headers_in_heredoc(self):
        """Markdown ## headers in gh heredoc must be blocked."""
        cmd = (
            "gh issue create --title 'test' --body \"$(cat <<'EOF'\n"
            "## Context\n"
            "Some description\n"
            "EOF\n)\""
        )
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None
        assert "temp file" in reason.lower() or "Write tool" in reason

    def test_blocks_hash_comment_in_heredoc(self):
        """# comments in heredoc body must be blocked."""
        cmd = "git commit -m \"$(cat <<'EOF'\n# This is a comment\nfix something\nEOF\n)\""
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None

    def test_allows_heredoc_without_hash_lines(self):
        """Heredocs without # lines should pass through."""
        cmd = "git commit -m \"$(cat <<'EOF'\nfix: update naming conventions\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_allows_hash_in_non_heredoc_command(self):
        """Hash in a regular (non-heredoc) command is fine."""
        assert check_bash_anti_patterns("git log --format='%H'") is None

    def test_blocks_indented_hash_in_heredoc(self):
        """Indented # lines in heredoc must also be blocked."""
        cmd = "gh pr create --body \"$(cat <<'EOF'\n  ## Summary\nbody\nEOF\n)\""
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None


class TestLongInlinePythonBlock:
    """Block long python -c scripts that risk settings.local.json pollution."""

    def test_blocks_long_python_c(self):
        """Long inline python -c must be blocked."""
        script = "x = 1; " * 50  # well over 200 chars
        cmd = f'python -c "{script}"'
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None
        assert "temp .py file" in reason.lower() or "Write tool" in reason

    def test_blocks_long_python_exe_c(self):
        """python.exe -c also blocked."""
        script = "x = 1; " * 50
        cmd = f'C:/Python314/python.exe -c "{script}"'
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None

    def test_blocks_long_python3_c(self):
        """python3 -c also blocked."""
        script = "x = 1; " * 50
        cmd = f'python3 -c "{script}"'
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None

    def test_allows_short_python_c(self):
        """Short python -c is fine."""
        cmd = 'python -c "print(42)"'
        assert check_bash_anti_patterns(cmd) is None

    def test_allows_python_script_file(self):
        """Running a .py file is fine regardless of path length."""
        cmd = "python /tmp/very/long/path/to/some/deeply/nested/script.py --arg1 value1 --arg2 value2"
        assert check_bash_anti_patterns(cmd) is None

    def test_allows_python_m_pytest(self):
        """python -m pytest is not -c, should not be blocked."""
        cmd = "python -m pytest C:/Users/user/source/obiwag-agents/hooks/tests/test_pre_tool_use.py -v"
        assert check_bash_anti_patterns(cmd) is None


class TestWindowsBackslashPathBlock:
    """Block Windows backslash paths that fail in bash."""

    def test_blocks_backslash_exe_path(self):
        """C:\\Python314\\python.exe fails in bash."""
        cmd = 'C:\\Python314\\python.exe /tmp/script.py'
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None
        assert "backslash" in reason.lower()

    def test_blocks_backslash_in_argument(self):
        """Backslash path as argument also fails."""
        cmd = 'python C:\\Users\\user\\AppData\\Local\\Temp\\script.py'
        reason = check_bash_anti_patterns(cmd)
        assert reason is not None

    def test_allows_forward_slash_path(self):
        """Forward-slash paths work in Git Bash."""
        assert check_bash_anti_patterns("python C:/Users/user/tmp/script.py") is None

    def test_allows_unix_path(self):
        """Unix-style paths are fine."""
        assert check_bash_anti_patterns("python /c/Users/user/tmp/script.py") is None

    def test_allows_bare_command(self):
        """Bare command names are fine."""
        assert check_bash_anti_patterns("python /tmp/script.py") is None

    def test_allows_backslash_in_regex(self):
        r"""Regex backslashes like \s+ are not Windows paths."""
        assert check_bash_anti_patterns(r"git log --format='%s\n'") is None

    def test_blocks_double_quoted_backslash_path(self):
        """Double-quoted backslash paths still fail in bash (\\t becomes tab)."""
        cmd = 'powershell -c "D:\\temp\\file"'
        assert check_bash_anti_patterns(cmd) is not None

    def test_allows_single_quoted_backslash_path(self):
        """Single-quoted strings make backslashes literal in bash."""
        assert check_bash_anti_patterns("echo 'C:\\Python314\\python.exe'") is None

    def test_allows_backslash_path_in_heredoc(self):
        """Backslash paths in heredoc content are descriptive text, not executed (issue #81)."""
        cmd = "git commit -F /tmp/msg.txt <<'EOF'\nMentions C:\\path\\example in docs\nEOF"
        assert check_bash_anti_patterns(cmd) is None

    def test_allows_backslash_in_commit_message_file(self):
        """git commit -F with a file doesn't execute the message content."""
        assert check_bash_anti_patterns("git commit -F /tmp/commit_msg.txt") is None


class TestHeredocFalsePositives:
    """Heredoc body content must NOT trigger anti-pattern blocks (issue #52)."""

    def test_type_in_commit_message_heredoc(self):
        """'type naming' in a commit message heredoc must not trigger PowerShell block."""
        cmd = "git commit -m \"$(cat <<'EOF'\nfix: update type naming conventions\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_grep_word_in_heredoc(self):
        """The word 'grep' in heredoc content must not trigger grep block."""
        cmd = "git commit -m \"$(cat <<'EOF'\ndocs: explain how to use grep tool\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_find_word_in_heredoc(self):
        """The word 'find' in heredoc content must not trigger find block."""
        cmd = "git commit -m \"$(cat <<'EOF'\nfind all matching files\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_cat_word_in_heredoc(self):
        """The word 'cat' in heredoc content must not trigger cat block."""
        cmd = "git commit -m \"$(cat <<'EOF'\ncat the file output\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_sed_word_in_heredoc(self):
        """The word 'sed' in heredoc content must not trigger sed block."""
        cmd = "git commit -m \"$(cat <<'EOF'\nsed replacement pattern\nEOF\n)\""
        assert check_bash_anti_patterns(cmd) is None

    def test_actual_command_before_heredoc_still_blocked(self):
        """A real anti-pattern before a heredoc should still be caught."""
        cmd = "cat file.txt <<'EOF'\nsome content\nEOF"
        assert check_bash_anti_patterns(cmd) is not None


class TestLegitimateCommands:
    """Verify legitimate Bash commands are NOT blocked."""

    def test_allows_git_commands(self):
        assert check_bash_anti_patterns("git -C C:/repo status") is None
        assert check_bash_anti_patterns("git fetch origin main") is None
        assert check_bash_anti_patterns("git -C C:/repo -c user.email=x@y.com commit -m 'msg'") is None
        assert check_bash_anti_patterns("git push origin main") is None

    def test_allows_pytest(self):
        assert check_bash_anti_patterns("python -m pytest tests/ -v") is None

    def test_allows_npm(self):
        assert check_bash_anti_patterns("npm install") is None
        assert check_bash_anti_patterns("npm run build") is None

    def test_allows_go_build(self):
        assert check_bash_anti_patterns("go build ./cmd/server") is None

    def test_allows_docker(self):
        assert check_bash_anti_patterns("docker compose up -d") is None

    def test_allows_powershell_deploy(self):
        assert check_bash_anti_patterns(
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy.ps1"
        ) is None

    def test_allows_gh(self):
        assert check_bash_anti_patterns("gh issue create --title 'test'") is None
        assert check_bash_anti_patterns("gh issue close 19") is None

    def test_allows_pip_install(self):
        assert check_bash_anti_patterns("pip install pytest") is None

    def test_allows_mkdir(self):
        assert check_bash_anti_patterns("mkdir -p C:/foo/bar") is None


class TestEphemeralArtifactBlock:
    """Block git add of .obi/ ephemeral session artifacts."""

    def test_blocks_git_add_discovery_report(self):
        assert check_bash_anti_patterns("git add .obi/discovery-report.md") is not None

    def test_blocks_git_add_session_file(self):
        assert check_bash_anti_patterns("git add .obi/session-2026-03-21.md") is not None

    def test_blocks_git_add_with_path_prefix(self):
        assert check_bash_anti_patterns("git add C:/repo/.obi/discovery-report-v2.md") is not None

    def test_allows_git_add_other_obi_files(self):
        """Non-ephemeral .obi/ files like config should be allowed."""
        assert check_bash_anti_patterns("git add .obi/config.json") is None

    def test_allows_git_add_normal_files(self):
        assert check_bash_anti_patterns("git add hooks/pre_tool_use.py") is None


class TestBlockReasonMessages:
    """Verify block reasons mention the correct tool to use."""

    def test_cd_mentions_absolute_paths(self):
        reason = check_bash_anti_patterns("cd /tmp && ls")
        assert "absolute path" in reason.lower() or "git -C" in reason

    def test_cat_mentions_read_tool(self):
        reason = check_bash_anti_patterns("cat file.txt")
        assert "Read" in reason

    def test_grep_mentions_grep_tool(self):
        reason = check_bash_anti_patterns("grep pattern file.txt")
        assert "Grep" in reason

    def test_find_mentions_glob_tool(self):
        reason = check_bash_anti_patterns("find . -name '*.py'")
        assert "Glob" in reason

    def test_sed_mentions_edit_tool(self):
        reason = check_bash_anti_patterns("sed -i 's/x/y/' f.txt")
        assert "Edit" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

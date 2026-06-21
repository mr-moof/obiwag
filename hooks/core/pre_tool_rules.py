"""Data-driven Bash rules for pre_tool_use.

The PreToolUse hook used to grow a single function for each new rule:
add a regex to a list, add a message, expand the matcher.  This module
turns the regex-shaped rules into ``CommandRule`` records with stable
``id`` fields so violations can be logged, asserted on, or pivoted into
metrics later.

Procedural checks (heredoc-with-#-lines, long inline ``python -c``,
Windows backslash paths) stay in ``pre_tool_use.py`` because they need
non-regex evaluation logic.  This module is intentionally regex-only.
"""

from dataclasses import dataclass, field
import re
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class CommandRule:
    """A regex-based Bash command rule.

    Attributes:
        id: Stable identifier (e.g. ``"shell-cd-then-run"``).  Useful for
            logging which rule fired without parsing the message.
        pattern: Regex matched against the command text.
        message: Reason returned when the pattern matches.  This becomes
            the ``reason`` field of the PreToolUse block decision.
        flags: ``re`` flags applied at match time (default ``0``).
    """
    id: str
    pattern: str
    message: str
    flags: int = 0

    def match(self, command: str) -> Optional[str]:
        """Return ``message`` if pattern matches the command, else ``None``."""
        if re.search(self.pattern, command, self.flags):
            return self.message
        return None


# ---------------------------------------------------------------------------
# Restricted npm binaries — separate list because they have a distinct
# domain (execution allow-listing, not "use a dedicated tool").
# ---------------------------------------------------------------------------

RESTRICTED_NPM_RULES: List[CommandRule] = [
    CommandRule(
        id="npm-serve-restricted",
        pattern=r"npx\s+(serve|http-server)\b",
        message=(
            "npx serve/http-server can be blocked by execution allow-listing in "
            "some environments — use `python -m http.server <port>` instead."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Bash anti-patterns — redirect users to dedicated Claude Code tools.
# Order matters: rules are evaluated top-to-bottom and the first match wins.
# ---------------------------------------------------------------------------

BASH_ANTI_PATTERN_RULES: List[CommandRule] = [
    CommandRule(
        id="shell-cd-then-run",
        pattern=r"^\s*cd\s+\S+\s*&&",
        message="Use absolute paths instead of 'cd && ...'. For git, use 'git -C <path>'.",
    ),
    CommandRule(
        id="shell-read-via-cat",
        pattern=r"^\s*(cat|head|tail)\s+",
        message="Use the Read tool instead of cat/head/tail.",
    ),
    CommandRule(
        id="shell-search-via-grep",
        pattern=r"^\s*(grep|rg)\s+",
        message="Use the Grep tool instead of grep/rg.",
    ),
    CommandRule(
        id="shell-find-via-find",
        pattern=r"^\s*find\s+",
        message="Use the Glob tool instead of find.",
    ),
    CommandRule(
        id="shell-list-via-piped-ls",
        pattern=r"^\s*ls\s+.*\|",
        message="Use the Glob tool instead of piped ls.",
    ),
    CommandRule(
        id="powershell-read-cmdlet",
        pattern=r"(Get-Content|Select-String|type\s+)",
        message="Use the Read/Grep tool instead of Get-Content/Select-String.",
        flags=re.IGNORECASE,
    ),
    CommandRule(
        id="shell-write-via-redirect",
        pattern=r'^\s*(echo|printf)\s+.*[>]',
        message="Use the Write tool instead of echo/printf redirection.",
    ),
    CommandRule(
        id="shell-edit-via-sed-awk",
        pattern=r"^\s*(sed|awk)\s+",
        message="Use the Edit tool instead of sed/awk.",
    ),
    CommandRule(
        id="git-add-ephemeral-obi-artifact",
        pattern=r"git\s+add\s+.*\.obi/(discovery-report|session-)",
        message=(
            "Do not commit .obi/discovery-report* or .obi/session-* files. "
            "These are ephemeral."
        ),
    ),
]


def evaluate_rules(
    rules: List[CommandRule], command: str
) -> Optional[Tuple[CommandRule, str]]:
    """Find the first rule that matches ``command``.

    Returns a ``(rule, message)`` tuple on match, ``None`` otherwise.
    Returning the rule (not just the message) lets callers log the rule
    id for telemetry without re-parsing the message string.

    A malformed regex in ``rule.pattern`` is skipped rather than
    propagated — one bad rule must not break the entire PreToolUse
    pipeline. ``test_pre_tool_rules.test_all_patterns_compile`` catches
    bad rules at test time; this guard is the runtime safety net.
    """
    for rule in rules:
        try:
            message = rule.match(command)
        except re.error:
            continue
        if message is not None:
            return rule, message
    return None


__all__ = [
    'CommandRule',
    'RESTRICTED_NPM_RULES',
    'BASH_ANTI_PATTERN_RULES',
    'evaluate_rules',
]

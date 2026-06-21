"""Capability-claim detector for the Stop hook (WI-6).

Flags assistant turns where Claude denied a capability (filesystem access,
tool availability, permission) without any tool_use in the same turn to back
up the claim. Advisory only — emits a ``[Deflection]`` systemMessage; never
blocks.

Cross-cutting rules followed:

- **Transcript-window discipline:** callers pass in the last 2 assistant turns
  (via ``transcript_analyzer.get_recent_assistant_turns``). This module does
  not re-slice.
- **Role boundaries:** the caller provides assistant-only turns, so the
  detector never matches against quoted user text.
- **Code-block exclusion:** denials inside triple-backtick fences or single-
  line backtick spans are ignored. Denials inside blockquotes (``> ...``) are
  ignored (those are usually quoted prior-turn text).

The verification anchor for Claude Code is ``tool_use presence in the same
turn`` — the fallback option called out in the plan (line 450). No ``ToolSearch``
or equivalent event is visible in transcript data (confirmed during WI-6
discovery).
"""

import re
from typing import List


_DENIAL_PATTERNS = [
    r"\bi\s+(?:don['’]t|do\s+not|can['’]t|cannot)\s+"
    r"(?:have|access|reach|see|read|write|run)\b",
    r"\b(?:that|this|it|that['’]s)\s+(?:is\s+)?(?:not\s+|un)"
    r"(?:available|accessible|possible)\b",
    r"\b(?:no|don['’]t\s+have|lack)\s+"
    r"(?:access\s+to|permission|a\s+tool|the\s+ability)\b",
    r"\b(?:my|the)\s+(?:tool|tools|environment|sandbox)\s+"
    r"(?:is|are)\s+(?:read-only|limited|restricted)\b",
    # Paraphrased denials (plan line 487-488 documents these as best-effort).
    r"\bthat['’]s\s+outside\s+what\s+i\s+can\s+do\b",
    r"\bi\s+don['’]t\s+have\s+(?:a\s+)?(?:way|means)\s+to\b",
]

# Patterns copied from transcript_analyzer.analyze_transcript so the anchor
# check stays consistent with how tool calls are counted elsewhere.
_TOOL_USE_PATTERNS = [
    r'<invoke',
    r'"tool_name"\s*:\s*"',
    r'Tool:\s*\w+',
]


def _strip_code_and_quotes(content: str) -> str:
    """Remove fenced code blocks, inline code spans, and blockquoted lines.

    Denials inside these regions are not Claude's own claims. Leaves all
    other text (including any tool_use markers) untouched so the anchor
    check runs against the full turn, not the stripped narrative.
    """
    # Triple-backtick fences (non-greedy, across lines).
    without_fences = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    # Inline backtick spans.
    without_inline = re.sub(r"`[^`\n]*`", "", without_fences)
    # Blockquote lines (must be at line start, optionally after whitespace).
    without_quotes = re.sub(r"(?m)^\s*>.*$", "", without_inline)
    return without_quotes


def _turn_has_tool_use(content: str) -> bool:
    return any(re.search(p, content, re.IGNORECASE) for p in _TOOL_USE_PATTERNS)


def detect_unverified_denials(recent_assistant_turns: List[dict]) -> List[str]:
    """Scan pre-sliced assistant turns for denials without verification.

    Input is the output of
    ``transcript_analyzer.get_recent_assistant_turns(transcript_text, n=2)``.
    Returns a list of quoted matches (up to 160 chars each) for the formatter
    to cite in the systemMessage. Empty list means nothing to flag.
    """
    flagged: List[str] = []

    for turn in recent_assistant_turns or []:
        raw = turn.get('content', '') or ''
        if not raw.strip():
            continue

        # Anchor check runs against the full turn — a tool_use block in a
        # fenced example would be a deliberate illustration, but in practice
        # tool_use markers live in the narrative stream alongside claims.
        has_anchor = _turn_has_tool_use(raw)
        if has_anchor:
            continue

        narrative = _strip_code_and_quotes(raw)
        for pattern in _DENIAL_PATTERNS:
            match = re.search(pattern, narrative, re.IGNORECASE)
            if match:
                quote = _excerpt(narrative, match.start(), match.end())
                flagged.append(quote)
                break  # One flag per turn; multiple phrasings in the same
                       # turn are the same incident.

    return flagged


def _excerpt(text: str, start: int, end: int, window: int = 80) -> str:
    """Return ~160 chars of surrounding context for a match, single-lined."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    snippet = text[lo:hi].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    return snippet


def format_capability_denial_nag(flagged: List[str]) -> str:
    """Format a ``[Deflection]`` advisory for the Stop hook."""
    if not flagged:
        return ""

    count = len(flagged)
    header = (
        f"[Deflection] Capability denial without verification detected "
        f"in {count} recent turn(s). "
        "Confirm denial is accurate or correct in next session."
    )
    quotes = "\n".join(f'  - "{q}"' for q in flagged)
    return f"{header}\n{quotes}"

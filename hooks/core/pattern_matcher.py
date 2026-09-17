"""Pattern matcher for Obi Memory System.

Matches tasks to grounding patterns and generates injection text.
"""

import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple
from .calibration import parse_yaml_frontmatter, get_pattern_threshold, get_max_injected_sources
from .memory_reader import get_repo_path
from .paths import get_obi_root

MAX_PATTERN_FILES_PER_DIR = 128
MAX_PATTERN_BYTES = 256 * 1024
MAX_MATCH_TEXT_CHARS = 200_000


class PatternLoadAborted(RuntimeError):
    """Grounding was abandoned before it could exceed the hook's safe budget."""


def _check_deadline(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise PatternLoadAborted('grounding soft deadline exhausted')


def load_patterns(deadline_monotonic: float | None = None) -> List[Dict[str, Any]]:
    """Load pattern files from every source, lowest precedence first.

    1. ``~/.claude/.obi/patterns/`` — the deployed copy. Without this a machine
       with no source-repo checkout loads **zero** patterns, so grounding was
       silently a dev-workstation-only feature (issue #202).
    2. ``<source repo>/.obi/patterns/`` — the authoring copy, so an edit takes
       effect before it is deployed.
    3. ``~/.claude/.obi/memory/patterns/`` — user overrides.

    Later sources win per ``topic``.

    Returns:
        List of pattern dicts with topic, keywords, sources, injection text
    """
    obi_root = get_obi_root()
    search_dirs = [str(obi_root / ".obi" / "patterns")]

    repo_path = get_repo_path()
    if repo_path:
        search_dirs.append(os.path.join(repo_path, "patterns"))

    search_dirs.append(str(obi_root / ".obi" / "memory" / "patterns"))

    pattern_topics: Dict[str, Dict[str, Any]] = {}
    for directory in search_dirs:
        _check_deadline(deadline_monotonic)
        for pattern in _load_patterns_from_dir(directory, deadline_monotonic):
            pattern_topics[pattern['topic']] = pattern

    return list(pattern_topics.values())


def _load_patterns_from_dir(
    patterns_dir: str,
    deadline_monotonic: float | None = None,
) -> List[Dict[str, Any]]:
    """Load pattern files from a directory.

    Filenames are sorted so load order is deterministic. Anchors (below) make
    equal-confidence ties common, and ``matches.sort`` is stable, so an
    arbitrary ``os.listdir`` order would make ``detect_task_type`` answer
    differently on different machines.
    """
    patterns = []

    if not os.path.isdir(patterns_dir):
        return patterns

    _check_deadline(deadline_monotonic)
    filenames = [name for name in sorted(os.listdir(patterns_dir)) if name.endswith('.md')]
    if len(filenames) > MAX_PATTERN_FILES_PER_DIR:
        raise PatternLoadAborted(
            f'pattern directory exceeds {MAX_PATTERN_FILES_PER_DIR} markdown files'
        )

    for filename in filenames:
        _check_deadline(deadline_monotonic)
        if filename.endswith('.md'):
            filepath = os.path.join(patterns_dir, filename)
            try:
                pattern = _load_pattern_file(filepath, deadline_monotonic)
                if pattern:
                    patterns.append(pattern)
            except PatternLoadAborted:
                raise
            except Exception:
                continue

    return patterns


def _load_pattern_file(
    filepath: str,
    deadline_monotonic: float | None = None,
) -> Optional[Dict[str, Any]]:
    """Load a single pattern file."""
    _check_deadline(deadline_monotonic)
    if os.path.getsize(filepath) > MAX_PATTERN_BYTES:
        raise PatternLoadAborted(
            f'pattern file exceeds {MAX_PATTERN_BYTES} bytes: {os.path.basename(filepath)}'
        )
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    _check_deadline(deadline_monotonic)

    frontmatter = parse_yaml_frontmatter(content)
    if not frontmatter.get('topic'):
        return None

    # Extract injection text from markdown body
    injection_text = _extract_injection_text(content)

    return {
        'topic': frontmatter.get('topic'),
        'confidence': frontmatter.get('confidence', 0.5),
        # Standalone triggers: one hit is enough to ground. Disjoint from
        # match_keywords. Forgetting to copy this through was the whole
        # feature's failure mode -- every file-loaded pattern would look
        # anchorless while the unit tests, which build dicts by hand, passed.
        'anchor_keywords': frontmatter.get('anchor_keywords', []),
        'match_keywords': frontmatter.get('match_keywords', []),
        'sources': frontmatter.get('sources', []),
        'injection_text': injection_text,
    }


def _extract_injection_text(content: str) -> str:
    """Extract injection text from pattern markdown.

    Looks for a code block after "## Injection Text" heading.
    """
    # Find the Injection Text section
    match = re.search(r'## Injection Text\s*```\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Fallback: use Key Grounding Points section
    match = re.search(r'## Key Grounding Points\s*(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if match:
        return match.group(1).strip()

    return ""


def match_task_to_patterns(
    task_text: str,
    patterns: Optional[List[Dict[str, Any]]] = None,
    deadline_monotonic: float | None = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """Match a task description to patterns.

    Args:
        task_text: The task description or user prompt
        patterns: Optional list of patterns (loads from files if not provided)

    Returns:
        List of (pattern, confidence) tuples, sorted by confidence descending
    """
    _check_deadline(deadline_monotonic)
    if patterns is None:
        # Preserve the long-standing zero-argument loader contract for callers
        # and test doubles that do not opt into a deadline. Only pass the new
        # argument when a real deadline exists.
        patterns = (
            load_patterns(deadline_monotonic)
            if deadline_monotonic is not None
            else load_patterns()
        )

    if not patterns:
        return []

    if len(task_text) > MAX_MATCH_TEXT_CHARS:
        half = MAX_MATCH_TEXT_CHARS // 2
        task_text = task_text[:half] + task_text[-half:]
    task_lower = task_text.lower()
    matches = []

    for pattern in patterns:
        _check_deadline(deadline_monotonic)
        anchors = pattern.get('anchor_keywords', []) or []
        supporting = pattern.get('match_keywords', []) or []
        if not anchors and not supporting:
            continue

        # Count hits over the UNION of both lists (word boundary to avoid false
        # positives). Counting the union keeps the curve below identical to the
        # pre-anchor behaviour for patterns that declare no anchors.
        anchor_hits = _count_hits(anchors, task_lower, deadline_monotonic)
        total_hits = anchor_hits + _count_hits(supporting, task_lower, deadline_monotonic)

        if total_hits == 0:
            continue

        base_confidence = pattern.get('confidence', 0.5)

        if anchor_hits:
            # An anchor is unambiguous product vocabulary ("widgetapi",
            # "pwsh"), so one mention is the whole signal -- skip the curve.
            adjusted_confidence = base_confidence
        else:
            # Supporting keywords are only meaningful in combination. The boost
            # is 0.8 / 0.9 / 1.0 / 1.0 for 1 / 2 / 3 / 4+ hits (NOT 0.7 for the
            # first, as an earlier comment claimed).
            #
            # Read that against the threshold before touching either number: a
            # single supporting hit scores confidence * 0.8, so clearing the
            # default 0.7 gate on one hit would need confidence >= 0.875. An
            # anchored pattern must therefore sit in [threshold, threshold/0.8)
            # -- below it anchors cannot fire, at or above it one supporting hit
            # grounds on its own and the supporting tier stops meaning anything.
            # test_pattern_matcher.py enforces that band.
            adjusted_confidence = base_confidence * min(1.0, 0.7 + (0.1 * total_hits))

        matches.append((pattern, adjusted_confidence, anchor_hits, total_hits))

    # Rank by score, then strength of evidence, then topic name.
    #
    # SCORES are unchanged for patterns that declare no anchors -- that is the
    # compatibility guarantee. Tie ORDER deliberately is not: it used to fall out
    # of a stable sort over load order, which for load_patterns() is os.listdir
    # order, so which pattern won a tie depended on the filesystem. Anchors make
    # equal scores common, so that had to become a total order.
    matches.sort(key=lambda m: (-m[1], -m[2], -m[3], m[0].get('topic') or ''))

    return [(pattern, score) for pattern, score, _, _ in matches]


def _count_hits(
    keywords: List[str],
    task_lower: str,
    deadline_monotonic: float | None = None,
) -> int:
    """Count keywords present in *task_lower* as whole words.

    Matching is literal and ``\\b``-anchored, so plurals need their own entry:
    ``\\bmaintenance plan\\b`` does not match "maintenance plans".
    """
    hits = 0
    for keyword in keywords:
        _check_deadline(deadline_monotonic)
        if re.search(r'\b' + re.escape(str(keyword).lower()) + r'\b', task_lower):
            hits += 1
    return hits


def select_injections(
    task_text: str,
    deadline_monotonic: float | None = None,
) -> List[Tuple[str, float, str]]:
    """Return every qualifying pattern as ``(topic, score, injection_text)``.

    Ranked best-first, above threshold, with empty-injection patterns dropped --
    a pattern with nothing to say must not occupy a slot (a real case: the
    github pattern qualified for months while contributing no text).

    Deliberately NOT truncated to ``max_injected_sources``. Callers that dedupe
    need the full ranked list, because capping before excluding already-injected
    topics lets stale topics eat the slots and starve a fresh one for the rest of
    the session. Cap inside the same atomic step that records the claim
    (``SessionState.claim_topics``).
    """
    threshold = get_pattern_threshold()

    selected = []
    for pattern, score in match_task_to_patterns(
        task_text,
        deadline_monotonic=deadline_monotonic,
    ):
        if score < threshold:
            continue
        injection = pattern.get('injection_text', '')
        if not injection:
            continue
        selected.append((pattern.get('topic') or '', score, injection))

    return selected


def render_injection(selected: List[Tuple[str, float, str]]) -> Optional[str]:
    """Render selected injections into the text block shown to the model."""
    parts = [
        f"[Pattern: {topic} (confidence: {score:.2f})]\n{text}"
        for topic, score, text in selected
    ]
    return "\n\n".join(parts) if parts else None


def get_injection_text(task_text: str) -> Optional[str]:
    """Get combined injection text for a task, capped at ``max_injected_sources``.

    Thin wrapper over ``select_injections`` for callers that do not dedupe.

    Args:
        task_text: The task description or user prompt

    Returns:
        Injection text string, or None if nothing qualifies
    """
    return render_injection(select_injections(task_text)[:get_max_injected_sources()])


def detect_task_type(task_text: str) -> str:
    """Detect the primary task type from task text.

    Returns:
        Task type string (canvasapi, storageapi, widgetapi, powershell, unknown)
    """
    matches = match_task_to_patterns(task_text)

    if matches and matches[0][1] >= 0.3:  # Low threshold for type detection
        return matches[0][0].get('topic', 'unknown')

    return 'unknown'

"""Pattern matcher for Obi Memory System.

Matches tasks to grounding patterns and generates injection text.
"""

import os
import re
from typing import Dict, Any, List, Optional, Tuple
from .calibration import parse_yaml_frontmatter, get_pattern_threshold, get_max_injected_sources
from .memory_reader import get_repo_path
from .paths import get_obi_root


def load_patterns() -> List[Dict[str, Any]]:
    """Load all pattern files from repo and user directories.

    Repo patterns are loaded first, then user patterns can override.

    Returns:
        List of pattern dicts with topic, keywords, sources, injection text
    """
    patterns = []

    # Load from repo
    repo_path = get_repo_path()
    if repo_path:
        patterns_dir = os.path.join(repo_path, "patterns")
        patterns.extend(_load_patterns_from_dir(patterns_dir))

    # Load from user memory (can override/add to repo patterns)
    user_patterns_dir = str(get_obi_root() / ".obi" / "memory" / "patterns")
    user_patterns = _load_patterns_from_dir(user_patterns_dir)

    # Merge user patterns with repo patterns (user takes precedence by topic)
    pattern_topics = {p['topic']: p for p in patterns}
    for up in user_patterns:
        pattern_topics[up['topic']] = up

    return list(pattern_topics.values())


def _load_patterns_from_dir(patterns_dir: str) -> List[Dict[str, Any]]:
    """Load pattern files from a directory."""
    patterns = []

    if not os.path.isdir(patterns_dir):
        return patterns

    for filename in os.listdir(patterns_dir):
        if filename.endswith('.md'):
            filepath = os.path.join(patterns_dir, filename)
            try:
                pattern = _load_pattern_file(filepath)
                if pattern:
                    patterns.append(pattern)
            except Exception:
                continue

    return patterns


def _load_pattern_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Load a single pattern file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    frontmatter = parse_yaml_frontmatter(content)
    if not frontmatter.get('topic'):
        return None

    # Extract injection text from markdown body
    injection_text = _extract_injection_text(content)

    return {
        'topic': frontmatter.get('topic'),
        'confidence': frontmatter.get('confidence', 0.5),
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
    patterns: Optional[List[Dict[str, Any]]] = None
) -> List[Tuple[Dict[str, Any], float]]:
    """Match a task description to patterns.

    Args:
        task_text: The task description or user prompt
        patterns: Optional list of patterns (loads from files if not provided)

    Returns:
        List of (pattern, confidence) tuples, sorted by confidence descending
    """
    if patterns is None:
        patterns = load_patterns()

    if not patterns:
        return []

    task_lower = task_text.lower()
    matches = []

    for pattern in patterns:
        keywords = pattern.get('match_keywords', [])
        if not keywords:
            continue

        # Count keyword matches (word boundary to avoid false positives)
        matched_count = 0
        for keyword in keywords:
            kw_re = r'\b' + re.escape(keyword.lower()) + r'\b'
            if re.search(kw_re, task_lower):
                matched_count += 1

        if matched_count > 0:
            # Calculate confidence:
            # - Base confidence from pattern (how reliable is this pattern)
            # - Boost based on number of matches (more matches = higher confidence)
            # - At least 1 match gives 70% of base, 2+ matches approach 100%
            base_confidence = pattern.get('confidence', 0.5)
            match_boost = min(1.0, 0.7 + (0.1 * matched_count))  # 0.7, 0.8, 0.9, 1.0 for 1,2,3,4+ matches
            adjusted_confidence = base_confidence * match_boost

            matches.append((pattern, adjusted_confidence))

    # Sort by confidence descending
    matches.sort(key=lambda x: x[1], reverse=True)

    return matches


def get_injection_text(task_text: str) -> Optional[str]:
    """Get injection text for a task.

    Matches task to patterns and returns combined injection text
    from top matching patterns.

    Args:
        task_text: The task description or user prompt

    Returns:
        Injection text string, or None if no matches above threshold
    """
    threshold = get_pattern_threshold()
    max_sources = get_max_injected_sources()

    matches = match_task_to_patterns(task_text)

    # Filter by threshold
    qualified = [(p, c) for p, c in matches if c >= threshold]

    if not qualified:
        return None

    # Take top matches up to max_sources
    top_matches = qualified[:max_sources]

    # Build injection text
    parts = []
    for pattern, confidence in top_matches:
        injection = pattern.get('injection_text', '')
        if injection:
            parts.append(f"[Pattern: {pattern['topic']} (confidence: {confidence:.2f})]\n{injection}")

    if not parts:
        return None

    return "\n\n".join(parts)


def detect_task_type(task_text: str) -> str:
    """Detect the primary task type from task text.

    Returns:
        Task type string (the matched grounding pattern's topic, e.g.
        powershell, debugging, github; or 'unknown')
    """
    matches = match_task_to_patterns(task_text)

    if matches and matches[0][1] >= 0.3:  # Low threshold for type detection
        return matches[0][0].get('topic', 'unknown')

    return 'unknown'

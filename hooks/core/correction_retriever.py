"""Correction retriever for RAG-style injection.

Retrieves relevant past corrections to inject into session context,
helping prevent repeated mistakes.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict

from core.paths import get_obi_root


def get_corrections_path() -> str:
    """Get path to corrections directory."""
    return str(get_obi_root() / ".obi" / "memory" / "corrections")


def read_recent_corrections(days: int = 30) -> List[Dict[str, Any]]:
    """Read corrections from the last N days.

    Args:
        days: Number of days of history to read

    Returns:
        List of correction entries
    """
    corrections_path = get_corrections_path()

    if not os.path.isdir(corrections_path):
        return []

    corrections = []
    cutoff_date = datetime.now() - timedelta(days=days)

    try:
        for filename in os.listdir(corrections_path):
            if not filename.endswith('.jsonl'):
                continue

            # Parse date from filename (YYYY-MM-DD.jsonl)
            try:
                file_date = datetime.strptime(filename.replace('.jsonl', ''), '%Y-%m-%d')
                if file_date < cutoff_date:
                    continue
            except ValueError:
                continue

            filepath = os.path.join(corrections_path, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                corrections.append(entry)
                            except json.JSONDecodeError:
                                continue
            except Exception:
                continue

    except Exception:
        return []

    return corrections


def score_correction_relevance(
    correction: Dict[str, Any],
    task_type: str,
    keywords: List[str]
) -> float:
    """Score a correction's relevance to the current task.

    Args:
        correction: Correction entry dict
        task_type: Current task type
        keywords: Keywords from current task

    Returns:
        Relevance score (0.0 to 1.0)
    """
    score = 0.0

    # Task type match (high weight)
    if correction.get('task_type', '') == task_type:
        score += 0.4

    # Recency bonus (corrections from last 7 days get extra weight)
    try:
        corr_time = datetime.fromisoformat(correction.get('timestamp', ''))
        days_ago = (datetime.now() - corr_time).days
        if days_ago <= 7:
            score += 0.2 * (1 - days_ago / 7)
    except (ValueError, TypeError):
        pass

    # Keyword overlap
    user_message = correction.get('user_message', '').lower()
    correction_type = correction.get('correction_type', '').lower()

    keyword_matches = 0
    for keyword in keywords:
        if keyword.lower() in user_message or keyword.lower() in correction_type:
            keyword_matches += 1

    if keywords and keyword_matches > 0:
        score += 0.3 * min(keyword_matches / len(keywords), 1.0)

    # Correction type weight (vendor_hallucination is critical)
    if correction.get('correction_type') == 'vendor_hallucination':
        score += 0.1
    elif correction.get('correction_type') == 'api_shape':
        score += 0.05

    return min(score, 1.0)


def extract_keywords_from_task(task_text: str) -> List[str]:
    """Extract relevant keywords from task text.

    Args:
        task_text: User's task description

    Returns:
        List of keywords
    """
    # Common vendor/technology keywords to look for
    vendor_keywords = [
        'cloud', 'aws', 'azure', 'gcp', 'hypervisor', 'kubernetes', 'docker',
        'powershell', 'api', 'endpoint', 'authentication', 'oauth', 'bearer',
        'token', 'credential', 'table', 'query', 'filter', 'cmdb'
    ]

    task_lower = task_text.lower()
    found_keywords = []

    for keyword in vendor_keywords:
        if keyword in task_lower:
            found_keywords.append(keyword)

    # Also extract any quoted strings as potential keywords
    import re
    quoted = re.findall(r'["\']([^"\']+)["\']', task_text)
    found_keywords.extend(quoted[:5])  # Limit to 5

    return found_keywords


def get_relevant_corrections(
    task_type: str,
    task_text: str = "",
    max_corrections: int = 3,
    min_score: float = 0.3
) -> List[Dict[str, Any]]:
    """Get corrections relevant to the current task.

    Args:
        task_type: Type of task being performed
        task_text: Optional task description for keyword extraction
        max_corrections: Maximum number of corrections to return
        min_score: Minimum relevance score threshold

    Returns:
        List of relevant corrections, sorted by relevance
    """
    corrections = read_recent_corrections(days=30)

    if not corrections:
        return []

    # Extract keywords from task text
    keywords = extract_keywords_from_task(task_text) if task_text else []

    # Score each correction
    scored = []
    for corr in corrections:
        score = score_correction_relevance(corr, task_type, keywords)
        if score >= min_score:
            scored.append((score, corr))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Return top N
    return [corr for _, corr in scored[:max_corrections]]


def format_corrections_for_injection(corrections: List[Dict[str, Any]]) -> str:
    """Format corrections for injection into session context.

    Args:
        corrections: List of correction entries

    Returns:
        Formatted markdown string for injection
    """
    if not corrections:
        return ""

    lines = [
        "## Relevant Past Corrections",
        "",
        "These corrections from recent sessions may be relevant:",
        ""
    ]

    for i, corr in enumerate(corrections, 1):
        corr_type = corr.get('correction_type', 'unknown')
        corr_desc = corr.get('correction_type_desc', '')
        message = corr.get('user_message', '')[:100]
        task_type = corr.get('task_type', 'unknown')

        # Get source if available
        source = corr.get('source_cited', '')

        lines.append(f"**{i}. {corr_type}** ({task_type})")
        lines.append(f"   - Context: \"{message}...\"")
        if source:
            lines.append(f"   - Source: {source}")
        lines.append("")

    return "\n".join(lines)


def get_correction_injection(
    task_type: str,
    task_text: str = ""
) -> Optional[str]:
    """Get formatted correction injection for session context.

    Main entry point for RAG-style correction injection.

    Args:
        task_type: Type of task being performed
        task_text: Optional task description

    Returns:
        Formatted injection text, or None if no relevant corrections
    """
    corrections = get_relevant_corrections(
        task_type=task_type,
        task_text=task_text,
        max_corrections=3,
        min_score=0.3
    )

    if not corrections:
        return None

    return format_corrections_for_injection(corrections)


# Group corrections by type for analysis
def get_correction_summary() -> Dict[str, Any]:
    """Get summary of corrections for calibration purposes.

    Returns:
        Summary dict with counts by type and task_type
    """
    corrections = read_recent_corrections(days=30)

    summary = {
        'total': len(corrections),
        'by_type': defaultdict(int),
        'by_task_type': defaultdict(int),
        'by_date': defaultdict(int),
    }

    for corr in corrections:
        corr_type = corr.get('correction_type', 'unknown')
        task_type = corr.get('task_type', 'unknown')

        summary['by_type'][corr_type] += 1
        summary['by_task_type'][task_type] += 1

        # Date grouping
        try:
            timestamp = corr.get('timestamp', '')
            date = timestamp.split('T')[0] if 'T' in timestamp else timestamp[:10]
            summary['by_date'][date] += 1
        except (ValueError, TypeError, IndexError):
            pass

    # Convert defaultdicts to regular dicts
    summary['by_type'] = dict(summary['by_type'])
    summary['by_task_type'] = dict(summary['by_task_type'])
    summary['by_date'] = dict(summary['by_date'])

    return summary

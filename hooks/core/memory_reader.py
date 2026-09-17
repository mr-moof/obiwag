"""Memory reader/writer for Obi Memory System.

Handles reading and writing session summaries, correction logs, and grounding logs.
"""

import os
import json
import hashlib
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional

from .paths import get_obi_root


def get_memory_path() -> str:
    """Get path to user's memory directory."""
    return str(get_obi_root() / ".obi" / "memory")


# Re-exported from paths.py; keeps `from core.memory_reader import get_repo_path`
# working for __init__.py and other callers.
from .paths import get_obi_data_dir as get_repo_path  # noqa: F401


def get_pending_path() -> str:
    """Get path to pending evolutions directory."""
    return str(get_obi_root() / ".obi" / "pending")


# Re-exported from paths.py (dedup of #124).
from .paths import ensure_dir  # noqa: F401


def generate_session_id(seed: Optional[str] = None) -> str:
    """Generate a short session identifier."""
    if seed:
        return hashlib.md5(seed.encode()).hexdigest()[:8]
    return hashlib.md5(datetime.now().isoformat().encode()).hexdigest()[:8]


def read_session_history(limit: int = 10) -> List[Dict[str, Any]]:
    """Read recent session summaries.

    Args:
        limit: Maximum number of sessions to return

    Returns:
        List of session summary dicts, most recent first
    """
    memory_path = get_memory_path()
    sessions_path = os.path.join(memory_path, "sessions")

    if not os.path.isdir(sessions_path):
        return []

    summaries = []
    files = sorted(os.listdir(sessions_path), reverse=True)[:limit]

    for filename in files:
        if filename.endswith('.md'):
            filepath = os.path.join(sessions_path, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                # Parse frontmatter for metadata
                from .calibration import parse_yaml_frontmatter
                metadata = parse_yaml_frontmatter(content)
                metadata['_filename'] = filename
                summaries.append(metadata)
            except Exception:
                continue

    return summaries


def write_session_summary(
    session_id: str,
    task_type: str,
    outcome: str,
    metrics: Dict[str, Any],
    sources_cited: List[str],
    corrections: List[Dict[str, Any]],
    learnings: List[Dict[str, Any]],
    summary_text: str
) -> str:
    """Write a session summary to the sessions directory.

    Returns:
        Path to the written file
    """
    memory_path = get_memory_path()
    sessions_path = os.path.join(memory_path, "sessions")
    ensure_dir(sessions_path)

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{session_id}.md"
    filepath = os.path.join(sessions_path, filename)

    # Build frontmatter
    frontmatter = f"""---
session_id: {session_id}
date: {date_str}
task_type: {task_type}
outcome: {outcome}

metrics:
  tool_calls: {metrics.get('tool_calls', 0)}
  corrections: {metrics.get('corrections', 0)}
  verifications: {metrics.get('verifications', 0)}
  files_modified: {metrics.get('files_modified', 0)}
  tests_run: {str(metrics.get('tests_run', False)).lower()}
  tests_passed: {str(metrics.get('tests_passed', False)).lower()}

sources_cited:
"""

    for source in sources_cited:
        frontmatter += f"  - {source}\n"

    frontmatter += "\ncorrections:\n"
    for corr in corrections:
        frontmatter += f"  - type: {corr.get('type', 'unknown')}\n"
        frontmatter += f"    context: \"{corr.get('context', '')}\"\n"
        if corr.get('source_provided'):
            frontmatter += f"    source_provided: {corr.get('source_provided')}\n"

    frontmatter += "\nlearnings:\n"
    for learning in learnings:
        frontmatter += f"  - pattern: {learning.get('pattern', 'unknown')}\n"
        frontmatter += f"    grounding: \"{learning.get('grounding', '')}\"\n"

    frontmatter += "---\n\n"

    content = frontmatter + summary_text

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    return filepath


# Correction type classification
CORRECTION_TYPES = {
    'api_shape': 'Wrong API structure/parameters',
    'vendor_hallucination': 'Invented vendor API',
    'logic_error': 'Incorrect implementation logic',
    'missing_feature': 'Forgot to implement requested feature',
    'over_building': 'Added unrequested features',
    'test_gap': 'Missing or weak tests',
    'syntax_error': 'Syntax or language error',
    'config_error': 'Configuration mistake',
    'unknown': 'Unclassified correction',
}


def classify_correction(user_message: str) -> str:
    """Classify a correction based on the user message content.

    Args:
        user_message: The user's correction message

    Returns:
        One of the CORRECTION_TYPES keys
    """
    message_lower = user_message.lower()

    # Check for vendor hallucination indicators
    vendor_indicators = [
        'api', 'endpoint', 'doesn\'t exist', 'not a real',
        'made up', 'hallucin', 'invented', 'fake'
    ]
    if any(ind in message_lower for ind in vendor_indicators):
        return 'vendor_hallucination'

    # Check for API shape issues
    api_shape_indicators = [
        'parameter', 'argument', 'wrong type', 'wrong shape',
        'should be', 'expects', 'format'
    ]
    if any(ind in message_lower for ind in api_shape_indicators):
        return 'api_shape'

    # Check for over-building
    over_building_indicators = [
        'didn\'t ask', 'too much', 'unnecessary', 'remove',
        'don\'t need', 'extra', 'yagni'
    ]
    if any(ind in message_lower for ind in over_building_indicators):
        return 'over_building'

    # Check for missing features
    missing_indicators = [
        'forgot', 'missing', 'also need', 'didn\'t implement',
        'where is', 'should have'
    ]
    if any(ind in message_lower for ind in missing_indicators):
        return 'missing_feature'

    # Check for test issues
    test_indicators = [
        'test', 'coverage', 'assertion', 'mock', 'spec'
    ]
    if any(ind in message_lower for ind in test_indicators):
        return 'test_gap'

    # Check for logic errors
    logic_indicators = [
        'wrong', 'incorrect', 'bug', 'doesn\'t work',
        'broken', 'error', 'fail'
    ]
    if any(ind in message_lower for ind in logic_indicators):
        return 'logic_error'

    # Check for syntax errors
    syntax_indicators = [
        'syntax', 'typo', 'compile', 'parse'
    ]
    if any(ind in message_lower for ind in syntax_indicators):
        return 'syntax_error'

    # Check for config errors
    config_indicators = [
        'config', 'setting', 'environment', 'variable'
    ]
    if any(ind in message_lower for ind in config_indicators):
        return 'config_error'

    return 'unknown'


def log_correction(
    session_id: str,
    task_type: str,
    correction_type: str,
    user_message: str,
    source_cited: Optional[str] = None,
    tool_corrected: Optional[str] = None,
    tool_calls_before: Optional[int] = None,
    auto_classify: bool = True
) -> None:
    """Log a correction signal to the daily corrections file.

    Args:
        session_id: Current session identifier
        task_type: Type of task being performed
        correction_type: Category of correction (api_shape, vendor_hallucination, etc.)
                        If 'auto' or empty, will be auto-classified from user_message
        user_message: The user's correction message
        source_cited: Optional source the user cited
        tool_corrected: Optional tool that was corrected
        tool_calls_before: Optional count of tool calls before this correction (efficiency metric)
        auto_classify: Whether to auto-classify if correction_type is unknown
    """
    memory_path = get_memory_path()
    corrections_path = os.path.join(memory_path, "corrections")
    ensure_dir(corrections_path)

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}.jsonl"
    filepath = os.path.join(corrections_path, filename)

    # Scan and append under one lock, so two Stop processes finishing at the same
    # moment cannot both scan, both miss, and both append the same correction.
    # Imported lazily: session_state imports this module, so a module-level
    # import would be circular.
    from .session_state import StateUnavailable, _file_lock

    try:
        with _file_lock(filepath + '.lock'):
            _append_correction_if_new(filepath, user_message, correction_type,
                                      auto_classify, session_id, task_type,
                                      source_cited, tool_corrected, tool_calls_before)
    except StateUnavailable:
        # Lock busy past its budget. This path is NOT atomic -- the holder can
        # append the same text after we do, producing a duplicate. That is the
        # accepted cost: a duplicate is recoverable, a lost correction is not.
        _append_correction_if_new(filepath, user_message, correction_type,
                                  auto_classify, session_id, task_type,
                                  source_cited, tool_corrected, tool_calls_before)


def _append_correction_if_new(
    filepath: str,
    user_message: str,
    correction_type: str,
    auto_classify: bool,
    session_id: str,
    task_type: str,
    source_cited: Optional[str],
    tool_corrected: Optional[str],
    tool_calls_before: Optional[int],
) -> None:
    """Dedupe-check and append. Caller holds the lock (see log_correction)."""
    # Checked before classifying: classify_correction runs a regex sweep, and on
    # a duplicate every bit of that work is discarded.
    if _correction_already_logged(filepath, user_message):
        return

    # Auto-classify if needed
    final_correction_type = correction_type
    if auto_classify and (not correction_type or correction_type in ('auto', 'unknown')):
        final_correction_type = classify_correction(user_message)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "task_type": task_type,
        "correction_type": final_correction_type,
        "correction_type_desc": CORRECTION_TYPES.get(final_correction_type, 'Unknown'),
        "user_message": user_message,
    }

    if source_cited:
        entry["source_cited"] = source_cited
    if tool_corrected:
        entry["tool_corrected"] = tool_corrected
    if tool_calls_before is not None:
        entry["tool_calls_before_correction"] = tool_calls_before

    from .jsonl_helper import append_jsonl
    append_jsonl(filepath, entry)


def _correction_already_logged(filepath: str, user_message: str) -> bool:
    """True if this exact correction text is already in today's file.

    Stop re-analyzes the whole transcript on every firing, and a correction stays
    in the transcript for the rest of the session -- and into later sessions once
    compaction carries it forward -- so without this the same message is appended
    once per analysis.

    Scoped to the current day's file: it is the unit already being appended to, it
    bounds the read, and a correction genuinely repeated on a later day is signal
    worth keeping.

    FAILS OPEN. Every failure path returns False (log it) rather than raising,
    because losing a real correction is worse than storing a duplicate. That
    covers more than I/O errors: a log holding invalid UTF-8, JSON scalars or
    lists instead of objects, or a non-string ``user_message`` will otherwise
    raise UnicodeDecodeError/AttributeError out of this helper and take the
    correction down with it.
    """
    if not user_message or not os.path.exists(filepath):
        return False

    needle = ' '.join(str(user_message).split())
    try:
        # errors='replace': invalid UTF-8 in the log must not raise. A mangled
        # line simply will not compare equal, so it degrades to "not a duplicate".
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    prior = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(prior, dict):
                    continue  # a scalar or list line is not a correction record
                prior_msg = prior.get('user_message')
                if not isinstance(prior_msg, str):
                    continue
                if ' '.join(prior_msg.split()) == needle:
                    return True
    except Exception:  # noqa: BLE001 - never let a bad log discard a correction
        return False

    return False


def read_grounding_log(days: int = 7) -> List[Dict[str, Any]]:
    """Read grounding log entries from recent days.

    Args:
        days: Number of days of history to read

    Returns:
        List of grounding log entries
    """
    grounding_path = str(get_obi_root() / ".obi" / "grounding-log.jsonl")

    if not os.path.exists(grounding_path):
        return []

    entries = []
    try:
        with open(grounding_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []

    return entries


def log_grounding_source(
    session_id: str,
    task_type: str,
    source: str,
    context: str
) -> None:
    """Log a source citation to the grounding log.

    Used when user cites a source during a correction.
    """
    grounding_path = str(get_obi_root() / ".obi" / "grounding-log.jsonl")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "task_type": task_type,
        "source": source,
        "context": context,
    }

    from .jsonl_helper import append_jsonl
    append_jsonl(grounding_path, entry)


def read_claude_history(limit: int = 5, project_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read recent conversation topics from Claude Code's history.jsonl.

    Optimized to read only the last 100 lines from the file for better
    performance with large history files.

    Args:
        limit: Maximum number of sessions to return
        project_filter: Optional project path to filter by (for current project context)

    Returns:
        List of session summaries (deduplicated by sessionId), most recent first
    """
    history_path = str(get_obi_root() / "history.jsonl")

    if not os.path.exists(history_path):
        return []

    entries = []
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            # Only keep last 100 lines in memory (enough for ~20 sessions)
            recent_lines = deque(f, maxlen=100)

        for line in recent_lines:
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    if not entries:
        return []

    # Group by sessionId and get first meaningful prompt per session
    sessions = {}
    for entry in entries:
        session_id = entry.get('sessionId')
        if not session_id:
            continue

        # Apply project filter if specified
        if project_filter and entry.get('project', '') != project_filter:
            continue

        # Skip command-only entries (starts with /)
        display = entry.get('display', '')
        if display.startswith('/') or len(display) < 10:
            continue

        # Keep first meaningful prompt per session (topic indicator)
        if session_id not in sessions:
            sessions[session_id] = {
                'sessionId': session_id,
                'topic': display[:100] + '...' if len(display) > 100 else display,
                'timestamp': entry.get('timestamp', 0),
                'project': entry.get('project', 'unknown'),
            }

    # Sort by timestamp descending (most recent first)
    sorted_sessions = sorted(sessions.values(), key=lambda x: x['timestamp'], reverse=True)

    return sorted_sessions[:limit]


def write_pending_evolution(
    proposal_id: str,
    parameter: str,
    current_value: Any,
    proposed_value: Any,
    evidence: Dict[str, Any],
    rationale: str
) -> str:
    """Write a pending evolution proposal.

    Returns:
        Path to the written file
    """
    pending_path = get_pending_path()
    ensure_dir(pending_path)

    # Generate filename
    safe_param = parameter.replace('.', '_').replace('/', '_')
    filename = f"{proposal_id}_{safe_param}.md"
    filepath = os.path.join(pending_path, filename)

    content = f"""---
proposal_id: {proposal_id}
created: {datetime.now().isoformat()}
type: calibration_change
parameter: {parameter}
current_value: {current_value}
proposed_value: {proposed_value}
confidence: {evidence.get('confidence', 0.5)}

evidence:
  sessions_analyzed: {evidence.get('sessions_analyzed', 0)}
  corrections_in_type: {evidence.get('corrections_in_type', 0)}
  average_corrections: {evidence.get('average_corrections', 0)}
  success_rate: {evidence.get('success_rate', 0)}

prior_state:
  {parameter}: {current_value}
---

# Proposed Evolution: {parameter}

## Rationale
{rationale}

## Commands
Accept: `/obi-memory-review accept {proposal_id}`
Reject: `/obi-memory-review reject {proposal_id}`
"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    return filepath

"""Learning detector for Obi Memory System.

Analyzes session transcripts to detect learnable content:
- Gotchas: Errors encountered and resolved
- Patterns: Successful approaches worth documenting
- Workflow: Process improvements from high-correction sessions
"""

import os
import re
from typing import Any, Dict, List, Optional, Set

from core.learning_types import Learning, LearningType, strip_system_content
from core.paths import get_obi_root
from core.hook_logger import log_swallowed


# Keywords that indicate gotcha-worthy corrections
GOTCHA_INDICATORS = [
    # File system issues
    (r'(ntfs|windows|file\s*system)', 'platform', 'docs/gotchas.md'),
    (r'(filename|file\s*name).*(invalid|illegal|special\s*char)', 'platform', 'docs/gotchas.md'),
    (r'(can\'?t|cannot)\s*(checkout|clone|pull)', 'git', 'docs/gotchas.md'),
    (r'(permission|access)\s*(denied|error)', 'platform', 'docs/gotchas.md'),

    # Git issues
    (r'(wrong|incorrect)\s*(branch|repo|repository)', 'git', 'docs/gotchas.md'),
    (r'(email|author).*(not your personal|git config|@\w+\.\w+)', 'git', 'docs/gotchas.md'),
    (r'(force\s*push|push\s*--force)', 'git', 'docs/gotchas.md'),
    (r'(merge\s*conflict|conflict)', 'git', 'docs/gotchas.md'),

    # Location/path issues
    (r'(wrong|incorrect)\s*(location|directory|path|folder)', 'location', 'docs/gotchas.md'),
    (r'(should\s*be|belongs)\s*in\s*[\'"`/~]', 'location', 'docs/gotchas.md'),
    (r'(not|don\'?t)\s*(~/.github|~/.claude|obiwag)', 'location', 'docs/gotchas.md'),
]


def detect_gotchas_from_corrections(
    corrections: List[Dict[str, Any]],
    transcript: str = ""
) -> List[Learning]:
    """Extract gotchas from detected corrections.

    Args:
        corrections: List of correction dicts with 'text', 'pattern', 'correction_type'
        transcript: Full transcript text for additional context

    Returns:
        List of Learning objects for gotcha-type learnings
    """
    learnings = []

    for correction in corrections:
        text = correction.get('text', '').lower()

        for pattern, category, target_file in GOTCHA_INDICATORS:
            if re.search(pattern, text, re.IGNORECASE):
                # Extract a title from the correction text
                title = extract_title_from_correction(correction.get('text', ''))

                learnings.append(Learning(
                    type=LearningType.GOTCHA,
                    title=title,
                    content=f"Correction: {correction.get('text', '')[:100]} | Pattern: {correction.get('pattern', 'unknown')}",
                    target_file=target_file,
                    context=f"Correction detected: {correction.get('pattern', 'unknown')}",
                    confidence=correction.get('confidence', 0.7),
                    metadata={'category': category}
                ))
                break  # One learning per correction

    return learnings



def detect_workflow_improvements(
    transcript: str,
    metrics: Dict[str, Any]
) -> List[Learning]:
    """Detect workflow improvements from session metrics.

    Args:
        transcript: Full session transcript
        metrics: Session metrics dict

    Returns:
        List of Learning objects for workflow improvements
    """
    learnings = []

    # High correction sessions may indicate missing documentation
    corrections = metrics.get('corrections', 0)
    if corrections >= 5:
        # Include correction types in content for better context
        correction_types = []
        for corr in metrics.get('correction_details', []):
            ctype = corr.get('correction_type') or corr.get('pattern', 'unknown')
            correction_types.append(str(ctype)[:50])
        types_str = ', '.join(correction_types[:5]) if correction_types else 'unclassified'
        learnings.append(Learning(
            type=LearningType.WORKFLOW,
            title="High correction session detected",
            content=f"Session had {corrections} corrections ({types_str}) - may indicate missing docs or unclear requirements",
            target_file="docs/workflow/phases.md",
            context=f"Metrics: {corrections} corrections",
            confidence=0.7,
            metadata={'corrections': corrections}
        ))

    return learnings


def extract_title_from_correction(correction_text: str) -> str:
    """Extract a concise title from correction text.

    Args:
        correction_text: The full correction text

    Returns:
        A short title (max 60 chars)
    """
    # Remove common prefixes
    text = correction_text.strip()
    prefixes_to_remove = [
        r'^no[,.]?\s*',
        r'^wrong[,.]?\s*',
        r'^actually[,]?\s*',
        r'^that\'?s\s*(not|wrong|incorrect)[,.]?\s*',
    ]

    for prefix in prefixes_to_remove:
        text = re.sub(prefix, '', text, flags=re.IGNORECASE)

    # Truncate and clean
    text = text.strip()
    if len(text) > 60:
        text = text[:57] + "..."

    return text if text else "Unknown correction"


def load_existing_knowledge() -> Set[str]:
    """Load existing knowledge from MEMORY.md and .obi/patterns/*.md.

    Returns a set of lowercased lines for duplicate detection.
    """
    knowledge_lines: Set[str] = set()
    home = os.path.expanduser("~")

    try:
        # Read MEMORY.md from dynamically resolved project memory directory
        from core.project_memory import get_project_memory_dir
        memory_dir = get_project_memory_dir()
        if memory_dir:
            memory_path = os.path.join(memory_dir, "MEMORY.md")
            if os.path.exists(memory_path):
                with open(memory_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        stripped = line.strip().lower()
                        if stripped and len(stripped) > 10:
                            knowledge_lines.add(stripped)
    except Exception as exc:
        log_swallowed("knowledge_load_memory", exc)

    try:
        # Read .obi/patterns/*.md
        patterns_dir = str(get_obi_root() / ".obi" / "patterns")
        if os.path.isdir(patterns_dir):
            for fname in os.listdir(patterns_dir):
                if fname.endswith('.md'):
                    fpath = os.path.join(patterns_dir, fname)
                    with open(fpath, 'r', encoding='utf-8') as f:
                        for line in f:
                            stripped = line.strip().lower()
                            if stripped and len(stripped) > 10:
                                knowledge_lines.add(stripped)
    except Exception as exc:
        log_swallowed("knowledge_load_patterns", exc)

    return knowledge_lines


def detect_learnings(
    transcript: str,
    metrics: Dict[str, Any],
    correction_details: Optional[List[Dict[str, Any]]] = None,
    min_confidence: float = 0.7
) -> List[Learning]:
    """Main entry point: detect all learnings from session.

    Args:
        transcript: Full session transcript text
        metrics: Session metrics dict from analyze_transcript
        correction_details: Optional list of correction details
        min_confidence: Minimum confidence threshold (default 0.7)

    Returns:
        List of Learning objects, deduplicated, filtered, and sorted by confidence
    """
    all_learnings = []

    # Vendor/reference detectors scan only genuine conversation text (user +
    # assistant turns), with the injected skills catalog and tool_result
    # feedback excluded. Stops the skills list ("...Analytics MCP servers...")
    # from tripping reference_impl. Falls back to raw transcript if the
    # extractor yields nothing. (detector-noise fix)
    convo_text = transcript
    if transcript:
        try:
            from core.transcript_analyzer import extract_conversation_text
            convo_text = extract_conversation_text(transcript) or transcript
        except Exception:
            convo_text = transcript

    # 1. Gotchas from corrections
    if correction_details:
        gotchas = detect_gotchas_from_corrections(correction_details, transcript)
        all_learnings.extend(gotchas)

    # 2. Workflow improvements are intentionally NOT graduated as learnings.
    # A high correction count is a meta-signal (telemetry/advisory), not
    # reusable knowledge worth storing — it produced the recurring "High
    # correction session detected" false positive. detect_workflow_improvements()
    # is retained for advisory callers and tests but excluded from the queue.

    # 3. Vendor discoveries from conversation text
    if convo_text:
        vendor_learnings = detect_vendor_discoveries(convo_text)
        all_learnings.extend(vendor_learnings)

    # 4. Reference implementations (success patterns, no correction needed)
    if convo_text:
        ref_impl_learnings = detect_reference_implementations(convo_text)
        all_learnings.extend(ref_impl_learnings)

    # Deduplicate by (type, target_file) - keep highest confidence
    seen = {}
    for learning in all_learnings:
        key = (learning.type, learning.target_file)
        if key not in seen or learning.confidence > seen[key].confidence:
            seen[key] = learning

    # Filter out learnings that duplicate existing knowledge
    try:
        existing = load_existing_knowledge()
        deduped = {
            k: v for k, v in seen.items()
            if v.title.lower() not in existing
        }
        seen = deduped
    except Exception as exc:
        log_swallowed("learning_dedup", exc)

    # Filter by minimum confidence and sort by confidence descending
    result = sorted(
        [l for l in seen.values() if l.confidence >= min_confidence],
        key=lambda x: x.confidence,
        reverse=True
    )

    # Drop learnings the user has previously dismissed. clear-learnings /
    # reject in /obi-memory-review record (type, category) keys here so a
    # detector false positive can't keep re-surfacing every Stop.
    try:
        from core.learning_suppression import filter_suppressed
        result = filter_suppressed(result)
    except Exception as exc:
        log_swallowed("learning_suppression_filter", exc)

    return result


def format_learnings_summary(learnings: List[Learning]) -> str:
    """Format learnings into a human-readable summary.

    Args:
        learnings: List of Learning objects

    Returns:
        Formatted string for display
    """
    if not learnings:
        return ""

    lines = [
        "",
        "═" * 67,
        "🔔 SYSTEM PROMPT (separate from above)",
        "═" * 67,
        "",
        "📚 Session Learnings Detected",
        "",
        "Obi noticed patterns worth documenting for future sessions.",
        "This is NOT about the code changes above - it's about capturing",
        "reusable knowledge (gotchas, technology patterns, etc.).",
        "",
        f"Found {len(learnings)} potential learning(s):",
        "",
    ]

    # Group by type
    by_type: Dict[LearningType, List[Learning]] = {}
    for learning in learnings:
        if learning.type not in by_type:
            by_type[learning.type] = []
        by_type[learning.type].append(learning)

    # Format each group
    type_labels = {
        LearningType.GOTCHA: "Gotchas",
        LearningType.TECHNOLOGY: "Technology Knowledge",
        LearningType.PLATFORM: "Platform Workarounds",
        LearningType.WORKFLOW: "Workflow",
        LearningType.PATTERN: "Patterns",
    }

    for learning_type, type_learnings in by_type.items():
        label = type_labels.get(learning_type, learning_type.value.title())
        lines.append(f"┌─ {label} ({len(type_learnings)}) " + "─" * (60 - len(label)))

        for i, learning in enumerate(type_learnings, 1):
            lines.append(f"│ {i}. {learning.title}")
            # Truncate content for display
            content_preview = learning.content[:80].replace('\n', ' ')
            if len(learning.content) > 80:
                content_preview += "..."
            lines.append(f"│    {content_preview}")
            lines.append(f"│    → {learning.target_file}")

        lines.append("└" + "─" * 66)
        lines.append("")

    lines.append("Sync these learnings to obiwag-agents repo?")
    lines.append("  yes  = commit & push to the remote")
    lines.append("  no   = discard")
    lines.append("  edit = review before committing")
    lines.append("═" * 67)

    return "\n".join(lines)


def format_learnings_notification(learnings: List[Learning]) -> str:
    """Format a brief, non-interactive notification about captured learnings.

    This replaces the old format_learnings_summary() for use in hooks.
    The old summary format asked yes/no questions, which don't work in
    systemMessage (it's one-way communication).

    Args:
        learnings: List of Learning objects

    Returns:
        Single-line notification string, or empty string if no learnings
    """
    if not learnings:
        return ""

    count = len(learnings)
    types = sorted(set(l.type.value for l in learnings))
    type_str = ", ".join(types)

    return f"💡 Obi captured {count} learning(s) ({type_str}) → /obi-memory-review to manage"


def serialize_learnings(learnings: List[Learning]) -> List[Dict[str, Any]]:
    """Serialize learnings to JSON-compatible format.

    Args:
        learnings: List of Learning objects

    Returns:
        List of dicts suitable for JSON serialization
    """
    return [
        {
            'type': learning.type.value,
            'title': learning.title,
            'content': learning.content,
            'target_file': learning.target_file,
            'context': learning.context,
            'confidence': learning.confidence,
            'metadata': learning.metadata,
        }
        for learning in learnings
    ]


def deserialize_learnings(data: List[Dict[str, Any]]) -> List[Learning]:
    """Deserialize learnings from JSON format.

    Args:
        data: List of dicts from JSON

    Returns:
        List of Learning objects
    """
    learnings = []
    for item in data:
        try:
            learning = Learning(
                type=LearningType(item['type']),
                title=item['title'],
                content=item['content'],
                target_file=item['target_file'],
                context=item.get('context', ''),
                confidence=item.get('confidence', 0.7),
                metadata=item.get('metadata', {}),
            )
            learnings.append(learning)
        except (KeyError, ValueError):
            continue  # Skip malformed entries

    return learnings


# Re-exports for backward compatibility. Shared types moved to
# ``learning_types`` (140); vendor/reference-impl detection moved to
# ``learning_vendor_detector`` (134). Existing callers (stop.py,
# healthcheck.py, tests) still import everything from here.
from core.learning_vendor_detector import (  # noqa: E402,F401
    VENDOR_INDICATORS,
    KNOWLEDGE_SIGNALS,
    CODE_INTROSPECTION_PATTERNS,
    REFERENCE_IMPL_CATEGORIES,
    detect_vendor_discoveries,
    detect_reference_implementations,
)

__all__ = [
    # Types (re-exported from learning_types)
    'Learning',
    'LearningType',
    'strip_system_content',
    # Gotcha / workflow / main entry points
    'GOTCHA_INDICATORS',
    'detect_gotchas_from_corrections',
    'detect_workflow_improvements',
    'detect_learnings',
    'extract_title_from_correction',
    'load_existing_knowledge',
    'format_learnings_summary',
    'format_learnings_notification',
    'serialize_learnings',
    'deserialize_learnings',
    # Vendor / reference-impl (re-exported from learning_vendor_detector)
    'VENDOR_INDICATORS',
    'KNOWLEDGE_SIGNALS',
    'CODE_INTROSPECTION_PATTERNS',
    'REFERENCE_IMPL_CATEGORIES',
    'detect_vendor_discoveries',
    'detect_reference_implementations',
]

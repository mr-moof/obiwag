"""Shared types for the learning pipeline.

Extracted from ``learning_detector.py`` to break the circular import with
``learning_vendor_detector.py`` (issue #140). Contains only leaf-level
definitions that both detectors depend on:

- ``Learning`` — dataclass that every detector emits.
- ``LearningType`` — enum used as the learning taxonomy.
- ``strip_system_content`` — transcript sanitizer that removes harness-
  injected content before any pattern scan runs.

``learning_detector`` and ``learning_vendor_detector`` both import from
here. This module must not import from either of them.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class LearningType(Enum):
    """Types of learnable content."""
    GOTCHA = "gotcha"
    TECHNOLOGY = "technology"
    PATTERN = "pattern"
    PLATFORM = "platform"
    WORKFLOW = "workflow"
    BLINDSPOT = "blindspot"


@dataclass
class Learning:
    """Represents a detected learning."""
    type: LearningType
    title: str
    content: str
    target_file: str
    context: str = ""
    confidence: float = 0.7
    metadata: Dict[str, Any] = field(default_factory=dict)


def strip_system_content(transcript: str) -> str:
    """Strip harness-injected content from a transcript.

    The raw transcript interleaves user/assistant turns with harness
    scaffolding: ``<system-reminder>`` blocks, slash-command framing AND
    rendered slash-command body markdown, pre/post hook notifications,
    and skill-prompt sections. Those blobs contain vendor keywords and
    correction-like phrases that cause false-positive detections. Strip
    them so detectors only scan actual conversation content.

    Args:
        transcript: Raw session transcript text.

    Returns:
        Transcript with harness-injected content removed.
    """
    # Slash-command BODY: <command-message> through the rendered markdown
    # body. The body has no closing tag — it ends at the next message
    # boundary (JSONL "role": marker, another <command-message>, a system
    # reminder, or end of input). Without this strip, slash command files
    # like /learning (which contains the example phrase "OAuth PKCE flow
    # for static sites") and /obi-auto bleed into detector scans. (#159)
    cleaned = re.sub(
        r'<command-message>.*?(?="role"\s*:|<command-message>|<system-reminder>|\Z)',
        '', transcript, flags=re.DOTALL
    )
    # <system-reminder>...</system-reminder> — harness context (memory, skills, MCP, SessionStart notes)
    cleaned = re.sub(
        r'<system-reminder>.*?</system-reminder>',
        '', cleaned, flags=re.DOTALL
    )
    # <user-prompt-submit-hook>...</user-prompt-submit-hook> — hook output injected as user content
    cleaned = re.sub(
        r'<user-prompt-submit-hook>.*?</user-prompt-submit-hook>',
        '', cleaned, flags=re.DOTALL
    )
    # Fallback for malformed transcripts: any remaining <command-*> tag pairs
    # (the slash-command-body strip above usually catches these, but defensive).
    cleaned = re.sub(
        r'<command-(?:name|message|args)>.*?</command-(?:name|message|args)>',
        '', cleaned, flags=re.DOTALL
    )
    # Injected skill/command prompt blocks keyed by "# Claude/Obi ... Role"
    cleaned = re.sub(
        r'# (?:Claude|Obi)[^\n]*Role\b.*?(?=\n# (?!Claude|Obi)|$)',
        '', cleaned, flags=re.DOTALL
    )
    return cleaned

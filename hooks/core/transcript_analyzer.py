"""Transcript analysis and correction detection for the Stop hook.

Extracted from hooks/stop.py in (125) to keep that file under the 600-line
ceiling. Responsible for:
- Splitting raw transcript text into human/assistant turns
- Detecting high-confidence corrections in human turns
- Aggregating per-session metrics (tool calls, files modified, tests, etc.)
"""

import json
import re


def parse_jsonl_conversation(transcript_text: str) -> list:
    """Extract genuine conversation turns from a Claude Code JSONL transcript.

    Claude Code writes one JSON object per line. Only ``user`` and
    ``assistant`` *message* records carry real conversation; everything else
    (``attachment`` records that hold the injected skills catalog, ``summary``
    rows, hook/system meta) is harness scaffolding. Within a user message,
    typed text lives in string content or ``{"type": "text"}`` blocks — while
    ``tool_result`` blocks (which carry tool output AND hook-rejection
    feedback like "Use the Glob tool instead of find") share the same
    ``role: user`` and must NOT be treated as human prose. Counting those as
    corrections is the root of the "high correction session" false positive.

    Returns a list of ``{'role': 'human'|'assistant', 'content': str}`` dicts,
    or ``[]`` when the input is not line-delimited JSON (caller falls back to
    ``split_transcript_into_turns``). Never raises.
    """
    turns = []
    saw_json = False
    for raw in transcript_text.splitlines():
        raw = raw.strip()
        if not raw or raw[0] != '{':
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        saw_json = True

        msg = obj.get('message') if isinstance(obj.get('message'), dict) else obj
        role = msg.get('role') or obj.get('type')
        if role not in ('user', 'human', 'assistant'):
            continue  # skips attachment/summary/system/meta records

        content = msg.get('content')
        text_parts = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                # Only genuine text blocks. tool_use / tool_result / thinking
                # blocks are excluded — that's what keeps tool output and hook
                # feedback out of the correction scan.
                if isinstance(block, dict) and block.get('type') == 'text':
                    text_parts.append(block.get('text', '') or '')

        text = '\n'.join(p for p in text_parts if p).strip()
        if not text:
            continue  # e.g. a user message that is purely tool_result blocks

        normalized_role = 'human' if role in ('user', 'human') else 'assistant'
        turns.append({'role': normalized_role, 'content': text})

    return turns if saw_json else []


def extract_conversation_text(transcript_text: str) -> str:
    """Return only genuine user+assistant text from a transcript.

    Built on :func:`parse_jsonl_conversation` so harness scaffolding (skills
    catalog, tool_result feedback, system reminders) is excluded before the
    vendor/reference-implementation detectors scan it. Falls back to
    ``strip_system_content`` when the transcript is not JSONL (older inline
    format / unit-test strings).
    """
    from core.learning_types import strip_system_content

    turns = parse_jsonl_conversation(transcript_text)
    if turns:
        return strip_system_content('\n\n'.join(t['content'] for t in turns))
    return strip_system_content(transcript_text)


def split_transcript_into_turns(transcript_text: str) -> list:
    """Split transcript into human and assistant turns.

    Returns list of dicts with 'role' and 'content' keys.
    """
    turns = []

    # Common turn markers in transcript formats
    # JSON format: {"role": "human", "content": "..."}
    # or {"role": "user", ...}
    json_pattern = r'"role"\s*:\s*"(human|user|assistant)"[^}]*"content"\s*:\s*"([^"]*)"'
    json_matches = re.findall(json_pattern, transcript_text, re.IGNORECASE | re.DOTALL)
    if json_matches:
        for role, content in json_matches:
            normalized_role = 'human' if role.lower() in ('human', 'user') else 'assistant'
            turns.append({'role': normalized_role, 'content': content})
        return turns

    # Text format: "Human:" or "User:" or "H:" markers
    text_pattern = r'(?:^|\n)\s*(Human|User|H):\s*(.+?)(?=(?:\n\s*(?:Human|User|Assistant|H|A):)|$)'
    text_matches = re.findall(text_pattern, transcript_text, re.IGNORECASE | re.DOTALL)
    if text_matches:
        for role, content in text_matches:
            turns.append({'role': 'human', 'content': content.strip()})
        return turns

    # Fallback: treat entire text as one turn (can't distinguish)
    return []


def get_recent_assistant_turns(transcript_text: str, n: int = 2) -> list:
    """Return the last ``n`` assistant turns from a transcript.

    Cross-cutting rule 2 of the friction-reduction plan: detectors that scan
    transcript text slice to the last 2 assistant turns only. Implement once,
    reuse across detectors (WI-5, WI-6).

    Uses ``split_transcript_into_turns`` for role-aware parsing (cross-cutting
    rule 3). Claude Code transcripts use JSON format, so assistant turns are
    captured via the JSON pattern. The text-format fallback in
    ``split_transcript_into_turns`` only captures human turns — callers that
    need assistant turns against a non-JSON transcript will get an empty list.
    """
    turns = split_transcript_into_turns(transcript_text)
    assistant_turns = [t for t in turns if t.get('role') == 'assistant']
    return assistant_turns[-n:] if n > 0 else assistant_turns


def detect_corrections_in_human_turns(turns: list) -> list:
    """Detect actual user corrections in human turns only.

    Uses high-confidence patterns that indicate genuine corrections,
    not just casual use of words like "fix" or "actually".

    Harness-injected content (``<system-reminder>``, ``<command-args>``,
    etc.) is stripped from each turn's content before pattern matching.
    A prior pass in ``analyze_transcript`` strips the full transcript,
    but per-turn re-stripping handles the case where harness blocks
    survive inside a single JSON turn (issue #141).

    Returns list of correction dicts with 'text', 'pattern', 'confidence', 'correction_type'.
    """
    from core.learning_types import strip_system_content

    corrections = []

    # High-confidence correction patterns with type hints
    # Format: (pattern, confidence, correction_type_hint)
    correction_patterns = [
        # Direct negations (high confidence)
        (r'^no[,.]?\s+(that\'?s\s+wrong|that\'?s\s+not|use|try|it\s+should)', 0.9, None),
        (r'^wrong[,!]', 0.9, None),
        (r'^that\'?s\s+(not|wrong|incorrect)', 0.9, None),

        # Explicit corrections with comma (medium-high confidence)
        (r'^actually,\s+', 0.8, None),  # Requires comma for disambiguation
        (r'^wait,?\s+(that\'?s|it\'?s|this\s+is)\s+(not|wrong)', 0.8, None),

        # Redirection patterns (medium confidence)
        #
        # The signal is "instead" NOT followed by "of". English puts the
        # redirection on either side of the verb -- "instead, use X" and "use X
        # instead" are both corrections -- while "X instead of Y" is an ordinary
        # comparison and is not.
        #
        # The separator matters because \b fires on ANY non-word char, so a bare
        # \s+ lookahead let every non-space spelling of "instead of" through:
        # "instead-of", "docs/instead/of/this", an em-dashed form.
        #
        # It must match "instead of" as a UNIT -- whitespace, or ONE punctuation
        # char with no space around it. A permissive [\s\-/]+ class also swallowed
        # "Instead - of course, use the wrapper", which is a genuine correction
        # interrupted by a dash, not a comparison.
        #
        # The trailing lookahead drops the possessive "instead's", which \b also
        # splits. Escapes keep this line ASCII.
        (r'\binstead\b(?!(?:\s+|[-/\u2013\u2014])of\b)(?![\'\u2019]s\b)',
         0.7, None),
        (r'should\s+be\s+[\'"`]', 0.7, 'api_shape'),  # Often parameter corrections
        (r'not\s+[\'"`][^\'"`]+[\'"`][,.]?\s+(use|it\'?s|should)', 0.7, 'api_shape'),

        # Explicit error call-outs (medium confidence)
        (r'that\'?s\s+an?\s+error', 0.75, 'logic_error'),
        (r'(you|claude)\s+(made\s+a|got\s+it)\s+(mistake|wrong)', 0.8, None),

        # API/technology specific patterns (high confidence for type)
        (r'(api|endpoint)\s+(doesn\'?t|does\s+not)\s+exist', 0.9, 'api_hallucination'),
        (r'(made\s+up|invented|fake)\s+(api|endpoint)', 0.9, 'api_hallucination'),
        (r'^didn\'?t\s+ask\s+(for|you\s+to)', 0.8, 'over_building'),
        (r'^(that\'?s\s+)?too\s+much', 0.75, 'over_building'),
        (r'^(you\s+)?forgot\s+to', 0.8, 'missing_feature'),
        (r'^where\s+is\s+the', 0.8, 'missing_feature'),
        # Note: Removed overly broad patterns that matched test output
        # "missing" alone and "test.*(missing|need|should)" caused false positives
    ]

    for turn in turns:
        if turn.get('role') != 'human':
            continue

        raw_content = turn.get('content', '')
        # Re-strip harness-injected content per-turn. The outer pass in
        # analyze_transcript handles most cases, but embedded reminders
        # inside a single JSON turn can slip through (issue #141).
        content = strip_system_content(raw_content).strip()
        if not content:
            continue

        # Skip long content — document uploads, specs, large pastes.
        # Real corrections are short ("No, that's wrong", "Use the wrapper").
        if len(content) > 2000:
            continue

        # Check each line in human turn (corrections often at line start)
        for line in content.split('\n'):
            line = line.strip()
            if len(line) < 5:  # Skip very short lines
                continue

            for pattern, confidence, type_hint in correction_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    corrections.append({
                        'text': line[:200],
                        'pattern': pattern,
                        'confidence': confidence,
                        'correction_type': type_hint,  # May be None, will be classified later
                    })
                    break  # One correction per line

    return corrections


def analyze_transcript(transcript_text: str) -> dict:
    """Analyze session transcript for metrics and corrections.

    IMPORTANT: Only human turns are analyzed for corrections to avoid
    false positives from Claude's own output containing words like
    "fix", "actually", "should be", etc.

    Returns dict with:
        - tool_calls: int
        - corrections: int
        - files_modified: int
        - tests_run: bool
        - tests_passed: bool
        - sources_cited: list
        - correction_details: list
    """
    metrics = {
        'tool_calls': 0,
        'corrections': 0,
        'files_modified': 0,
        'tests_run': False,
        'tests_passed': False,
        'verifications': 0,
        'sources_cited': [],
        'correction_details': [],
    }

    if not transcript_text:
        return metrics

    # Count tool calls (look for tool invocation patterns)
    tool_patterns = [
        r'<invoke',
        r'"tool_name"\s*:\s*"',
        r'Tool:\s*\w+',
    ]
    for pattern in tool_patterns:
        metrics['tool_calls'] += len(re.findall(pattern, transcript_text, re.IGNORECASE))

    # Strip system-reminder blocks before correction detection.
    # Hook blocking messages (e.g. "Use absolute paths instead of 'cd && ...'")
    # appear in <system-reminder> tags and match the correction pattern
    # `instead[,]?\s+(use|try|do|of)`, producing false positives. (issue 114)
    from core.learning_types import strip_system_content
    cleaned_transcript = strip_system_content(transcript_text)

    # Prefer JSONL-aware parsing: it extracts only genuine user/assistant
    # text and drops tool_result blocks, so hook-rejection feedback ("Use the
    # Glob tool instead of find") and tool output are never miscounted as
    # human corrections (the "high correction session" false positive). Falls
    # back to the regex splitter for non-JSONL transcripts (issue: detector-noise).
    turns = parse_jsonl_conversation(transcript_text)
    if not turns:
        turns = split_transcript_into_turns(cleaned_transcript)
    if turns:
        correction_details = detect_corrections_in_human_turns(turns)
        metrics['corrections'] = len(correction_details)
        metrics['correction_details'] = correction_details
    else:
        # Fallback: if we can't split turns, use conservative detection
        # Only count very explicit correction markers
        fallback_patterns = [
            r'^no[,.]?\s+that\'?s\s+wrong',
            r'^wrong[,!]',
            r'^that\'?s\s+incorrect',
        ]
        for pattern in fallback_patterns:
            metrics['corrections'] += len(re.findall(pattern, cleaned_transcript, re.IGNORECASE | re.MULTILINE))

    # Detect file modifications
    file_patterns = [
        r'File created successfully',
        r'File modified',
        r'"tool":\s*"(Edit|Write)"',
    ]
    for pattern in file_patterns:
        metrics['files_modified'] += len(re.findall(pattern, transcript_text, re.IGNORECASE))

    # Detect test execution
    test_patterns = [
        r'npm\s+test',
        r'pytest',
        r'pester',
        r'go\s+test',
        r'cargo\s+test',
        r'Invoke-Pester',
    ]
    for pattern in test_patterns:
        if re.search(pattern, transcript_text, re.IGNORECASE):
            metrics['tests_run'] = True
            break

    # Detect test results
    if metrics['tests_run']:
        pass_patterns = [
            r'tests?\s+passed',
            r'All\s+\d+\s+tests?\s+passed',
            r'passed.*\d+.*failed.*0',
            r'SUCCESS',
            r'Tests Passed:',
        ]
        for pattern in pass_patterns:
            if re.search(pattern, transcript_text, re.IGNORECASE):
                metrics['tests_passed'] = True
                break

    # Detect source citations
    source_patterns = [
        r'docs/[\w\-/]+\.md',
        r'https?://[\w\.\-/]+',
        r'galaxy\.[\w\.]+/[\w\-/]+',
    ]
    for pattern in source_patterns:
        matches = re.findall(pattern, transcript_text)
        metrics['sources_cited'].extend(matches)

    # Deduplicate sources
    metrics['sources_cited'] = list(set(metrics['sources_cited']))

    return metrics

"""Auto-memory capture for rigor=max plan-vs-reality surprises.

Invoked inline in the rigor=max Ralph loop AFTER each phase's grep gate
succeeds, BEFORE advancing. Compares the locked answers in
runtime.phase0.answers[] against the probe outcomes in runtime.probes for
the just-completed phase. Where they diverge = surprise candidate.

The orchestrator performs the inline confidence classification against the
running model (no recursive rigor=max dispatch). This module exposes pure
functions that the orchestrator calls with the classifier's parsed JSON
verdict; the model call itself is outside this module.

Confidence threshold: 0.7 (per policies/rigor-max-gates.md Gate 5, matching
the existing learning-system threshold in learning_detector.py).

Surprise landing (per discovery report deviation #4):
  1. Markdown audit at ~/.claude/.obi/pending/surprise-<phase>-<UTC>.md
  2. Entry appended to ~/.claude/.obi/pending-learnings.json so the existing
     /obi-memory-review approve <#> flow lists and graduates it.

Detection of expected vs actual happens in compute_surprises(), which the
orchestrator hands to its inline classifier. Below-threshold or invalid JSON
verdicts are logged to .obi/session-quality.jsonl as 'auto-memory-skip' and
silently dropped.
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import get_obi_root
from core.session_state import StateUnavailable, bounded_file_lock


CONFIDENCE_THRESHOLD = 0.7
_LEARNINGS_THREAD_LOCK = threading.Lock()
_IO_RETRIES = 10
_IO_RETRY_SEC = 0.02

# Allowed memory types for /obi-memory-review approve flow. Mirrors the
# memory schema in user CLAUDE.md auto-memory section.
VALID_MEMORY_TYPES = {'user', 'feedback', 'project', 'reference', 'tool'}

# Maps phase0:id values to probe.probe names. Mirrors the Probe Routing
# table in policies/obi-auto-max-schema.md. Without this, surprise
# detection silently misses divergences because probe outcomes are keyed
# by probe-name (e.g. "namespace_kind"), not by phase0 answer id
# (e.g. "target_namespace").
ANS_ID_TO_PROBE = {
    'target_namespace': 'namespace_kind',
    'runner_tags':       'runner_tags',
    'pages_access':      'pages_access',
    'marketplace_url':   'marketplace_reach',
    'mirror_target':     'mirror_existence',
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def compute_surprises(
    phase: int,
    locked_answers: List[Dict[str, Any]],
    probe_outcomes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build surprise candidates by comparing expected vs actual.

    Args:
        phase: Phase number that just completed.
        locked_answers: List of {id, question, answer, locks_field, source}
            entries from the plan's runtime.phase0.answers block.
        probe_outcomes: List of probe-result dicts (the JSON written by
            tools/probes/_lib.ps1 :: New-ProbeResult).

    Returns:
        A list of candidate dicts, each with:
            phase, expected_id, expected_value, actual_probe, actual_value,
            actual_status, divergence
    """
    candidates: List[Dict[str, Any]] = []

    by_probe = {p.get('probe'): p for p in probe_outcomes}

    for ans in locked_answers:
        ans_id = ans.get('id')
        if not ans_id:
            continue
        expected = ans.get('answer')
        # Resolve answer-id -> probe-name via ANS_ID_TO_PROBE table, falling
        # back to ans_id itself for forward-compatibility with custom probes.
        probe_name = ANS_ID_TO_PROBE.get(ans_id, ans_id)
        probe = by_probe.get(probe_name)
        if not probe:
            continue

        actual_status = probe.get('status')
        actual_data = probe.get('data') or {}
        # Heuristics: divergence when status != 'ok' OR when a likely-named
        # data field disagrees with the expected answer.
        divergence: Optional[str] = None
        actual_value = None

        if actual_status != 'ok':
            divergence = f'probe status={actual_status}'
            actual_value = probe.get('error') or actual_status
        else:
            # Look for the answer value in any string-valued data field
            answer_str = str(expected) if expected is not None else ''
            data_blob = json.dumps(actual_data, default=str)
            if answer_str and answer_str.lower() not in data_blob.lower():
                divergence = 'answer not present in probe data'
                actual_value = data_blob

        if divergence:
            candidates.append({
                'phase': phase,
                'expected_id': ans_id,
                'expected_value': expected,
                'actual_probe': probe.get('probe'),
                'actual_value': actual_value,
                'actual_status': actual_status,
                'divergence': divergence,
            })

    return candidates


def parse_classifier_verdict(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the classifier's JSON verdict string.

    Expected JSON shape:
        {"confidence": 0.0-1.0, "summary": "<1-line>",
         "type": "user|feedback|project|reference|tool"}

    Returns the parsed dict if valid, else None. Invalid or missing JSON
    is treated as confidence 0 by the caller.
    """
    if not raw:
        return None
    # Permit prose surrounding the JSON object.
    match = re.search(r'\{.*?\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(obj, dict):
        return None

    conf = obj.get('confidence')
    summary = obj.get('summary')
    mtype = obj.get('type')

    if not isinstance(conf, (int, float)):
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None
    if mtype not in VALID_MEMORY_TYPES:
        return None

    return {
        'confidence': float(conf),
        'summary': summary.strip(),
        'type': mtype,
    }


def is_actionable(verdict: Optional[Dict[str, Any]],
                  threshold: float = CONFIDENCE_THRESHOLD) -> bool:
    """Apply the confidence threshold gate.

    A None verdict (parse failed) or low confidence => not actionable.
    """
    if verdict is None:
        return False
    return verdict.get('confidence', 0.0) >= threshold


def slugify(text: str) -> str:
    """Reduce text to a filename-safe slug."""
    s = re.sub(r'[^A-Za-z0-9]+', '_', text.lower()).strip('_')
    return s[:60] if s else 'unnamed'


def render_surprise_md(
    candidate: Dict[str, Any],
    verdict: Dict[str, Any],
) -> Tuple[str, str]:
    """Render the markdown body for a pending/surprise-<phase>-<UTC>.md file.

    Returns (filename, content).
    """
    phase = candidate['phase']
    ts_compact = _utc_now_compact()
    ts_iso = _utc_now_iso()

    name_slug = slugify(verdict['summary'])
    filename = f'surprise-{phase}-{ts_compact}-{name_slug}.md'

    description = verdict['summary']
    body_lines = [
        '---',
        f'name: surprise_phase{phase}_{name_slug}',
        f'description: {description}',
        f'type: {verdict["type"]}',
        'auto_captured: true',
        f'confidence: {verdict["confidence"]:.2f}',
        f'phase: {phase}',
        f'captured_at: "{ts_iso}"',
        '---',
        '',
        f'# Surprise from Phase {phase}',
        '',
        f'**Summary:** {description}',
        '',
        '## Expected (from plan runtime.phase0.answers)',
        '',
        f'- id: `{candidate["expected_id"]}`',
        f'- value: `{candidate["expected_value"]}`',
        '',
        '## Actual (from probe runtime)',
        '',
        f'- probe: `{candidate["actual_probe"]}`',
        f'- status: `{candidate["actual_status"]}`',
        f'- value: `{candidate["actual_value"]}`',
        '',
        '## Divergence',
        '',
        candidate['divergence'],
        '',
        '## Approval',
        '',
        'Run `/obi-memory-review approve <#>` to graduate this entry to ',
        'permanent memory. The pending-learnings.json index lists the same ',
        'entry by number.',
        '',
    ]
    return filename, '\n'.join(body_lines)


def write_surprise_artifacts(
    candidate: Dict[str, Any],
    verdict: Dict[str, Any],
    pending_dir: Path,
    learnings_json_path: Path,
) -> Dict[str, str]:
    """Write the audit md AND append to pending-learnings.json.

    Args:
        candidate: from compute_surprises()
        verdict: from parse_classifier_verdict()
        pending_dir: ~/.claude/.obi/pending/
        learnings_json_path: ~/.claude/.obi/pending-learnings.json

    Returns:
        Dict with 'md_path' and 'json_path' keys.
    """
    pending_dir.mkdir(parents=True, exist_ok=True)
    filename, content = render_surprise_md(candidate, verdict)
    md_path = pending_dir / filename
    md_path.write_text(content, encoding='utf-8')

    learning_entry = {
        'type': verdict['type'],
        'title': verdict['summary'],
        'content': (
            f'Phase {candidate["phase"]} surprise: '
            f'expected {candidate["expected_id"]}={candidate["expected_value"]!r}, '
            f'probe {candidate["actual_probe"]} status={candidate["actual_status"]}, '
            f'divergence: {candidate["divergence"]}.'
        ),
        'target_file': str(md_path),
        'context': f'Auto-captured from rigor=max phase {candidate["phase"]}.',
        'confidence': float(verdict['confidence']),
        'metadata': {
            'auto_captured': True,
            'phase': candidate['phase'],
            'expected_id': candidate['expected_id'],
            'actual_probe': candidate['actual_probe'],
            'captured_at': _utc_now_iso(),
        },
    }

    _append_learning_with_retry(learnings_json_path, learning_entry)

    return {'md_path': str(md_path), 'json_path': str(learnings_json_path)}


def _read_text_with_retry(path: Path) -> str:
    """Read state despite brief Windows scanner/sharing contention."""
    last_error: Optional[OSError] = None
    for attempt in range(_IO_RETRIES):
        try:
            return path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _IO_RETRIES:
                time.sleep(_IO_RETRY_SEC)
    raise StateUnavailable(str(path)) from last_error


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Publish state despite brief Windows scanner/sharing contention."""
    last_error: Optional[OSError] = None
    for attempt in range(_IO_RETRIES):
        try:
            os.replace(str(source), str(destination))
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < _IO_RETRIES:
                time.sleep(_IO_RETRY_SEC)
    raise StateUnavailable(str(destination)) from last_error


def _append_learning_with_retry(
    learnings_json_path: Path,
    learning_entry: Dict[str, Any],
    lock_timeout_seconds: float = 5.0,
) -> None:
    """Append `learning_entry` to pending-learnings.json under an exclusive
    lock-file.

    Round 1 used tempfile + os.replace (atomic final swap, but read/modify/
    write window unprotected — late writer dropped earlier appends).
    Round 2 added an mtime pre/post check, which closed the simple race but
    still had a TOCTOU window if two writers both read the same pre-mtime
    and both passed the post-mtime check before either reached os.replace.
    Round 3 used a sentinel lock file via os.open(O_CREAT|O_EXCL), but a
    crashed holder could strand it and lock timeout fell back to an unlocked
    write, recreating the lost-update race. Round 4 uses a bounded in-process
    lock plus the shared crash-safe OS sidecar lock. It retries brief Windows
    read/replace sharing denials and fails closed if state remains unavailable;
    it never proceeds unlocked or treats an I/O error as corrupt JSON.
    """
    learnings_json_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = learnings_json_path.with_name(
        learnings_json_path.name + '.lock'
    )
    started = time.monotonic()
    if not _LEARNINGS_THREAD_LOCK.acquire(timeout=lock_timeout_seconds):
        raise StateUnavailable(str(lock_path))
    try:
        elapsed = time.monotonic() - started
        remaining = max(0.001, lock_timeout_seconds - elapsed)
        with bounded_file_lock(str(lock_path), timeout=remaining):
            existing: Dict[str, Any] = {'learnings': []}
            try:
                loaded = json.loads(_read_text_with_retry(learnings_json_path))
            except FileNotFoundError:
                loaded = {'learnings': []}
            except json.JSONDecodeError:
                loaded = {'learnings': []}
            if isinstance(loaded, dict):
                existing = loaded
            if not isinstance(existing.get('learnings'), list):
                existing['learnings'] = []

            existing['learnings'].append(learning_entry)

            # Per-thread unique tempfile prevents cleanup collisions. The two
            # locks above serialize the read-modify-write; the replace retry
            # handles non-cooperating Windows readers such as endpoint scanners.
            tmp_path = learnings_json_path.with_name(
                f'{learnings_json_path.stem}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp'
            )
            try:
                with tmp_path.open('w', encoding='utf-8') as handle:
                    json.dump(existing, handle, indent=2)
                    handle.write('\n')
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(tmp_path, learnings_json_path)
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
    finally:
        _LEARNINGS_THREAD_LOCK.release()


def log_skip(
    candidate: Dict[str, Any],
    reason: str,
    quality_log_path: Path,
) -> None:
    """Append an auto-memory-skip entry to .obi/session-quality.jsonl.

    Used when classifier verdict is invalid or below threshold. Does not
    raise on write errors.
    """
    entry = {
        'ts': _utc_now_iso(),
        'source': 'auto-memory-capture',
        'event': 'auto-memory-skip',
        'phase': candidate.get('phase'),
        'expected_id': candidate.get('expected_id'),
        'reason': reason,
    }
    try:
        quality_log_path.parent.mkdir(parents=True, exist_ok=True)
        with quality_log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
    except OSError:
        pass


def capture_surprise(
    candidate: Dict[str, Any],
    verdict_raw: str,
    pending_dir: Optional[Path] = None,
    learnings_json_path: Optional[Path] = None,
    quality_log_path: Optional[Path] = None,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> Optional[Dict[str, str]]:
    """End-to-end: parse classifier verdict, gate, write artifacts or log skip.

    This is the orchestrator's primary entry point. The orchestrator calls
    the classifier inline (no recursion into rigor=max) and passes the
    raw response here.

    Returns the artifact paths dict on capture, or None on skip.
    """
    if pending_dir is None:
        pending_dir = get_obi_root() / '.obi' / 'pending'
    if learnings_json_path is None:
        learnings_json_path = get_obi_root() / '.obi' / 'pending-learnings.json'
    if quality_log_path is None:
        # Repo-relative (script invocation directory)
        quality_log_path = Path('.obi/session-quality.jsonl')

    verdict = parse_classifier_verdict(verdict_raw)
    if not is_actionable(verdict, threshold):
        reason = (
            'invalid classifier JSON'
            if verdict is None
            else f'confidence {verdict["confidence"]:.2f} below threshold {threshold}'
        )
        log_skip(candidate, reason, quality_log_path)
        return None

    return write_surprise_artifacts(
        candidate=candidate,
        verdict=verdict,
        pending_dir=pending_dir,
        learnings_json_path=learnings_json_path,
    )

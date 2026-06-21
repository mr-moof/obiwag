"""Calibration loader for Obi Memory System.

Reads calibration settings from ~/.claude/.obi/calibration.md
"""

import os
import re
from typing import Any, Dict

import yaml

from core.paths import get_obi_root


def get_calibration_path() -> str:
    """Get path to user's calibration file."""
    return str(get_obi_root() / ".obi" / "calibration.md")


def parse_yaml_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML frontmatter from markdown content.

    Uses ``yaml.safe_load`` to parse the block between the first two
    ``---`` fences. Returns ``{}`` for missing, empty, malformed, or
    non-mapping frontmatter — never raises.
    """
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    frontmatter_text = parts[1]

    try:
        loaded = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        return {}

    if not isinstance(loaded, dict):
        return {}

    return loaded


def parse_value(value: str) -> Any:
    """Parse a YAML value string into Python type.

    Standalone coerce helper retained for backward compatibility with
    external callers and tests. Mirrors the legacy behavior (bool, int,
    float via simple regex, string fallback) without pulling pyyaml into
    the scalar path.
    """
    value = value.strip().strip('"').strip("'")

    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.isdigit():
        return int(value)
    if re.match(r"^-?\d+\.?\d*$", value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


# Module-level cache (avoids repeated file reads within a single hook invocation)
_calibration_cache = None
_calibration_mtime = 0
_calibration_path = None


def load_calibration() -> Dict[str, Any]:
    """Load calibration settings from file.

    Returns default values if file doesn't exist or can't be parsed.
    Uses mtime-based caching to avoid redundant file reads.
    """
    global _calibration_cache, _calibration_mtime, _calibration_path

    defaults = {
        'verification': {
            'default_budget': 1,
            'max_budget': 5,
            'budgets_by_type': {
                'cloud': 2,
                'database': 2,
                'powershell': 1,
                'unknown': 1,
            }
        },
        'safety': {
            'auto_inject_sources': True,
            'log_corrections': True,
            'generate_summaries': True,
            'propose_evolutions': True,
            'autonomous_learning': True,
        },
        'patterns': {
            'source_injection_threshold': 0.7,
            'max_injected_sources': 3,
        },
        'performance': {
            'total_sessions': 0,
            'corrections_received': 0,
            'successful_completions': 0,
            'hallucination_catches': 0,
        },
        'quality_thresholds': {
            'file_size_yellow': 400,
            'file_size_red': 600,
            'ignore_globs': [],
        }
    }

    path = get_calibration_path()
    if not os.path.exists(path):
        return defaults

    try:
        current_mtime = os.path.getmtime(path)
        if (
            _calibration_cache is not None
            and current_mtime == _calibration_mtime
            and path == _calibration_path
        ):
            return _calibration_cache

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        parsed = parse_yaml_frontmatter(content)

        # Merge parsed with defaults (parsed takes precedence)
        result = defaults.copy()
        for section, values in parsed.items():
            if section in result and isinstance(values, dict):
                if isinstance(result[section], dict):
                    result[section].update(values)
                else:
                    result[section] = values
            else:
                result[section] = values

        _calibration_cache = result
        _calibration_mtime = current_mtime
        _calibration_path = path
        return result
    except Exception:
        return defaults


def get_verification_budget(task_type: str) -> int:
    """Get verification budget for a task type.

    Args:
        task_type: Type of task (e.g. cloud, database, powershell, unknown — usually the matched grounding pattern's topic)

    Returns:
        Number of verification cycles allowed before human gate
    """
    calibration = load_calibration()
    budgets = calibration.get('verification', {}).get('budgets_by_type', {})
    default = calibration.get('verification', {}).get('default_budget', 1)

    return budgets.get(task_type.lower(), default)


def is_safety_enabled(setting: str) -> bool:
    """Check if a safety setting is enabled.

    Args:
        setting: One of auto_inject_sources, log_corrections,
                 generate_summaries, propose_evolutions

    Returns:
        True if enabled, False otherwise
    """
    calibration = load_calibration()
    return calibration.get('safety', {}).get(setting, True)


def get_pattern_threshold() -> float:
    """Get confidence threshold for pattern matching."""
    calibration = load_calibration()
    return calibration.get('patterns', {}).get('source_injection_threshold', 0.7)


def get_max_injected_sources() -> int:
    """Get maximum number of sources to inject."""
    calibration = load_calibration()
    return calibration.get('patterns', {}).get('max_injected_sources', 3)


def get_interval(name: str, default: int) -> int:
    """Return a numeric interval from the ``intervals:`` block in calibration.

    Returns ``default`` when the block or key is missing, or when the stored
    value cannot be coerced to int.
    """
    calibration = load_calibration()
    try:
        return int(calibration.get('intervals', {}).get(name, default))
    except (TypeError, ValueError):
        return default


def get_detector_config(name: str) -> Dict[str, Any]:
    """Return the per-detector review-gate config for ``name``.

    Reads ``detectors.<name>`` from calibration. Cross-cutting rule 4 of the
    friction-reduction plan: each detector ships with its own review clock
    (sessions, firings) so detectors don't share review triggers.

    Returns a dict with ``enabled``, ``review_after_sessions``,
    ``review_after_firings``, ``firings_total``. Missing keys fall back to
    sensible defaults (enabled=True, thresholds=20/15, firings=0) so a
    detector fires on a fresh workstation without requiring manual
    calibration edits.
    """
    calibration = load_calibration()
    block = calibration.get('detectors', {}).get(name, {})
    return {
        'enabled': block.get('enabled', True),
        'review_after_sessions': block.get('review_after_sessions', 20),
        'review_after_firings': block.get('review_after_firings', 15),
        'firings_total': block.get('firings_total', 0),
    }


def increment_detector_firings(name: str, delta: int = 1) -> bool:
    """Increment ``detectors.<name>.firings_total`` by ``delta`` in calibration.

    Uses the same regex-substitution approach as ``update_performance_metrics``
    to avoid YAML round-tripping. Scoped to the detector-named block so the
    rewrite cannot accidentally hit a same-named key elsewhere.

    If the detector block (or the ``detectors:`` block entirely) is missing,
    auto-seeds it with default thresholds and ``firings_total = delta``.
    Without this, fresh-install workstations silently dropped firings until
    the user manually copied the template block (#150).

    Returns True on success, False if the file is missing or write failed.
    """
    if delta == 0:
        return True

    path = get_calibration_path()
    if not os.path.exists(path):
        return False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        block_pattern = rf'(\n  {re.escape(name)}:\n(?:    [^\n]+\n)*?    firings_total:\s*)(\d+)'
        match = re.search(block_pattern, content)
        if match:
            current = int(match.group(2))
            new_value = current + delta
            content = re.sub(block_pattern, rf'\g<1>{new_value}', content, count=1)
        else:
            # Auto-seed: detector (and possibly the whole detectors: block)
            # isn't in the file. Build a YAML stanza using the same defaults
            # get_detector_config returns, set firings_total to delta.
            defaults = get_detector_config(name)
            detector_yaml = (
                f"  {name}:\n"
                f"    enabled: {str(defaults['enabled']).lower()}\n"
                f"    review_after_sessions: {defaults['review_after_sessions']}\n"
                f"    review_after_firings: {defaults['review_after_firings']}\n"
                f"    firings_total: {delta}\n"
            )

            if re.search(r'(?m)^detectors:\s*$', content):
                # detectors: block exists — append the new detector under it.
                # Place it after the LAST nested key of detectors: (or right
                # under the header if the block is empty).
                content = re.sub(
                    r'(?ms)(^detectors:\s*\n(?:  [^\n]*\n)*)',
                    lambda m: m.group(1) + detector_yaml,
                    content,
                    count=1,
                )
            else:
                # No detectors: block at all — append a fresh one. Trailing
                # newline ensures separation from any existing content.
                if not content.endswith('\n'):
                    content += '\n'
                content += '\ndetectors:\n' + detector_yaml

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Invalidate the cache so the next read reflects the bump.
        global _calibration_cache, _calibration_mtime, _calibration_path
        _calibration_cache = None
        _calibration_mtime = 0
        _calibration_path = None

        return True
    except Exception:
        return False


def update_performance_metrics(
    sessions_delta: int = 0,
    corrections_delta: int = 0,
    successful_delta: int = 0,
    hallucination_delta: int = 0
) -> bool:
    """Update performance metrics in calibration file.

    Increments the specified counters by the given deltas.

    Args:
        sessions_delta: Amount to add to total_sessions
        corrections_delta: Amount to add to corrections_received
        successful_delta: Amount to add to successful_completions
        hallucination_delta: Amount to add to hallucination_catches

    Returns:
        True if update succeeded, False otherwise
    """
    path = get_calibration_path()
    if not os.path.exists(path):
        return False

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Update each metric using regex substitution
        def increment_metric(content: str, metric_name: str, delta: int) -> str:
            if delta == 0:
                return content
            pattern = rf'(\s+{metric_name}:\s*)(\d+)'
            match = re.search(pattern, content)
            if match:
                current = int(match.group(2))
                new_value = current + delta
                content = re.sub(pattern, rf'\g<1>{new_value}', content)
            return content

        content = increment_metric(content, 'total_sessions', sessions_delta)
        content = increment_metric(content, 'corrections_received', corrections_delta)
        content = increment_metric(content, 'successful_completions', successful_delta)
        content = increment_metric(content, 'hallucination_catches', hallucination_delta)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True
    except Exception:
        return False

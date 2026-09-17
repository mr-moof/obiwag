"""Vendor/technology detection for the learning pipeline.

Extracted from learning_detector.py in (134) to keep that file under the
600-line ceiling. Contains:
- Vendor/knowledge/introspection/reference-impl pattern tables
- ``detect_vendor_discoveries()``: vendor keyword + knowledge signal co-occurrence
- ``detect_reference_implementations()``: reusable integration patterns

Gotcha detection (correction-based) stays in ``learning_detector`` as it is
not vendor-oriented. Shared types (``Learning``, ``LearningType``,
``strip_system_content``) live in ``learning_types`` so both modules can
import them without forming a cycle (140).
"""

import re
from typing import List

from core.learning_types import Learning, LearningType, strip_system_content


# Technology indicators for domain-pattern routing.
# Each entry: (regex_pattern, category_name, target_pattern_file).
# These are GENERIC, widely-applicable technology buckets. Add your own
# domain-specific buckets here (e.g. a vendor SDK you integrate with) and point
# them at a docs/domain-patterns/<name>.md you maintain.
VENDOR_INDICATORS = [
    # Cloud providers
    (r'(aws\b|amazon\s*web\s*services|azure\b|gcp\b|google\s*cloud|cloud\s*provider)',
     'cloud', 'docs/domain-patterns/cloud.md'),
    # Virtualization / hypervisors
    (r'(hypervisor|virtual\s*machine|vm\s*host|kvm\b|qemu\b|libvirt|openstack|nova\b)',
     'virtualization', 'docs/domain-patterns/virtualization.md'),
    # Generic ticketing services
    (r'(ticketing\s*system|support\s*request)',
     'service-management', 'docs/domain-patterns/service-management.md'),
    # Authentication / Authorization protocols
    (r'(oidc|oauth2?|saml|ldap\b|kerberos|jwt\b|bearer\s*token|claims?\s*based|openid)',
     'authentication', 'docs/domain-patterns/authentication.md'),
]

# Knowledge-signal patterns that indicate discoverable vendor knowledge
# (not just casual mentions, but actual learnings)
KNOWLEDGE_SIGNALS = [
    r'(?:api|endpoint|field|column|parameter)\s+(?:property\s+)?(is|was|should\s+be|uses?)\s+',  # property name discoveries (requires technical noun prefix)
    r'(never|always|must|don.t)\s+(use|set|call|pass)',  # imperative rules
    r'(error|exception|warning).*?(benign|ignore|non-terminating|suppress)',  # error handling gotchas
    r'(output|returns?|gives?)\s+\w+\s*,?\s*not\s+\w+', # "uses X, not Y" corrections
    r'(reference|working)\s+(implementation|example|pattern)',  # reusable implementations
    r'(reusable|reuse)\s+(across|for|in)\s+',            # cross-project reuse signals
    r'(pkce|oauth|saml|oidc)\s+(flow|grant|exchange)',    # auth integration patterns
]

# Patterns in context windows that indicate code introspection, not real knowledge.
# If any of these match the context window, skip the vendor learning.
CODE_INTROSPECTION_PATTERNS = [
    r'Get-Module\s+-ListAvailable',              # PS module existence check
    r'Test-Path\b.*\.(ps[dm]?1|dll|exe)\b',     # file existence check
    r'Import-Module\b',                          # module loading
    r'\$\w+InScope\b',                           # scope-check variable
    r'if\s*\(\s*\$\w+\s*\)',                     # simple conditional check
    r'Write-Host\s+.*\[SKIP\]',                  # deploy skip message
    r'window\.\w+',                               # HTML/JS page content (rendered site)
]


def _snap_to_sentence_boundaries(text: str, start: int, end: int):
    """Adjust [start, end) to sit on sentence boundaries.

    Returns (new_start, new_end) on success, or ``None`` when content cannot be
    cleanly bounded within ``MAX_WALK`` chars. Detectors should drop a learning
    on ``None`` rather than emit content that begins mid-word — those entries
    erode reviewer trust and pile up in pending-learnings.

    Accepts: top-of-text as a sentence start, end-of-text as a sentence end,
    and indices that already sit just after sentence punctuation + whitespace.
    """
    MAX_WALK = 80
    BOUNDARY = ".!?\n"

    # Walk start backward to find the previous sentence end, then advance past whitespace.
    new_start = -1
    if start <= 0:
        new_start = 0
    else:
        for i in range(start, max(-1, start - MAX_WALK), -1):
            if i > 0 and text[i - 1] in BOUNDARY:
                j = i
                while j < len(text) and text[j] in " \t\n":
                    j += 1
                new_start = j
                break
        # Fallback: if we can reach doc-start within MAX_WALK, use it (top-of-text
        # is a valid sentence start). This rescues short transcripts where the
        # match lives in the first sentence and there's no preceding boundary.
        if new_start < 0 and start <= MAX_WALK:
            new_start = 0

    if new_start < 0:
        return None

    # Walk end forward to a sentence end (inclusive of the punctuation).
    new_end = -1
    for i in range(end, min(len(text), end + MAX_WALK)):
        if text[i] in BOUNDARY:
            new_end = i + 1
            break
    if new_end < 0:
        if end >= len(text):
            new_end = len(text)
        else:
            return None

    if new_end - new_start < 30:
        return None
    return new_start, new_end


def _looks_like_path_fragment(context_window: str, match_start: int, match_end: int) -> bool:
    """True when the context around a vendor match reads like a file path list.

    Filenames such as ``cloud-cost-viewer.md`` contain vendor keywords but
    carry no knowledge. Reject contexts that are dominated by path separators
    or filename-like tokens near the match (issue #141).
    """
    near_start = max(0, match_start - 40)
    near_end = min(len(context_window), match_end + 40)
    near = context_window[near_start:near_end]

    # Heuristic 1: heavy on path separators / filename extensions near the match.
    slash_count = near.count('/') + near.count('\\')
    if slash_count >= 3:
        return True

    # Heuristic 2: the match is inside a bare filename token (alnum+hyphens+dots,
    # no spaces) and surrounded by directory-listing-style text.
    token_start = match_start
    token_end = match_end
    while token_start > 0 and context_window[token_start - 1] not in ' \t\n':
        token_start -= 1
    while token_end < len(context_window) and context_window[token_end] not in ' \t\n':
        token_end += 1
    token = context_window[token_start:token_end]
    if re.match(r'^[\w./\\\-]+\.(md|py|ps1|psm1|json|yaml|yml|txt|log)$', token, re.IGNORECASE):
        return True

    return False


# Integration categories that indicate reusable reference implementations
# Each entry: (regex_pattern, category_name, target_pattern_file)
REFERENCE_IMPL_CATEGORIES = [
    (r'(oauth2?|pkce|authorization.code)\s+(flow|grant|exchange|redirect)',
     'auth-oauth', 'docs/domain-patterns/authentication.md'),
    (r'(mcp|model.context.protocol)\s+(server|client|tool)',
     'mcp-integration', 'docs/domain-patterns/mcp.md'),
    (r'(api\s+client|rest\s+client|http\s+client)\s+(wrapper|pattern|implementation)',
     'api-client', 'docs/domain-patterns/api-clients.md'),
    (r'(github\s+pages|static\s+site)\s+(deploy|publish|ci)',
     'static-site', 'docs/domain-patterns/static-site.md'),
    (r'(docker|container)\s+(compose|build|deploy|image)',
     'containerization', 'docs/domain-patterns/docker.md'),
]


def detect_vendor_discoveries(transcript: str) -> List[Learning]:
    """Detect vendor/technology knowledge from full transcript.

    Scans for vendor keywords co-occurring with knowledge-signal patterns.
    Both conditions must be met: vendor keyword present AND a knowledge
    signal within ~500 chars of the vendor match.

    Args:
        transcript: Full session transcript text

    Returns:
        List of Learning objects for vendor discoveries
    """
    learnings = []
    # Strip system content to avoid matching keywords in skill prompts
    cleaned = strip_system_content(transcript)
    transcript_lower = cleaned.lower()

    for vendor_pattern, category, target_file in VENDOR_INDICATORS:
        # Find all vendor keyword matches
        for vendor_match in re.finditer(vendor_pattern, transcript_lower, re.IGNORECASE):
            start = max(0, vendor_match.start() - 100)
            end = min(len(cleaned), vendor_match.end() + 100)
            context_window = cleaned[start:end]

            # Skip if context looks like code introspection, not real knowledge
            is_introspection = any(
                re.search(p, context_window, re.IGNORECASE)
                for p in CODE_INTROSPECTION_PATTERNS
            )
            if is_introspection:
                continue

            # Skip if the vendor keyword lives in a filename/path fragment —
            # no real knowledge can be learned from a path listing (issue #141).
            match_in_window = vendor_match.start() - start
            if _looks_like_path_fragment(
                context_window,
                match_in_window,
                match_in_window + (vendor_match.end() - vendor_match.start()),
            ):
                continue

            # Check if any knowledge signal appears near the vendor keyword
            for signal in KNOWLEDGE_SIGNALS:
                if re.search(signal, context_window, re.IGNORECASE):
                    # Extract a title from the context window
                    title = f"{category}: vendor knowledge discovered"
                    # Get a content snippet around the match, snapped to
                    # sentence boundaries (skip the learning if no clean
                    # boundary is findable — avoids mid-word starts that
                    # erode reviewer trust in pending-learnings).
                    snippet_start = max(0, vendor_match.start() - 100)
                    snippet_end = min(len(cleaned), vendor_match.end() + 200)
                    snapped = _snap_to_sentence_boundaries(cleaned, snippet_start, snippet_end)
                    if snapped is None:
                        break
                    snippet_start, snippet_end = snapped
                    content = cleaned[snippet_start:snippet_end].strip()
                    if len(content) > 200:
                        content = content[:197] + "..."

                    learnings.append(Learning(
                        type=LearningType.TECHNOLOGY,
                        title=title,
                        content=content,
                        target_file=target_file,
                        context=f"Vendor keyword '{vendor_match.group()}' + knowledge signal",
                        confidence=0.75,
                        metadata={'category': category, 'signal': signal}
                    ))
                    break  # One signal match per vendor occurrence is enough

            # One learning per vendor category is enough
            if any(l.metadata.get('category') == category for l in learnings):
                break

    return learnings


def detect_reference_implementations(transcript: str) -> List[Learning]:
    """Detect reusable reference implementations from session transcript.

    Looks for sessions that produced working code for known integration
    categories (OAuth, MCP, API clients, etc.) even without corrections.
    These are cross-project knowledge worth capturing.

    Args:
        transcript: Full session transcript text

    Returns:
        List of Learning objects for reference implementations
    """
    learnings = []
    # Strip system content to avoid matching keywords in skill prompts
    cleaned = strip_system_content(transcript)
    cleaned_lower = cleaned.lower()

    # Require at least one success signal in the cleaned transcript
    success_signals = [
        r'(reference|working)\s+(implementation|example|pattern)',
        r'(reusable|reuse)\s+(across|for|in)\s+',
        r'(successfully|works|working|functional)\s+(implement|deploy|configur|integrat)',
        r'(tested|verified|confirmed)\s+(and\s+)?(works|working|functional)',
    ]
    has_success = any(
        re.search(sig, cleaned_lower, re.IGNORECASE)
        for sig in success_signals
    )
    if not has_success:
        return learnings

    # The success signal must appear NEAR the integration keyword, not merely
    # somewhere in the transcript. The old code checked the two independently,
    # so an integration keyword in the injected skills catalog ("...Analytics MCP
    # servers...") could pair with an unrelated "works" elsewhere and emit a
    # bogus reference_impl. Proximity ties the success claim to the keyword.
    SUCCESS_PROXIMITY = 300

    for pattern, category, target_file in REFERENCE_IMPL_CATEGORIES:
        match = None
        for cand in re.finditer(pattern, cleaned_lower, re.IGNORECASE):
            lo = max(0, cand.start() - SUCCESS_PROXIMITY)
            hi = min(len(cleaned_lower), cand.end() + SUCCESS_PROXIMITY)
            window = cleaned_lower[lo:hi]
            if any(re.search(sig, window, re.IGNORECASE) for sig in success_signals):
                match = cand
                break
        if match:
            snippet_start = max(0, match.start() - 100)
            snippet_end = min(len(cleaned), match.end() + 200)
            # Snap to sentence boundaries; drop the learning if no clean
            # boundary is findable. Mid-word starts like "s PNG images..."
            # are unreviewable and pile up in pending-learnings.
            snapped = _snap_to_sentence_boundaries(cleaned, snippet_start, snippet_end)
            if snapped is None:
                continue
            snippet_start, snippet_end = snapped
            content = cleaned[snippet_start:snippet_end].strip()
            if len(content) > 200:
                content = content[:197] + "..."

            learnings.append(Learning(
                type=LearningType.PATTERN,
                title=f"{category}: reference implementation",
                content=content,
                target_file=target_file,
                context=f"Reference implementation detected for '{category}'",
                confidence=0.8,
                metadata={'category': category, 'detector': 'reference_impl'}
            ))

    return learnings

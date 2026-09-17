"""Anchor-keyword grounding and pattern-file invariants (synthetic fixtures).

Two tiers of keyword:

* ``anchor_keywords`` -- unambiguous product vocabulary ("widgetapi", "pwsh").
  One hit grounds.
* ``match_keywords`` -- ordinary English that only means something in
  combination ("fix", "done", "module"). Two hits needed.

The tiers are held apart by an arithmetic band, and that band is the trap this
file exists to guard: scoring is ``confidence * min(1, 0.7 + 0.1*hits)`` against
a threshold, so one supporting hit scores ``confidence * 0.8``. An anchored
pattern must sit in ``[threshold, threshold/0.8)`` or one tier silently
collapses into the other.

Tests go through the synthetic fixture files and through
``_load_pattern_file`` rather than hand-built dicts: a loader that drops
``anchor_keywords`` would otherwise leave every fixture pattern anchorless while
the dict-based tests all passed.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.calibration import get_pattern_threshold  # noqa: E402
from core.pattern_matcher import (  # noqa: E402
    _load_pattern_file,
    detect_task_type,
    get_injection_text,
    load_patterns,
    match_task_to_patterns,
    select_injections,
)
from core.paths import get_obiwag_repo_path  # noqa: E402

REPO = HOOKS_DIR.parent
PATTERN_FILES = sorted((HOOKS_DIR / 'tests' / 'fixtures' / 'patterns').glob('*.md')) if REPO else []
SHIPPED = [_load_pattern_file(str(p)) for p in PATTERN_FILES]
ANCHORED = [p for p in SHIPPED if p and p.get('anchor_keywords')]

# 0.8 is the boost applied to a single supporting hit.
SINGLE_SUPPORTING_BOOST = 0.8

pytestmark = pytest.mark.skipif(not PATTERN_FILES, reason='pattern files not found')


def _ids(patterns):
    return [p.get('topic') for p in patterns]


def _single_hit_keywords(pattern, keywords):
    """Keywords that, used alone as a prompt, produce exactly ONE hit.

    Needed because keywords overlap: the prompt "pipeline failed" matches both
    ``pipeline failed`` and ``failed``, so it is a two-hit prompt and says
    nothing about single-hit behaviour. The invariant under test is about the
    hit count, not the word count.
    """
    from core.pattern_matcher import _count_hits

    all_keywords = list(pattern.get('anchor_keywords', [])) + list(pattern.get('match_keywords', []))
    return [kw for kw in keywords if _count_hits(all_keywords, kw.lower()) == 1]


class TestPatternFileInvariants:
    """Every fixture pattern file must satisfy these, forever."""

    @pytest.mark.parametrize('pattern', SHIPPED, ids=_ids(SHIPPED))
    def test_every_source_path_resolves(self, pattern):
        """A pattern citing a deleted doc misleads whoever reads it next.

        Four entries pointed into platforms/github-copilot/ for two releases
        after that tree was removed.
        """
        for source in pattern.get('sources', []):
            path = source.get('path') if isinstance(source, dict) else source
            if not path:
                continue
            assert (REPO / path).exists(), f"{pattern['topic']}: missing source {path}"

    @pytest.mark.parametrize('pattern', SHIPPED, ids=_ids(SHIPPED))
    def test_every_pattern_has_injection_text(self, pattern):
        """A pattern with no injectable body can never ground anything.

        The body comes from '## Injection Text' or, failing that, the
        '## Key Grounding Points' fallback -- what matters is that ONE of them
        yields text. The github pattern fixture with neither: it matched, cleared
        the threshold, and contributed nothing.
        """
        assert pattern.get('injection_text', '').strip(), (
            f"{pattern['topic']}: no '## Injection Text' or '## Key Grounding Points' body"
        )

    @pytest.mark.parametrize('pattern', SHIPPED, ids=_ids(SHIPPED))
    def test_keyword_tiers_are_disjoint(self, pattern):
        """A keyword lives in exactly one tier, so hit counting is unambiguous."""
        overlap = set(pattern.get('anchor_keywords', [])) & set(pattern.get('match_keywords', []))
        assert not overlap, f"{pattern['topic']}: {sorted(overlap)} in both tiers"

    @pytest.mark.parametrize('pattern', SHIPPED, ids=_ids(SHIPPED))
    def test_keywords_are_lowercase(self, pattern):
        """Matching lowercases the task text, so an uppercase keyword never fires."""
        for kw in list(pattern.get('anchor_keywords', [])) + list(pattern.get('match_keywords', [])):
            assert kw == kw.lower(), f"{pattern['topic']}: '{kw}' would never match"


class TestConfidenceBand:
    """The invariant that keeps the two tiers apart."""

    @pytest.mark.parametrize('pattern', ANCHORED, ids=_ids(ANCHORED))
    def test_anchor_can_fire(self, pattern):
        """Lower bound: below the threshold an anchor is silently mute."""
        threshold = get_pattern_threshold()
        assert pattern['confidence'] >= threshold, (
            f"{pattern['topic']}: confidence {pattern['confidence']} < threshold "
            f"{threshold}, so its anchors can never ground"
        )

    @pytest.mark.parametrize('pattern', ANCHORED, ids=_ids(ANCHORED))
    def test_one_supporting_hit_cannot_fire(self, pattern):
        """Upper bound: at/above threshold/0.8 a lone supporting word grounds.

        This is the check that catches raising an anchored pattern's confidence
        to 0.9 -- at a 0.7 gate that makes single hits on 'incident' or 'sn'
        enough, and the supporting tier stops existing.
        """
        threshold = get_pattern_threshold()
        ceiling = threshold / SINGLE_SUPPORTING_BOOST
        assert pattern['confidence'] < ceiling, (
            f"{pattern['topic']}: confidence {pattern['confidence']} >= {ceiling:.4f}, "
            f"so ONE supporting keyword would ground on its own"
        )


class TestGroundingBehaviour:
    """End-to-end through the real files: does the right thing fire?"""

    @pytest.mark.parametrize('pattern', ANCHORED, ids=_ids(ANCHORED))
    def test_each_anchor_grounds_alone(self, pattern):
        """Every declared anchor must ground by itself -- that is the promise."""
        for anchor in pattern['anchor_keywords']:
            matches = match_task_to_patterns(anchor, [pattern])
            assert matches, f"{pattern['topic']}: anchor '{anchor}' did not match"
            assert matches[0][1] >= get_pattern_threshold(), (
                f"{pattern['topic']}: anchor '{anchor}' scored {matches[0][1]:.3f}, "
                f"below the {get_pattern_threshold()} gate"
            )

    @pytest.mark.parametrize('pattern', ANCHORED, ids=_ids(ANCHORED))
    def test_single_supporting_keyword_does_not_ground(self, pattern):
        for kw in _single_hit_keywords(pattern, pattern.get('match_keywords', [])):
            matches = match_task_to_patterns(kw, [pattern])
            assert matches and matches[0][1] < get_pattern_threshold(), (
                f"{pattern['topic']}: supporting keyword '{kw}' alone scored "
                f"{matches[0][1]:.3f} and grounded"
            )

    def test_anchorless_patterns_still_need_two_hits(self):
        """The intent patterns must keep their combination requirement.

        Grounding on a common verb alone would make
        the debugging pattern fire on ordinary conversation.
        """
        for pattern in SHIPPED:
            if pattern.get('anchor_keywords'):
                continue
            for kw in _single_hit_keywords(pattern, pattern.get('match_keywords', [])):
                matches = match_task_to_patterns(kw, [pattern])
                assert matches and matches[0][1] < get_pattern_threshold(), (
                    f"{pattern['topic']}: single keyword '{kw}' grounded"
                )

    @pytest.mark.parametrize('prompt,topic', [
        ("let's work on CanvasAPI", 'canvasapi'),
        ('fix this powershell module', 'powershell'),
        ('update the example job email out of WidgetAPI', 'widgetapi'),
        ('check the storagehost datastore', 'storageapi'),
    ])
    def test_single_product_mention_grounds(self, prompt, topic):
        """The user-visible symptom of #202: one product word injected nothing."""
        topics = [t for t, _, _ in select_injections(prompt)]
        assert topic in topics, f'{prompt!r} did not ground {topic}; got {topics}'

    @pytest.mark.parametrize('prompt', [
        'onboard a new user to sample-machine',          # \bvm\b matched inside sample-machine
        'post it and tag a reviewer on the thread to fix',
        'can you look at that spreadsheet again',
    ])
    def test_ordinary_prompts_do_not_ground(self, prompt):
        assert select_injections(prompt) == [], f'{prompt!r} grounded something'


class TestLoaderAndOrdering:
    """The loader must carry the new field, and ranking must be reproducible."""

    def test_anchor_keywords_survive_file_loading(self, tmp_path):
        """Regression: the scoring change is inert if the loader drops the field.

        Every other test in the original suite built pattern dicts inline, so
        this gap would not have shown up anywhere.
        """
        f = tmp_path / 'thing.md'
        f.write_text(
            '---\n'
            'topic: thing\n'
            'confidence: 0.85\n'
            'anchor_keywords:\n'
            '  - widgetron\n'
            'match_keywords:\n'
            '  - widget\n'
            '---\n'
            '## Injection Text\n'
            '```\n'
            'Use the widgetron API.\n'
            '```\n',
            encoding='utf-8')

        loaded = _load_pattern_file(str(f))
        assert loaded['anchor_keywords'] == ['widgetron']

        matches = match_task_to_patterns('deploy widgetron today', [loaded])
        assert matches[0][1] == pytest.approx(0.85), 'anchor did not bypass the curve'

    def test_ranking_is_independent_of_listing_order(self, monkeypatch, tmp_path):
        """Ties are routine once anchors exist; the answer must not depend on
        the order the filesystem happens to return."""
        import core.pattern_matcher as pm

        forward = load_patterns()
        real_listdir = pm.os.listdir
        monkeypatch.setattr(pm.os, 'listdir', lambda d: list(reversed(real_listdir(d))))
        reversed_order = load_patterns()

        assert [p['topic'] for p in forward] == [p['topic'] for p in reversed_order]

    def test_detect_task_type_is_stable_across_tied_patterns(self):
        tied = [
            {'topic': 'bbb', 'confidence': 0.85, 'anchor_keywords': ['thing'],
             'match_keywords': [], 'sources': [], 'injection_text': 'b'},
            {'topic': 'aaa', 'confidence': 0.85, 'anchor_keywords': ['thing'],
             'match_keywords': [], 'sources': [], 'injection_text': 'a'},
        ]
        first = match_task_to_patterns('thing', tied)[0][0]['topic']
        second = match_task_to_patterns('thing', list(reversed(tied)))[0][0]['topic']
        assert first == second == 'aaa', 'tie-break must be deterministic'

    def test_anchorless_scores_are_unchanged_and_order_is_deterministic(self):
        """Two guarantees, stated separately because only one is 'no change'.

        SCORES for anchorless patterns must match the old formula exactly. Tie
        ORDER deliberately changed: it used to fall out of a stable sort over
        load order, so which pattern won depended on os.listdir. Single-pattern
        tests cannot see either property -- this uses several at once.
        """
        anchorless = [
            {'topic': t, 'confidence': 0.85, 'anchor_keywords': [],
             'match_keywords': ['alpha', 'beta'], 'sources': [], 'injection_text': t}
            for t in ('zeta', 'alpha_topic', 'mid')
        ]
        ranked = match_task_to_patterns('alpha beta', anchorless)

        # Old formula: 0.85 * min(1, 0.7 + 0.1*2) = 0.765, for every one of them.
        assert [round(score, 4) for _, score in ranked] == [0.765] * 3

        # Deterministic regardless of the order the caller supplied.
        forward = [p['topic'] for p, _ in ranked]
        backward = [p['topic'] for p, _ in match_task_to_patterns(
            'alpha beta', list(reversed(anchorless)))]
        assert forward == backward == ['alpha_topic', 'mid', 'zeta']

    def test_more_evidence_outranks_less_at_equal_score(self):
        patterns = [
            {'topic': 'one_anchor', 'confidence': 0.85, 'anchor_keywords': ['alpha'],
             'match_keywords': [], 'sources': [], 'injection_text': 'x'},
            {'topic': 'two_anchors', 'confidence': 0.85, 'anchor_keywords': ['alpha', 'beta'],
             'match_keywords': [], 'sources': [], 'injection_text': 'y'},
        ]
        ranked = match_task_to_patterns('alpha beta', patterns)
        assert ranked[0][0]['topic'] == 'two_anchors'


class TestLoadSources:
    """Patterns must load without a source-repo checkout.

    load_patterns() previously read only the source repo plus an absent user
    override dir, so a deployed install with no checkout loaded zero patterns and
    grounding was silently dev-workstation-only.
    """

    def _write_pattern(self, directory, topic, text):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{topic}.md').write_text(
            f'---\ntopic: {topic}\nconfidence: 0.85\n'
            f'anchor_keywords:\n  - {topic}\n---\n'
            f'## Injection Text\n```\n{text}\n```\n',
            encoding='utf-8')

    def test_loads_from_deployed_dir_without_a_repo(self, tmp_path, monkeypatch):
        import core.pattern_matcher as pm

        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        monkeypatch.setattr(pm, 'get_repo_path', lambda: None)
        self._write_pattern(tmp_path / '.obi' / 'patterns', 'deployedonly', 'from deploy')

        topics = {p['topic']: p for p in pm.load_patterns()}
        assert 'deployedonly' in topics
        assert 'from deploy' in topics['deployedonly']['injection_text']

    def test_source_repo_overrides_the_deployed_copy(self, tmp_path, monkeypatch):
        """An edit in the repo must win before it is deployed."""
        import core.pattern_matcher as pm

        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        repo = tmp_path / 'repo' / '.obi'
        monkeypatch.setattr(pm, 'get_repo_path', lambda: str(repo))
        self._write_pattern(tmp_path / '.obi' / 'patterns', 'thing', 'deployed text')
        self._write_pattern(repo / 'patterns', 'thing', 'repo text')

        topics = {p['topic']: p for p in pm.load_patterns()}
        assert 'repo text' in topics['thing']['injection_text']

    def test_user_override_wins_over_both(self, tmp_path, monkeypatch):
        import core.pattern_matcher as pm

        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        repo = tmp_path / 'repo' / '.obi'
        monkeypatch.setattr(pm, 'get_repo_path', lambda: str(repo))
        self._write_pattern(tmp_path / '.obi' / 'patterns', 'thing', 'deployed text')
        self._write_pattern(repo / 'patterns', 'thing', 'repo text')
        self._write_pattern(tmp_path / '.obi' / 'memory' / 'patterns', 'thing', 'user text')

        topics = {p['topic']: p for p in pm.load_patterns()}
        assert 'user text' in topics['thing']['injection_text']


class TestEmptyInjectionsAndCap:
    """An empty pattern must not consume one of the injection slots."""

    def test_empty_injection_never_takes_a_slot(self, monkeypatch):
        import core.pattern_matcher as pm

        patterns = [
            {'topic': 'silent', 'confidence': 0.9, 'anchor_keywords': ['trigger'],
             'match_keywords': [], 'sources': [], 'injection_text': ''},
            {'topic': 'speaks', 'confidence': 0.85, 'anchor_keywords': ['trigger'],
             'match_keywords': [], 'sources': [], 'injection_text': 'real guidance'},
        ]
        monkeypatch.setattr(pm, 'load_patterns', lambda: patterns)
        monkeypatch.setattr(pm, 'get_max_injected_sources', lambda: 1)

        text = get_injection_text('trigger')
        assert text is not None, 'the empty pattern ate the only slot'
        assert 'real guidance' in text

    def test_select_injections_is_not_truncated(self, monkeypatch):
        """Callers that dedupe need the full ranked list, or a fresh topic can
        be starved behind already-claimed ones."""
        import core.pattern_matcher as pm

        patterns = [
            {'topic': f't{i}', 'confidence': 0.85, 'anchor_keywords': ['trigger'],
             'match_keywords': [], 'sources': [], 'injection_text': f'text {i}'}
            for i in range(5)
        ]
        monkeypatch.setattr(pm, 'load_patterns', lambda: patterns)
        monkeypatch.setattr(pm, 'get_max_injected_sources', lambda: 3)

        assert len(select_injections('trigger')) == 5
        assert get_injection_text('trigger').count('[Pattern:') == 3

"""Synthetic correction and comparison fixtures; no recorded user messages."""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.memory_reader import log_correction  # noqa: E402
from core.transcript_analyzer import detect_corrections_in_human_turns  # noqa: E402


def _human(text):
    return [{'role': 'human', 'content': text}]


class TestInsteadIsNotAlwaysACorrection:
    """`instead, use X` redirects. `X instead of Y` compares."""

    @pytest.mark.parametrize('text', [
        'can you do "last 10" instead of "last 5"? i like it',
        'can you make it run on Sunday at 10 PM instead of Monday at 6:30',
        'can you make that a pretty HTML page instead of a .md',
        'we should schedule the refresh to run daily instead of weekly',
        'keep "the example project" instead of wave 1/2',
    ])
    def test_comparison_is_not_a_correction(self, text):
        """Synthetic preferences must not be classified as corrections."""
        assert detect_corrections_in_human_turns(_human(text)) == [], (
            f'{text!r} is a preference, not a correction'
        )

    @pytest.mark.parametrize('text', [
        'run it daily Instead Of weekly',                 # casing
        'run it daily instead  of weekly',                # extra space
        'use A instead of B, and C instead of D',         # twice in one line
        'see https://example.com/instead-of-that',        # hyphenated, inside a URL
        'the insteadof helper is fine',                   # inside a word
        'see docs/instead/of/this',                       # slash-separated path
        'path is /instead/of/that',                       # leading-slash path
        'the instead—of debate',                     # em dash
        "instead's meaning is subtle",                    # possessive
    ])
    def test_comparison_variants_are_not_corrections(self, text):
        """`\\b` fires on ANY non-word character, so a bare `\\s+of` lookahead let
        every non-space spelling of "instead of" through -- hyphen, slash, em
        dash -- plus the possessive. The separator class and the second lookahead
        cover them."""
        assert detect_corrections_in_human_turns(_human(text)) == [], (
            f'{text!r} should not read as a correction'
        )

    @pytest.mark.parametrize('text', [
        'instead, use Get-ExampleRecord',
        'instead use the wrapper method',
        'instead try the bulk endpoint',
        # Trailing form -- English puts the redirection on either side of the
        # verb. Requiring instead+verb caught only half of these.
        'the selected label is too vague. use the detailed label instead',
        'that column is wrong, use created_on instead',
        'do it with the wrapper instead',
        # Interrupted by a dash. A permissive separator class read "- of" as
        # "instead of" and suppressed a genuine correction, so the lookahead
        # matches "instead of" only as a unit.
        'Instead - of course, use the wrapper',
        'use the wrapper instead - of the two, it is safer',
        'no, use the wrapper',
        "actually, the endpoint doesn't exist",
        'you forgot to add the test',
    ])
    def test_genuine_redirections_still_detected(self, text):
        """Tightening precision must not cost recall on real corrections."""
        assert detect_corrections_in_human_turns(_human(text)), (
            f'{text!r} is a genuine correction and must still be detected'
        )


class TestCorrectionDedupe:
    """Stop re-analyzes the full transcript every firing; the log must not grow
    one entry per analysis."""

    @pytest.fixture(autouse=True)
    def _isolated(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        self.corrections = tmp_path / '.obi' / 'memory' / 'corrections'

    def _records(self):
        files = list(self.corrections.glob('*.jsonl'))
        out = []
        for f in files:
            out += [json.loads(l) for l in f.read_text(encoding='utf-8').splitlines() if l.strip()]
        return out

    def test_same_message_logged_once(self):
        for _ in range(5):
            log_correction('s1', 'widgetapi', 'unknown', 'no, use the wrapper')
        assert len(self._records()) == 1

    def test_dedupes_across_sessions(self):
        """Repeated messages across sessions must not duplicate the log."""
        log_correction('s1', 'widgetapi', 'unknown', 'no, use the wrapper')
        log_correction('s2', 'widgetapi', 'unknown', 'no, use the wrapper')
        log_correction('s3', 'widgetapi', 'unknown', 'no, use the wrapper')
        assert len(self._records()) == 1

    def test_whitespace_variants_are_the_same_correction(self):
        log_correction('s1', 'x', 'unknown', 'no,  use   the wrapper')
        log_correction('s2', 'x', 'unknown', 'no, use the wrapper')
        assert len(self._records()) == 1

    def test_distinct_corrections_are_all_kept(self):
        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')
        log_correction('s1', 'x', 'unknown', 'you forgot to add the test')
        log_correction('s1', 'x', 'unknown', "actually, the endpoint doesn't exist")
        assert len(self._records()) == 3

    def test_midnight_rollover_never_loses_a_correction(self, monkeypatch):
        """Dedupe is scoped to today's file, so a session crossing midnight must
        still record -- and a correction recurring on a NEW day is signal, not
        noise, so re-recording it is correct."""
        import datetime as real_datetime

        import core.memory_reader as mr

        class FakeDT(real_datetime.datetime):
            _now = real_datetime.datetime(2026, 7, 30, 23, 59, 50)

            @classmethod
            def now(cls, tz=None):
                return cls._now

        monkeypatch.setattr(mr, 'datetime', FakeDT)

        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')
        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')
        assert len(self._records()) == 1, 'same-day repeat should collapse'

        FakeDT._now = real_datetime.datetime(2026, 7, 31, 0, 0, 10)
        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')

        days = sorted(p.name for p in self.corrections.glob('*.jsonl'))
        assert days == ['2026-07-30.jsonl', '2026-07-31.jsonl']
        assert len(self._records()) == 2, 'crossing midnight must not lose it'

    def test_dedupe_failure_prefers_logging_over_losing(self, monkeypatch):
        """An unreadable log must not silently swallow a correction."""
        import core.memory_reader as mr

        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')

        def boom(*a, **k):
            raise OSError('unreadable')

        monkeypatch.setattr(mr, 'open', boom, raising=False)
        # Must not raise; the correction is still recorded.
        log_correction('s2', 'x', 'unknown', 'a different correction entirely')
        assert len(self._records()) >= 2

    @pytest.mark.parametrize('corrupt,label', [
        (b'\xff\xfe not valid utf8\n', 'invalid UTF-8'),
        (b'"just a string"\n', 'JSON scalar, not an object'),
        (b'[1, 2, 3]\n', 'JSON list, not an object'),
        (b'{"user_message": 12345}\n', 'non-string user_message'),
    ])
    def test_corrupt_log_content_never_loses_a_correction(self, corrupt, label):
        """Fail open on BAD CONTENT, not just I/O errors.

        Each of these used to raise out of the dedupe helper --
        UnicodeDecodeError or AttributeError -- discarding the correction before
        it was ever appended. The docstring claimed best-effort; it was not.
        """
        import datetime as dt

        self.corrections.mkdir(parents=True, exist_ok=True)
        day = dt.datetime.now().strftime('%Y-%m-%d')
        (self.corrections / f'{day}.jsonl').write_bytes(corrupt)

        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')

        raw = (self.corrections / f'{day}.jsonl').read_text(
            encoding='utf-8', errors='replace')
        assert 'use the wrapper' in raw, f'{label}: correction was lost'

    def test_scan_and_append_share_one_lock(self):
        """The normal path is atomic: one lock covers the scan AND the append.

        Without that, two Stop processes finishing together both scan, both miss,
        and both append -- reintroducing the duplication the dedupe exists to
        prevent.
        """
        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')
        log_correction('s2', 'x', 'unknown', 'no, use the wrapper')
        assert len(self._records()) == 1

    def test_contended_lock_keeps_the_correction_and_may_duplicate(self):
        """Documents the DELIBERATE tradeoff on the fallback path.

        When the lock is still held after its budget, the correction is appended
        unlocked rather than dropped. That is not atomic -- a peer holding the
        lock can append the same text afterwards, yielding a duplicate. The
        alternative is losing a real correction, which is worse and unrecoverable.

        An earlier version of this test used a DISTINCT message, so it could never
        have observed the duplicate it claimed to rule out.
        """
        from core.session_state import _file_lock

        log_correction('s1', 'x', 'unknown', 'no, use the wrapper')
        day_file = next(self.corrections.glob('*.jsonl'))

        with _file_lock(str(day_file) + '.lock'):
            # Same text, lock held: the dedupe scan cannot run under the lock.
            log_correction('s2', 'x', 'unknown', 'no, use the wrapper')

        msgs = [r.get('user_message') for r in self._records()]
        assert 'no, use the wrapper' in msgs, 'the correction must never be lost'
        # Duplicate is the accepted cost of not dropping it. Pinned so a future
        # change to drop-on-contention fails loudly instead of silently.
        assert msgs.count('no, use the wrapper') >= 1

"""Keep prompt-grounding tests independent of installed user memories."""
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def synthetic_grounding(request, monkeypatch):
    if request.path.name not in {"test_user_prompt_submit.py", "test_pattern_anchors.py", "test_stop_task_type_end_to_end.py"}:
        return
    if request.path.name == "test_pattern_anchors.py" and request.cls and request.cls.__name__ == "TestLoadSources":
        return
    import core.pattern_matcher as matcher
    fixture_root = Path(__file__).parent / "fixtures"
    monkeypatch.setattr(matcher, "get_repo_path", lambda: str(fixture_root))
    # Empty deployed and override roots ensure the only inputs are the fixtures.
    monkeypatch.setattr(matcher, "get_obi_root", lambda: fixture_root / "empty-runtime")

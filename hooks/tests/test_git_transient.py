"""Transport failures must not disguise credential or branch errors."""
from core.git_transient import coerce_output, transient_retry_alert, RETRY_ALERT

def test_transport_failure_is_advisory():
    assert transient_retry_alert("Bash", "git push", "error: RPC failed") == RETRY_ALERT

def test_auth_branch_and_non_git_failures_are_not_retried():
    for output in ("Authentication failed", "HTTP Basic: Access denied", "! [rejected] fetch first"):
        assert transient_retry_alert("Bash", "git push", output) is None
    assert transient_retry_alert("Read", "git push", "error: RPC failed") is None
    assert transient_retry_alert("Bash", "echo hello", "error: RPC failed") is None

def test_nested_output_is_preserved():
    assert coerce_output({"content": [{"text": "first"}], "error": "second"}) == "first\nsecond"

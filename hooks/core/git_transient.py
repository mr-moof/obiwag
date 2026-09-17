"""Normalize tool output and flag temporary Git transport failures.

Authentication failures are deliberately excluded. No hook redirects a push.
"""
import re

TRANSIENT_SIGNATURE = re.compile(r"error: RPC failed|The requested URL returned error: 5\d\d", re.I)
RETRY_ALERT = "[Transport] Git reported a temporary transport error. Verify remote state before a bounded retry."

def coerce_output(resp) -> str:
    """Flatten a PostToolUse ``tool_response``/``tool_error`` to searchable text.

    Handles the shapes a Bash result can take: a bare string, a dict with
    stdout/stderr/text/content/result/error, or a list of content blocks.
    """
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        parts = []
        for key in ("stdout", "stderr", "text", "content", "result", "error"):
            val = resp.get(key)
            if isinstance(val, str):
                parts.append(val)
            elif isinstance(val, (list, dict)):
                parts.append(coerce_output(val))
        return "\n".join(p for p in parts if p)
    if isinstance(resp, list):
        return "\n".join(coerce_output(b) for b in resp)
    return str(resp)


def transient_retry_alert(tool_name: str, command: str, output: str):
    if tool_name not in ("Bash", "PowerShell") or not command or not output:
        return None
    if not re.search(r"\b(?:git|gh)\b", command):
        return None
    return RETRY_ALERT if TRANSIENT_SIGNATURE.search(output) else None

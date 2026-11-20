"""Keep recognised speech out of log files unless the user opts in."""
from __future__ import annotations


def describe_text(text: str, enabled: bool) -> str:
    """Return *text* for the log only when transcript logging is enabled.

    System audio can contain private calls and meetings, so by default just
    the length is recorded; that is enough to debug the pipeline.
    """
    return text if enabled else f"<{len(text)} симв.>"

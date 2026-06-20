"""Quality evaluator for workflow conditions."""


def meets_quality_threshold(data: dict) -> str:
    """Return 'pass' if data meets quality threshold, else 'fail'."""
    if not data:
        return "fail"
    content = str(data.get("content", ""))
    if len(content) > 100:
        return "pass"
    return "fail"

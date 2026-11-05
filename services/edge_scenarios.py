"""Machine-readable scenario tags carried in the existing GradingCase.rationale field."""
import re

TAG = re.compile(r'^\[edge:([a-z][a-z0-9_]*)\]')


def edge_type_from_rationale(rationale: str) -> str | None:
    match = TAG.match(rationale or '')
    return match.group(1) if match else None


def tag_rationale(rationale: str, kind: str | None) -> str:
    if not kind:
        return rationale
    prefix = f'[edge:{kind}] '
    return (prefix + TAG.sub('', rationale or '').strip())[:500]

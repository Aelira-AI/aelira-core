"""The Gemini API key must travel in a header, never in the request URL.

httpx logs the full request URL at INFO. A key passed as a `?key=` query
parameter is therefore written verbatim to stdout, shipped to Loki, and
attached to Sentry breadcrumbs — every alt-text call leaking a live credential
to three sinks at once.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

CALLERS = [
    SRC / "ai" / "gemini_client.py",
    SRC / "ai" / "providers" / "gemini_provider.py",
    SRC / "education" / "image_alt_text.py",
]


def _post_calls(path: Path):
    """Yield every httpx post call node in the module."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "post":
            yield node


@pytest.mark.parametrize("path", CALLERS, ids=lambda p: p.name)
def test_no_api_key_passed_as_query_parameter(path):
    """Regression: `params={"key": ...}` puts the credential in the logged URL."""
    offenders = []
    for call in _post_calls(path):
        for kw in call.keywords:
            if kw.arg != "params" or not isinstance(kw.value, ast.Dict):
                continue
            for k in kw.value.keys:
                if isinstance(k, ast.Constant) and k.value == "key":
                    offenders.append(call.lineno)

    assert not offenders, (
        f"{path.name} passes the API key as a query parameter at line(s) "
        f"{offenders}; use headers={{'x-goog-api-key': ...}} instead"
    )


@pytest.mark.parametrize("path", CALLERS, ids=lambda p: p.name)
def test_generate_content_calls_send_the_key_header(path):
    """Every generateContent call must still authenticate, via the header."""
    header_calls = 0
    total_calls = 0
    for call in _post_calls(path):
        url = call.args[0] if call.args else None
        is_generate = isinstance(url, ast.JoinedStr) and any(
            isinstance(v, ast.Constant) and "generateContent" in v.value
            for v in url.values
        )
        if not is_generate:
            continue
        total_calls += 1
        for kw in call.keywords:
            if kw.arg == "headers" and isinstance(kw.value, ast.Dict):
                if any(
                    isinstance(k, ast.Constant) and k.value == "x-goog-api-key"
                    for k in kw.value.keys
                ):
                    header_calls += 1

    assert total_calls, f"no generateContent call found in {path.name}"
    assert header_calls == total_calls, (
        f"{path.name}: {total_calls - header_calls} of {total_calls} "
        "generateContent calls do not send the x-goog-api-key header"
    )

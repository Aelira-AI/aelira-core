"""Suspicious VBA calls are found with or without parentheses (#872)."""

import pytest

from src.security.document_validator import (
    DocumentValidator,
    ThreatLevel,
    _suspicious_vba_calls,
)
from tests.vba_fixtures import ooxml_with_vba

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "code",
    [
        'Shell "calc.exe"',
        "Shell(cmd)",
        "x = Shell (cmd, 1)",
        "Call Shell(c)",
        "Shell strCmd",
        'Set o = CreateObject("x")',
        'CreateObject "x"',
        'p = Environ$("TEMP")',
        'Dim s: s = "it\'s" : Shell s',
    ],
)
def test_suspicious_vba_call_matches_with_or_without_parentheses(code):
    assert _suspicious_vba_calls(code)


@pytest.mark.parametrize(
    "code",
    ["ShellSort arr", "' Shell out later", 'MsgBox "Shell"', "Rem Shell x", "x = 1"],
)
def test_word_shell_in_comments_strings_and_longer_names_is_not_a_call(code):
    assert _suspicious_vba_calls(code) == []


@pytest.mark.asyncio
async def test_shell_without_parentheses_makes_a_macro_critical():
    source = 'Sub AutoOpen()\r\n    Shell "calc.exe"\r\nEnd Sub\r\n'

    result = await DocumentValidator().validate("a.docx", ooxml_with_vba(source))

    assert result.threat_level == ThreatLevel.CRITICAL
    assert "office_macro_suspicious" in {f.category for f in result.findings}


@pytest.mark.parametrize(
    "code",
    [
        "Shell .Value",
        "Shell [A1]",
        "Shell -1",
        # An unbalanced quote must not hide the lines after it.
        'x = "abc\nShell "calc"\ny = "def"',
    ],
)
def test_call_with_any_first_argument_is_found(code):
    assert _suspicious_vba_calls(code) == ["Shell command execution"]


def test_assignment_to_the_name_is_not_a_call():
    assert _suspicious_vba_calls("Shell = 3") == []

import json
from unittest.mock import ANY

import pytest

from src.education.remediation.equation_image_source import (
    EquationImageIdentity,
    ValidatedEquationImage,
)


PAYLOAD = ValidatedEquationImage(
    jpeg_bytes=b"\xff\xd8\xff\xd9",
    mime_type="image/jpeg",
    source_sha256="a" * 64,
    normalized_sha256="b" * 64,
    width=10,
    height=10,
    identity=EquationImageIdentity(
        page_number=1,
        image_xref=7,
        image_index=0,
        occurrence_ordinal=0,
        bbox=(1.0, 2.0, 3.0, 4.0),
        occurrence_id="imgocc-v1-test",
    ),
)


class Client:
    purpose = "alt_text"
    provider = "gemini"
    model = "vision-model"

    def __init__(self, response):
        self.response = response
        self.calls = []

    def analyze_image_sync(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def success(content):
    return {
        "success": True,
        "content": content,
        "provider": "gemini",
        "model": "vision-model",
    }


def test_printed_equation_uses_only_purpose_bound_vision_and_returns_latex():
    from src.education.remediation.equation_recognizer import EquationRecognizer

    client = Client(success('{"classification":"printed_equation","latex":"x^2 + 1 = 0"}'))
    result = EquationRecognizer(client).recognize(PAYLOAD)

    assert result.classification == "printed_equation"
    assert result.latex == "x^2 + 1 = 0"
    assert result.provider == "gemini"
    assert result.model == "vision-model"
    assert client.calls == [
        {
            "image_data": PAYLOAD.jpeg_bytes,
            "prompt": ANY,
            "max_tokens": ANY,
        }
    ]
    assert "JSON" in client.calls[0]["prompt"]


def test_not_equation_requires_null_latex():
    from src.education.remediation.equation_recognizer import EquationRecognizer

    result = EquationRecognizer(
        Client(success('{"classification":"not_equation","latex":null}'))
    ).recognize(PAYLOAD)
    assert result.classification == "not_equation"
    assert result.latex is None


@pytest.mark.parametrize(
    "content",
    [
        '```json\n{"classification":"printed_equation","latex":"x"}\n```',
        'prefix {"classification":"printed_equation","latex":"x"}',
        '{"classification":"printed_equation","latex":"x"} suffix',
        '{"classification":"printed_equation","latex":"x","extra":1}',
        '{"classification":"printed_equation"}',
        '{"classification":"unknown","latex":"x"}',
        '{"classification":"not_equation","latex":"x"}',
        '{"classification":"printed_equation","latex":null}',
        '{"classification":"printed_equation","latex":""}',
        '{"classification":"printed_equation","latex":"x\\n+y"}',
        '{"classification":"printed_equation","latex":"x","latex":"y"}',
        '[{"classification":"printed_equation","latex":"x"}]',
    ],
)
def test_malformed_or_ambiguous_responses_fail_closed(content):
    from src.education.remediation.equation_recognizer import (
        EquationRecognitionRejected,
        EquationRecognizer,
    )

    with pytest.raises(EquationRecognitionRejected, match="invalid_provider_response"):
        EquationRecognizer(Client(success(content))).recognize(PAYLOAD)


def test_oversized_latex_fails_closed():
    from src.education.remediation.equation_recognizer import (
        EquationRecognitionRejected,
        EquationRecognizer,
    )

    content = json.dumps({"classification": "printed_equation", "latex": "x" * 9})
    with pytest.raises(EquationRecognitionRejected, match="invalid_provider_response"):
        EquationRecognizer(Client(success(content)), max_latex_chars=8).recognize(PAYLOAD)


@pytest.mark.parametrize(
    "client,response_code",
    [
        (None, "alt_text_client_unavailable"),
        (Client({"success": False, "error": "purpose_operation_mismatch"}), "provider_failure"),
        (Client({"success": False, "error": "audit_write_failed"}), "provider_failure"),
        (Client({"success": True}), "provider_failure"),
    ],
)
def test_missing_denied_or_audit_failed_client_fails_without_payload_leak(
    client, response_code
):
    from src.education.remediation.equation_recognizer import (
        EquationRecognitionRejected,
        EquationRecognizer,
    )

    with pytest.raises(EquationRecognitionRejected, match=response_code) as exc:
        EquationRecognizer(client).recognize(PAYLOAD)
    assert PAYLOAD.normalized_sha256 not in str(exc.value)
    assert "purpose_operation_mismatch" not in str(exc.value)
    assert "audit_write_failed" not in str(exc.value)


def test_non_alt_text_binding_is_rejected_before_transport():
    from src.education.remediation.equation_recognizer import (
        EquationRecognitionRejected,
        EquationRecognizer,
    )

    client = Client(success('{"classification":"printed_equation","latex":"x"}'))
    client.purpose = "remediation"
    with pytest.raises(EquationRecognitionRejected, match="purpose_mismatch"):
        EquationRecognizer(client).recognize(PAYLOAD)
    assert client.calls == []

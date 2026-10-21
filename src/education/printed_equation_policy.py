"""Immutable printed-equation second-reading consensus policy identity."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from src.education.canonical_mathml_version import CANONICAL_MATHML_VERSION

PRINTED_VERIFIER_POLICY_VERSION = "printed-equation-consensus-v1"
PRINTED_REQUIRED_AGREEMENT_COUNT = 2

# sha256 of the second reader's prompt in
# ``src.education.remediation.printed_equation_verifier``. The provider cache
# keys on prompt plus image, so a second reading that reused the primary prompt
# would be answered from the cache and agree with itself. The prompt text is
# pinned here so that changing it changes the policy and so cannot go unnoticed
# in stored evidence.
PRINTED_VERIFIER_PROMPT_SHA256 = (
    "1ed37bb104cb3a9e3608b9c8ae2415fa04b064fa0cd1ffc51e44604afe3de006"
)

_VERIFIER_POLICY = MappingProxyType(
    {
        "agreement": "exact_canonical_mathml",
        "canonical_mathml_version": CANONICAL_MATHML_VERSION,
        "required_agreement_count": PRINTED_REQUIRED_AGREEMENT_COUNT,
        "second_reading": "blind_distinct_prompt",
        "verifier_prompt_sha256": PRINTED_VERIFIER_PROMPT_SHA256,
    }
)
PRINTED_VERIFIER_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "policy_version": PRINTED_VERIFIER_POLICY_VERSION,
            **_VERIFIER_POLICY,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
).hexdigest()

__all__ = [
    "PRINTED_REQUIRED_AGREEMENT_COUNT",
    "PRINTED_VERIFIER_POLICY_SHA256",
    "PRINTED_VERIFIER_POLICY_VERSION",
    "PRINTED_VERIFIER_PROMPT_SHA256",
]

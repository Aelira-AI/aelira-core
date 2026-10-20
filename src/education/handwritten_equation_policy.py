"""Immutable handwriting-recognition consensus policy identity."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from src.education.canonical_mathml_version import CANONICAL_MATHML_VERSION
from src.education.handwritten_math_suitability import POLICY_SHA256

# v2: agreement is exact equality of canonical MathML v2 (redundant single-child
# groups collapsed), so the canonical-form version is part of the policy digest.
# Evidence recorded under v1 never validates against this policy.
HANDWRITTEN_VERIFIER_POLICY_VERSION = "handwritten-equation-consensus-v2"
HANDWRITTEN_REQUIRED_AGREEMENT_COUNT = 2

_VERIFIER_POLICY = MappingProxyType(
    {
        "agreement": "exact_canonical_mathml",
        "canonical_mathml_version": CANONICAL_MATHML_VERSION,
        "required_agreement_count": HANDWRITTEN_REQUIRED_AGREEMENT_COUNT,
        "suitability_policy_sha256": POLICY_SHA256,
    }
)
HANDWRITTEN_VERIFIER_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "policy_version": HANDWRITTEN_VERIFIER_POLICY_VERSION,
            **_VERIFIER_POLICY,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
).hexdigest()

__all__ = [
    "HANDWRITTEN_REQUIRED_AGREEMENT_COUNT",
    "HANDWRITTEN_VERIFIER_POLICY_SHA256",
    "HANDWRITTEN_VERIFIER_POLICY_VERSION",
]

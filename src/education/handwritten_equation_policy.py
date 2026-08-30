"""Immutable handwriting-recognition consensus policy identity."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

from src.education.handwritten_math_suitability import POLICY_SHA256

HANDWRITTEN_VERIFIER_POLICY_VERSION = "handwritten-equation-consensus-v1"
HANDWRITTEN_REQUIRED_AGREEMENT_COUNT = 2

_VERIFIER_POLICY = MappingProxyType(
    {
        "agreement": "exact_canonical_mathml",
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

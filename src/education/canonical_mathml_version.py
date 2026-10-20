"""Version identity of the canonical MathML form used for equation evidence.

The canonical form is produced by
``src.education.remediation.equation_verifier.canonicalize_mathml``. This
module holds only the version string so the immutable policy modules can fold it
into their digests without importing the verifier and its heavy dependencies.

Bump the version whenever the canonical form changes in a way that could make
previously stored MathML or agreement evidence mean something different:

``canonical-mathml-v1``
    Attribute allowlist and tag allowlist only. Braced groups stayed as
    single-child ``mrow`` wrappers, so ``x^{2}`` and ``x^2`` differed.
``canonical-mathml-v2``
    Attribute-free single-child ``mrow`` wrappers collapse to their child
    (except around a lone ``mo``), and ``mfrac`` may carry ``linethickness``.
"""

CANONICAL_MATHML_VERSION = "canonical-mathml-v2"

__all__ = ["CANONICAL_MATHML_VERSION"]

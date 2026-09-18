# Bounded LaTeX validation corpus

These eleven synthetic sources were supplied for the research tracked in #444
and selected for the PDF failure boundary in #445. `corpus.json` records each
source hash and compiler expectation. They contain no customer documents or
personal metadata. They follow this repository's license; no separate license
or external conformance certification is asserted.

M01 is the fraction control. M03/M04 distinguish nested scripts, M06 is a matrix,
M10 uses physics macros, M14 contains aligned equations and references, and M16
has a long expression with a final sentinel. N01–N04 exercise an undefined macro,
a missing include, a missing figure and malformed math. Missing assets are
intentional negative controls.

The PDFs produced directly from these sources are untagged. Successful compilation
must not make them accepted accessible PDFs. See
[validation scope and reproduction](../../../docs/testing/latex-pdf-validation.md).

The #446 tests also replay these sources through the real equation processor,
with AI disabled, to verify conservative evidence reporting and preserved source
hashes. They do not grade mathematical meaning. See
[evidence outcomes](../../../docs/testing/latex-evidence-outcomes.md).

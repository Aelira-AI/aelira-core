# Authored LaTeX metadata

LaTeX remediation does not infer language, title or authorship from filenames,
generated prose or application identity. Missing values require author review.
Existing language package options and source metadata are preserved. Structure
repairs copy only supported, unambiguous authored values into PDF metadata.
Original uploads remain unchanged.

## Supported profile

The `literal-authored-v1` profile reads literal preamble `title`, `author`,
`DocumentMetadata`, `hypersetup`, Babel and `setdefaultlanguage` declarations.
Recognized document-class language options are also retained. Babel's last
language is the default unless an explicit `main=` option is present. Language
names are mapped to language tags; conflicting declarations require review.

Macro-expanded metadata, conditional declarations, nested metadata expressions,
unsupported language options and declarations after the document begins are
refused by the export profile. This is a bounded parser, not a TeX interpreter.
It cannot infer metadata in included files or prove arbitrary package behavior.

HTML exports are reopened to verify the root language, title and author against
the source. Unknown fields remain absent. Literal `foreignlanguage` and
`otherlanguage` spans require matching language and text in the saved HTML,
including repeated occurrences. The mixed-language route selects Pandoc before
conversion. LaTeXML's installed binding cannot preserve this control and its
direct route refuses it. Lost span boundaries are never reconstructed by guesswork.

LaTeXML preprocessing retains the original Babel declaration as a comment and
restores document metadata from the untouched source after conversion. This
avoids the incompatible installed Babel binding without substituting English.
Known missing language packages are recorded as `language_environment_unavailable`,
distinct from ambiguous or unsupported source metadata.

PDF document language, Info title/author and XMP language/title/creator are saved
and reopened before the existing structural and independent validation gate.
No tagging flags are fabricated. Mixed-language PDF export is refused until
span-level structure-tree verification is supported. Unknown language does not
become English to satisfy a validator. The metadata inspection stage names its
profile and binds original-source and final-candidate hashes; public diagnostics
contain reason codes, not authored names or titles.

TeX runs in its attempt directory with source lookup supplied through `TEXINPUTS`.
This keeps cold format/font initialization in owned scratch and avoids the
format-helper failure caused by inherited `-output-directory`. Only the exact
benign format-initialization notices are exempted; nonzero exits, missing
dependencies, missing output and unknown warnings still block publication.

## Verification

`python scripts/smoke_latex_metadata.py` executes German, English, unknown,
mixed-language and conflicting-declaration controls through the actual converter
wrappers. The Docker CI matrix runs it as the production service user, with a
read-only root filesystem, fresh writable scratch and no network, on AMD64 and
ARM64. Its PDFs prove compilation and saved metadata only, not PDF/UA conformance.
Unit and route tests cover invented converter defaults, missing fields, unchanged
originals, explicit source-derived repairs and corruption/refusal controls.

The [queue corpus](latex-corpus-queue.md) retains the eleven original fixtures
with unknown language as refusal controls and separately identifies variants
with explicit synthetic source language. Equation-only previews do not claim
full document metadata preservation.

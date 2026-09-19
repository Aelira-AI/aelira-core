# Authored LaTeX figure and table relationships

Captions, filenames, comments and horizontal rules do not establish figure
alternatives or table headers. Source scanning keeps these findings unresolved
unless supported authored declarations exist. Automatic remediation never
generates a figure caption/alternative or guesses headers to clear a finding.
Author review remains required; source declarations alone do not verify an export.

## Supported HTML profile

`literal-relationships-v1` supports the following explicit declarations from the
[LaTeX tagging interfaces](https://tagging-project.latex-project.org/documentation/usage-instructions):

- A literal `\includegraphics[alt={...}]{image.png}` alternative, or an explicit
  `artifact` option for an intentional decoration. An empty alternative without
  that declaration remains unresolved. Alternatives and decorative declarations
  cannot conflict.
- Rectangular literal `tabular` cells with `l`, `c` or `r` columns, preceded
  immediately by `\tagpdfsetup{table/header-rows={1},table/header-columns={1}}`.
  Either header axis may be declared; indices are one-based. Visual rules alone
  supply neither axis.

This profile chooses Pandoc before conversion and uses a minimal HTML template.
It matches each unique literal image path to its source PNG/JPEG, embeds those
exact bytes, and applies the authored alternative or decorative role. Assets must
remain inside the source directory; network resources and ambiguous extension
selection are unsupported. It compares every table cell and its row/column order
before assigning explicit `th`, IDs and `headers` relationships. Column headers
above each cell and row headers to its left supply the declared associations.
It reopens the saved HTML and verifies the assets, alternatives, roles, cells,
unique IDs and header edges against the original source.

There is no fallback after detected loss. The diagnostic receipt names the profile,
binds source and saved-candidate hashes, and contains fixed reason codes rather
than authored text. Original source bytes and authored declarations are unchanged.
The TEX delivery path remains available for supported source repairs; a downloaded
TEX file is not evidence that its figure/table semantics were exported correctly.

For text/math HTML, LaTeXML uses its standard HTML5 stylesheet with only the
generated-branding template disabled. Its mascot therefore cannot add an image
absent from the source. Authored footer content and graphics remain subject to the
same checks as the rest of the document; no image is exempted by its CSS class.

## Refusal boundaries

Macro-expanded or conditional declarations, custom drawing environments, alternate
table dialects, spans/merged cells, nonliteral cell content and unsupported asset
transformations require review. This is a bounded parser, not a TeX interpreter.
Repeated image paths, changed cell order, extra graphical output, browser image
substitution (`picture`/`srcset`), hidden content and conflicting semantic roles
also refuse the candidate. Relationship-bearing HTML permits no scripts,
stylesheets or style blocks; relevant inline styles are limited to literal cell
alignment. These structural checks do not substitute for browser or AT testing.
Source/candidate files and embedded assets have size bounds, and tables are
limited to 4,096 cells.

PDF exports containing these source relationships are withheld with
`semantics_unsupported`: the existing PDF structural/independent validators do not
yet prove the identity of a source asset or table cell against marked PDF content.
Their checks remain mandatory for other supported PDFs. No PDF structure is
fabricated, and this change does not certify semantic equivalence or PDF/UA.

## Evidence

`scripts/smoke_latex_semantics.py` runs the actual converter wrappers in the
production-image CI matrix on AMD64 and ARM64. A synthetic diagram requires both
`A -> B` and `B -> C` in its authored alternative. The table control verifies that
value `4` refers to column `B` and row `Blue`, whose header refers to `Trial`.
Decorative, caption-only and visual-rule-only controls exercise both acceptance
and refusal. A real compiled PDF with the diagram is explicitly withheld.

Unit controls mutate saved assets, alternatives, cell values and header edges;
adversarial controls cover hidden content and ambiguous source syntax. The durable
queue corpus additionally requires N03's missing alternative to remain unresolved
and the whole result withheld even when language could be repaired. Tests establish declared relationship preservation,
not the truth of an author's description or usability with a screen reader.

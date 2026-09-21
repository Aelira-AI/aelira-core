# Complete authored LaTeX source projects

A LaTeX upload can depend on chapter files, figures, bibliographies and local
packages. The project workflow preserves the original ZIP and every accepted
member's bytes. It inventories the selected entry file and its dependencies
before preparing a separate analysis view. An incomplete project remains
retrievable with explicit unresolved dependency records; it is never silently
reduced to the entry file or individual math fragments.

## API and original-source retrieval

Authenticated workspace users submit a multipart request to
`POST /education/latex/projects` with `file` (a ZIP archive) and `entry_file`
(the exact, case-sensitive path to its main `.tex` file within the archive).
The response includes a scan ID and the project/original URLs. The durable local
worker checks the original archive digest before processing it.

| Route | Result |
| --- | --- |
| `GET /education/latex/projects/{scan_id}` | Dependency state, source manifest, conversion receipt and available download URLs |
| `GET /education/latex/projects/{scan_id}/original` | The exact original ZIP bytes, with an archive digest ETag |
| `GET /education/latex/projects/{scan_id}/html` | An accepted HTML candidate whose stored bytes match the conversion receipt, otherwise unavailable |

These routes apply the existing scan/workspace authorization. The report can be
retrieved while processing or after refusal. The original ZIP contains the raw TEX,
local classes/styles, bibliography and assets; extracting it restores the authored
source paths. There is no separate project-member download route. The existing
single-file TEX workflow remains separate.

Project remediation requests require manual source review. This workflow does not
rewrite a multi-file source project or publish a partially repaired project.
A scan or accepted HTML candidate still reports `human_review_required: true` and
`accessibility_status: not_verified`.

## Bounded archive and dependency profile

`literal-project-v1` accepts regular `.tex`, `.sty`, `.cls`, `.bib`, `.bst`, `.png`,
`.jpg`, `.jpeg`, `.pdf`, `.eps`, `.svg` and `.txt` files. Unreferenced accepted files
are preserved too. Directories do not permit links or special files. Structural
archive errors reject intake before extraction.

| Bound | Limit |
| --- | --- |
| Original archive | 32 MiB |
| Archive members, including directory entries | 256 |
| Each expanded file | 16 MiB |
| Total expanded files | 64 MiB |
| Compression ratio per member | 200:1 |
| Dependency depth | 32 |
| Resolved dependency edges | 4,096 |
| Source control-sequence tokens inspected across the graph | 16,384 |
| Accumulated flattened analysis cache | 64 MiB |

Absolute paths, traversal, backslashes, unsafe cross-platform names, symbolic or
special links, duplicate names, case collisions, encrypted members, unsupported
compression methods and excessive expansion are rejected. The parser does not
execute TeX. Malformed dependency arguments stop parsing rather than repeatedly
scanning the same unclosed suffix.

The dependency graph recognizes literal `input`, `include`, `includegraphics`,
`bibliography`, `addbibresource`, `usepackage`, `RequirePackage`, `documentclass`,
`LoadClass` and `bibliographystyle` arguments. Nested inputs and spaces in filenames
are supported. It checks paths relative to the entry directory and the declaring
file's directory; distinct matching candidates produce `dependency_ambiguous`.
Extensionless image names likewise require exactly one supported candidate.

Missing files, cycles, unsupported encodings, dynamic arguments, conditional
loading, character-code escapes (`^^`) and unsupported search-path commands
remain explicit issues. A bounded, source-controlled list distinguishes recognized
system package/class names from absent local files; recognition is not proof that
a tool or package is installed. No dependencies are downloaded.

## Full-context HTML profile

`pandoc-project-html-v1` expands literal chapter inputs in a separate analysis copy
while retaining the complete preamble. A narrow local style/class profile supports
literal package/class wrappers and balanced macro declarations. Wrapper declarations
are removed from that analysis view; supported local loads are expanded and a base
`LoadClass` declaration becomes `documentclass`. Local option dispatch, mixed
local/system package lists, repeated local loads and unsupported package logic are
withheld. The source archive remains unchanged.

Resolved graphic references in the analysis view use explicit paths relative to
the ZIP root. Their path changes are recorded in the manifest. For supported
PNG/JPEG figures, the existing authored-relationship checks bind the exported image
to the exact source bytes and authored alternative or decorative declaration.
Those checks also retain their stricter source limitations, including refusing
macro-bearing documents that contain figure/table relationships.

Pandoc receives the complete analysis view with its reader sandbox enabled. The
launcher uses fixed options and resource limits, with no user filters, scripts,
templates or PDF engines. This is an isolated HTML conversion profile, not a
claim that arbitrary TeX execution is safe. Known unsupported content and converter
loss diagnostics withhold the candidate. Authored metadata and supported source
relationships are checked against the saved HTML before publication.

Bibliography files are preserved and their dependencies checked, but this HTML
profile does not render citations/bibliographies. A present bibliography produces
`bibliography_export_unsupported`; a missing one leaves dependencies unresolved.
Project PDF output is not offered by this workflow. An accepted HTML candidate is
not a PDF/UA or WCAG conformance assertion.

## Evidence and provenance

The manifest binds the original archive, each member, the source set and the
analysis view with SHA-256 digests. Dependency edges retain source/target paths and
local target digests. The conversion receipt binds analysis and saved output bytes,
the tool version, transformations and bounded refusal reasons. Stored-source
helpers verify the manifest and source bytes when loading an owned copy; the API
also retains the original archive's trusted digest in the durable job record.

`scripts/smoke_latex_projects.py` uses the real converter to exercise a nested
chapter project with spaces, a local class and authored math macro; a separate
nested PNG figure with exact image bytes and alternative; missing chapters,
figures, styles and bibliographies; preserved-but-unsupported bibliography output;
and a macro-generated external input refusal. A separate converter-boundary
control reads a positive sentinel while proving an external include is blocked
by the sandbox independently of the dependency parser. Each project control verifies unchanged
original bytes and source/archive digests. Accepted controls verify saved-output
digests and their actual text, MathML or embedded image content.

CI runs this smoke alongside the existing metadata and relationship smoke suites
in both AMD64 and ARM64 production images with networking disabled and a read-only
root. Unit controls cover unsafe archives, parser bounds, ambiguous graphs,
source-preserving working copies and stored-source tampering. API/worker tests
cover scoped retrieval, durable options, original-byte verification and refusal of
automatic multi-file source repairs. These are structural and integration checks;
they do not establish screen-reader usability or visual equivalence.

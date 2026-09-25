# Pinned PDF profile evaluation

This is research for #454. It does not change the application compiler, its
PDF/UA-1 profile, independent validation gate or TEX-only managed-artifact
publication boundary. PDF machine validation, source preservation, mathematical
meaning and reader usability are separate observations.

## Experiment

The experiment uses all 26 cases and 28 unchanged source files in
`latex_research`, plus four authored synthetic controls in `latex_profiles`:
basic mathematics, a diagram with an authored alternative, a simple table with
an authored header row, and a `titlesec` heading. Each source context runs under
four profiles, giving 120 case/profile combinations:

| Profile | Source transformation | Independent check |
|---|---|---|
| `untagged` | Original source bytes | Explicit `ua1` negative baseline |
| `existing-ua1` | Explicit language plus the actual application's structure-fix method | `ua1` if compilation produces a candidate |
| `modern-ua1` | PDF 1.7, UA-1, `tagging=on`, associated MathML requested, `unicode-math` | `ua1` |
| `modern-ua2` | PDF 2.0, UA-2, `tagging=on`, structured and associated MathML requested, `unicode-math` | `ua2` |

The modern profiles are experimental variants. They add declarations in the
preamble but retain the document body exactly. Language is explicitly declared
English for the synthetic controls and German for P04; it is not an inferred
language repair. P01 retains its complete three-file source context. The missing
files and malformed syntax in N01–N04 remain intentionally broken.

The modern configuration follows the LaTeX project's current
[usage instructions](https://tagging-project.latex-project.org/documentation/usage-instructions).
Those instructions distinguish MathML associated files from PDF 2.0 MathML
structure elements. A request to generate either representation is not evidence
that it appears in a saved PDF; the inspector reports the actual objects.

The upstream [package status list](https://latex3.github.io/tagging-project/tagging-status/)
lists `titlesec` as currently incompatible. S04 is a bounded control for that
package, not a claim that every use must fail or that one successful document
establishes package-wide support.

## Measured results and decision

The complete run against application revision
`e7d9b05febabcc92b3d0a7de2c460d2433613173` produced 120 observations, with
zero execution or evidence-integrity failures. The
[sanitized results](latex-pdf-profile-results.json) retain the harness and runtime
hashes, source/PDF bindings, independent report hashes and per-case observations.
The harness was uncommitted during the run; its recorded file hashes identify
the implementation, separately from the application baseline revision.
The original harness is retained at commit
`86243de580e2e701ad0731502e7b74153a8d3fb1`. Its subsequent Black formatting
correction preserves the Python syntax tree; the recorded run hashes remain
the original measurements and are not rewritten to match later formatting.

| Profile | Compiled PDFs | Compiler failures | Generation refused | Independent passes / failures |
|---|---:|---:|---:|---:|
| Original untagged | 26 | 4 | 0 | 0 / 26 |
| Existing UA-1 | 0 | 29 | 1 | Not run |
| Modern UA-1 | 26 | 4 | 0 | 26 / 0 |
| Modern UA-2 | 26 | 4 | 0 | 26 / 0 |

The four modern/original compiler failures are the intentionally broken
N01–N04 contexts. The existing application profile instead fails on unsupported
`DocumentMetadata` author/title keys; P04 retains the application's metadata
refusal before compilation. This is tracked separately in
[#482](https://github.com/Aelira-AI/aelira-core/issues/482). These results measure
the pre-fix implementation on the pinned research runtime, not every production
runtime or a corrected legacy profile.

Both modern profiles contain 23 genuine MathML associated files across 21 PDFs.
UA-2 additionally contains 701 MathML namespace structure nodes across those
21 PDFs; UA-1 contains none. Every observed Formula node has a corresponding
MathML-associated-file observation in its document, but source-to-formula
semantic equivalence remains unassessed. Node counts do not establish that
fractions, scripts, units or equation references retain their intended meaning.

The saved objects also expose gaps hidden by machine validation:

- P02's unannotated diagram passes both validators with no Figure structure
  node. S02's authored diagram has a Figure node with its exact supplied
  alternative, `An arrow leads from A to B.`.
- P03's grouped table passes with 11 TD cells and no TH cells. S03's explicitly
  annotated simple table has two TH and four TD cells, but this does not prove
  support for grouped headers or arbitrary tables. A separate raw-object check
  found S03's column scope in `/C /TH-col` and the structure root's `/ClassMap`.
  The inspector records direct `/A` attributes only; an empty direct attribute
  field does not establish that class-based scope is absent.
- S04 compiles and passes with `titlesec`, but has no H/H1–H6 structure nodes.
  Successful compilation is therefore insufficient evidence of heading support.
- German P04 retains `/Lang=de` in both modern profiles. Document Info title
  and author observations are retained per PDF; XMP values and their raw stream
  hashes remain in the full local report. No inspector traversal was truncated.

The application's existing bounded structure check also differs from veraPDF:
it passes 22/26 modern UA-1 PDFs and 15/26 modern UA-2 PDFs, while veraPDF passes
all 26 in each profile. For example, it rejects S03 in both profiles. This
experiment records that disagreement without relaxing either acceptance gate.

**Decision: retain the production profile and publication boundary.** Correct
#482 independently, then use the pinned experiment to evaluate any proposed
migration. UA-2 provides observable additional math structure, but adoption
still requires source-to-PDF semantic checks, figure/table/heading acceptance
oracles, and named reader/assistive-technology trials. Human review, mathematical
fidelity and reader/AT usability were not performed in this run. A validator
pass here is not an accessibility certification or permission to publish a PDF.

## Pinned runtime

`docker/latex-profiles.Dockerfile` uses an immutable multi-architecture
`texlive/texlive` small-image index. This image is maintained by
[Island of TeX](https://github.com/islandoftex/texlive), separately from Aelira's
production image. Three additional package archives are checksum-pinned; a
changed upstream archive fails the build instead of silently updating a package.

The reviewed runtime is LuaHBTeX 1.24.0, TeX Live 2026 and LaTeX 2026-06-01.
`tests/fixtures/latex_profiles/runtime.json` records the image index, native
ARM64/AMD64 manifests, actual package declarations and package-file hashes.
The runner resolves the selected local image to its immutable image ID before
execution and rejects a runtime that differs from the lock.

Independent validation uses the signed veraPDF 1.30.2 Greenfield distribution.
The lock records installer and CLI JAR hashes. The installer signature was
checked against the publisher's key fingerprint
`13DD102B4DD69354D12DE5A83184863278B17FE7` in an isolated keyring.
Follow the publisher's [installation and signature instructions](https://docs.verapdf.org/install/)
and the version-specific
[CLI-only installation settings](https://github.com/veraPDF/veraPDF-apps/blob/v1.30.2/docker-install.xml).
The runner invokes the checked CLI JAR directly with Java and records the Java
version; it does not trust an arbitrary wrapper to select that implementation.

## Reproduction

Install this repository's Python development dependencies, Docker and Java 21.
Install the exact validator distribution from the URL in `runtime.json` into an
isolated directory, verifying its checksum before executing it. No production
configuration or `.env` edit is needed.

```sh
docker build -f docker/latex-profiles.Dockerfile -t aelira-latex-profiles:research docker
ENV=test python scripts/evaluate_latex_pdf_profiles.py \
  --image aelira-latex-profiles:research \
  --verapdf-jar /path/to/verapdf/bin/cli-1.30.2.jar \
  --java /path/to/java \
  --output /path/to/new-evidence-directory
```

Compilation runs offline, with a read-only container root, shell escape disabled
and owned scratch directories. Positive compilations require two passes with
fresh PDF outputs. All output directories and package caches belong to this
experiment. The source repository is not mounted into the compiler container.

`--cases S01 S02 S03 S04` is available for exploration. Such a report explicitly
sets `complete_matrix=false`; it is not the complete evaluation. Missing tools,
integrity errors and failed validator controls make the runner exit nonzero.
Expected compiler or validator rejection is a measured outcome, not an
accessibility pass.

Run the evidence contract tests with:

```sh
python -m pytest tests/test_latex_profile_contract.py tests/test_latex_profile_runner.py
```

## Evidence and limits

The report binds original and variant source hashes, repository revision,
harness hashes, image identity, installed package hashes, compiler logs and
both compiler-pass PDFs. The final compiler hash must agree with inspection
and independent validation. Validator JAR and candidate hashes are checked
before and after validation. A changed harness invalidates the run.

The inspector reads actual structure roles, namespaces, formula identities,
embedded XML streams, figure alternatives, table cells and attributes, links,
document language and metadata. It distinguishes MathML content from empty
declarations and unrelated associated files. Traversal, stream and XML limits
are explicit observations, never silently complete evidence. Stable object IDs
refer to one saved file; source-to-PDF semantic associations remain unmapped.
The inspector does not resolve class-based attributes or custom role mappings.
Its XMP title text combines language alternatives; repeated values there must
not be interpreted as duplicate authored titles without inspecting the XML.

Each independent report must name the requested UA profile and exact candidate
path/size, report normal completion, and contain consistent job/rule/check counts.
The basic control must both pass the modern profiles and fail the untagged
baseline before other outcomes can be interpreted. As
[veraPDF documents](https://docs.verapdf.org/validation/), its PDF/UA validation
covers machine-verifiable checks. Mathematical equivalence, human reading order,
reader application support and assistive-technology use remain unperformed.

Raw PDFs, XML reports and logs are diagnostic evidence. They are not approved
accessible downloads. Review local paths and tool output before publishing a raw
evidence bundle; the repository's results summary contains only selected,
sanitized observations.

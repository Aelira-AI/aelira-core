# Synthetic LaTeX research corpus

These 26 diagnostic cases and 28 source files were authored as synthetic research controls on 2026-09-18 and imported for [issue #444](https://github.com/Aelira-AI/aelira-core/issues/444). Each source file was reviewed before import. The source bytes are preserved exactly; `corpus.json` records their SHA-256 identities and a reviewed selection of the original control expectations. This directory contains source fixtures, the sanitized manifest and this README. It contains no generated documents, converter logs or research infrastructure.

The contributed synthetic fixtures and manifest follow the repository's [LICENSE](../../../LICENSE), GNU Affero General Public License version 3. This notice applies to the contributed corpus files; it does not change the licenses of TeX distributions, packages, converters or other external tools.

## Inventory and paths

- **M01–M18:** fractions, script attachment, tensor indices, matrix coordinates, integrals, Greek variants, quantum and vector notation, physics macros, scientific notation and units, piecewise boundaries, aligned rows and references, inline expressions, a long final term, and notation whose meaning depends on authored prose.
- **P01–P04:** a multi-file project, directed diagram relationships, grouped table headers and German language metadata.
- **N01–N04:** deliberately undefined macro, missing included chapter, missing image and malformed fraction controls.

All `entrypoint` and source `path` values resolve relative to this directory. P01 retains `main.tex`, `macros.tex` and `chapters/one.tex`; execute its entrypoint with P01 as the project working directory so its relative includes remain meaningful. The absent files in N02 and N03 are intentional and must not be supplied to make those cases pass. The existing `latex_validation` fixtures are a separate corpus and are not replaced by this import.

## Manifest contract

`corpus.json` uses `schema_version: 1` and `corpus_id: "latex-research-444-v1"`. Root fields declare provenance, scope, compile-expectation scope, the exact case/source counts and the human-review statuses. Each entry in `cases` contains:

| Field | Meaning |
| --- | --- |
| `id`, `title` | Stable control identifier and synthetic case title |
| `entrypoint` | Relative TeX entrypoint within this directory |
| `sources` | Complete included source inventory, each with relative `path` and lowercase hexadecimal `sha256` |
| `required_packages` | Declared package prerequisites; no promise that a converter supports every package |
| `compile_expected` | `passed` for the 22 intentionally compilable controls; `failed` for the four deliberate negative controls |
| `failure_reason` | Intended negative-control condition, or null for positive controls |
| `expected_content` | Authored structural/content expectations to assess independently |
| `reader_task`, `expected_answer` | A proposed reader task and its expected answer |
| `domain_human_review` | `not_run` |
| `assistive_technology` | `not_run` |

Compile expectations describe source intent with its required dependencies installed. They are not measured compile results, converter acceptance results or evidence that a document is complete. An unavailable tool or package must not satisfy a negative control merely because compilation failed.

Expected content and reader tasks are unvalidated hypotheses for testing. No domain-expert human review or assistive-technology reading test is claimed. A passing structural check, successful compilation or generated output does not certify mathematical fidelity, usable accessibility, PDF/UA conformance or WCAG conformance. Record actual tool/runtime versions, warnings, output identities, structural observations and human/reader results separately from this source manifest.

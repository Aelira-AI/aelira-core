# Commutative-diagram semantic contract

Aelira Core provides a bounded, provider-neutral contract for representing and verifying commutative-diagram semantics. A separate v0.9.7 PDF specialist can recognize one explicitly bounded visual into that contract, associate it with the exact source, verify the saved file, and hold the result for human approval. The graph verifier itself remains independent of pixels, providers, and document mutation.

The contract models:

- objects as typed nodes;
- directed and bidirectional arrows, including labeled parallel arrows and directed loops;
- labels attached to exactly one node or arrow;
- ordered composition paths; and
- declared commutativity relationships between distinct paths with the same endpoints.

The verifier checks references, path traversal, label attachment, relation endpoints, collection bounds, and canonicalization cost. Unknown fields, unsupported graph kinds, higher-cell data, unresolved crossings, incomplete topology, and ambiguous attachments fail validation. They remain human-review cases; the verifier does not guess.

## Canonical identity

`verify_commutative_diagram()` returns an immutable `VerifiedCommutativeDiagramV1` with a SHA-256 digest over canonical passive JSON. Canonical identity ignores:

- input collection order;
- layout coordinates; and
- incidental node, arrow, label, path, and relation identifiers.

It preserves mathematically material distinctions: object and arrow labels, endpoints, arrow direction, path order, and declared commutativity. Changing one of those values changes the digest or makes the graph invalid.

Nodes with identical labels require bounded identifier-independent relabeling. Inputs whose symmetry would require more than 4,096 candidate relabelings fail closed rather than consuming unbounded work.

## Accessible outputs

`describe_commutative_diagram()` returns a structured object inventory, arrow inventory, ordered paths, commutativity relationships, and the canonical graph digest. `render_commutative_diagram_html()` derives semantic HTML from that same description and escapes all supplied text.

These outputs describe what the verified graph declares. They do not prove that the declaration is mathematically true or that a source image was recognized correctly.

## PDF recognition and saved-file proof

`CommutativeDiagramRecognizer` accepts one exact embedded-image occurrence or page-raster region as a bounded JPEG through a purpose-bound `alt_text` client. Provider output must match the exact graph schema; unknown topology, unresolved crossings, incomplete paths, ambiguous attachments, and invalid commutativity relationships are refused rather than guessed.

An accepted result is associated with the exact source as a PDF `Figure`. The deterministic accessible description and canonical graph attachment are bound to the recognition and source digests. The saved file is reopened to verify source identity, structure ownership, reading order, attachment bytes, metadata, and applicable render or OCR evidence.

Every commutative-diagram result is review-gated. Direct, queued, Canvas, and Brightspace publication paths keep the artifact unavailable until a person approves the exact current evidence contract; changed source, semantics, saved evidence, or review digest invalidates that approval.

## Example

```python
from src.education.commutative_diagram import (
    describe_commutative_diagram,
    verify_commutative_diagram,
)

verified = verify_commutative_diagram(candidate)
description = describe_commutative_diagram(verified)

assert description.graph_sha256 == verified.canonical_sha256
```

The input must use `contract_kind="commutative_diagram_v1"` and pass the exact Pydantic schema. See the focused contract tests for complete triangle, square, parallel-arrow, loop, and adversarial examples.

## Deliberate limits

The graph contract does not discover regions, call an AI provider, infer topology from pixels, mutate a document, synthesize a proof, or support higher-dimensional cells. Those provider and PDF operations belong only to the bounded specialist above. Neither layer proves that a declared diagram commutes or supports higher-dimensional cells.

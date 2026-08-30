# Commutative-diagram semantic contract

Aelira Core provides a bounded, provider-neutral contract for representing and verifying commutative-diagram semantics. It is a library contract, not an image recognizer or PDF remediator.

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

This contract does not discover regions, call an AI provider, infer topology from pixels, associate a graph with a PDF object, mutate a document, synthesize a proof, or support higher-dimensional cells. A later source-association specialist can bind the stable verified type and digest to source evidence without changing this semantic boundary.

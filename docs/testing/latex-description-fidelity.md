# Mathematical description drafts

Related issues: #449 and the broader corpus replay in #444.

Equation descriptions are optional review drafts. They are not verified
mathematical equivalents, replacements for navigable MathML, or evidence that
an accessibility finding has been repaired. Neither response length nor a
successful provider establishes mathematical correctness.

## Input and output boundaries

The equation processor supplies the complete extracted equation to the provider
up to 4,096 characters. Supplied context, including its keys, is bounded to 2,048
characters. If either bound is exceeded, generation is withheld; no shortened
expression is submitted. Sanitization that changes the source or supplied
context also withholds generation. The original equation and converted MathML
remain available independently of description generation.

Document context is an excerpt and can include an inferred topic. It is explicitly
unverified, not a complete preamble or project dependency graph. The prompt asks
for uncertainty where author notation is ambiguous. This prompt is not a semantic
verifier: even a plausible, complete response remains a draft.

Successful responses up to 2,000 characters are available only as
`description_draft` on the equation processing result. Consumers must treat this
as untrusted, unverified text for human review. Do not insert it into HTML,
ARIA attributes, source repairs, or learner-facing mathematical alternatives.
No human-approval or promotion endpoint is introduced by this change.

The MathML receipt carries a typed `description` observation: source digest and
length, bounds, `input_status` (`complete` or `not_sent`), a fixed reason code,
`purpose=optional_summary`, `semantic_equivalence=not_assessed`, and
`human_review_required=true`. `complete` means the whole extracted equation and
supplied context were submitted; it does not mean the document context was
complete or the model understood the equation. Provider failures and malformed
responses cannot remove MathML or promote a heuristic label. The safe receipt
contains no provider response or document excerpt.

Both HTML exporters omit generated prose and retain structured math plus the
complete LaTeX equation in an expandable source/review block. The legacy
`aria_label` field remains empty. Stored scan responses also suppress historical
unverified ARIA labels on read; original records are not rewritten. Scan source
snippets remain previews and are now explicitly identified as such. Missing
historical evidence is not reconstructed.

LaTeX remediation distinguishes an actual missing cross-reference `\label{}`
finding from a mathematical description finding. Only the former has an automatic
reference-label repair. Description requests stay in manual review, without an
AI-generated source mutation or fixed-issue credit. Source scores and existing
TEX-only managed artifact and PDF validation boundaries remain unchanged.

## Regression controls

```sh
python -m pytest tests/test_latex_description_fidelity.py \
  tests/test_latex_evidence_outcomes.py tests/test_latex_validation_routes.py --no-cov
```

Tests use deterministic provider doubles, including a deliberately incorrect
interpretation, failures, empty/malformed replies and oversized output. The
hash-pinned M16 corpus source must reach the provider with its final
`97q_{\mathrm{end}}/(1+z^2)` term intact. Nested fractions/scripts, ambiguous
notation, limit boundaries, sanitization changes, disabled providers, historical
scan projection and saved source preservation have separate controls. Real
`latex2mathml` conversion exercises the structured-math path; these checks do not
certify semantics, screen-reader behavior, or the full #444 queued journey.

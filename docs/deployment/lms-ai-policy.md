# LMS AI policy and provider readiness

Aelira separates **deterministic accessibility work** from generative AI. LMS scanning, rule detection, severity, and deterministic repairs run without an AI provider. AI is an explicit account-wide lane that an administrator must enable for one or both purposes:

- **Remediation** — model-assisted repair suggestions for eligible LMS content.
- **Alternative text** — image content is provided to the selected model for description.

## Three independent controls

Configuration is not authorization. All three layers must agree at the instant of a call:

1. **Credentials/readiness** — a matching department BYOK key exists, or an approved Gemini pilot has a platform key, or local Ollama is reachable with required models.
2. **Pilot approval** — read-only approval allows only the shared Gemini credential lane; it does not enable LMS AI or another provider.
3. **LMS AI policy** — an account administrator enables one provider and at least one purpose in the Admin Dashboard.

Adding a key or approving a pilot never opts an account into LMS AI. Conversely, enabling a policy cannot make an unready provider usable.

## Data egress lanes

| Lane | Credential source | Egress |
|---|---|---|
| Ollama | None; local only | No model-request egress from the Aelira deployment |
| Gemini, OpenAI, Anthropic, xAI | Exact matching encrypted department BYOK | Selected prompt/image content goes to that provider |
| Gemini pilot | Shared platform Gemini key and explicit department approval | Selected prompt/image content goes to Google Gemini |

The policy API and dashboard never return credentials, ciphertext, model identifiers, or configured hosts. Readiness exposes only bounded states such as `ready`, `credentials_missing`, `unreachable`, and `model_missing`.

## Ollama constraints

LMS policy execution accepts only an explicit HTTP(S) loopback endpoint (`localhost`, `127.0.0.0/8`, or `::1`, canonicalized before use). User information, paths, query strings, redirects, remote/private-network hosts, department BYOK credentials, and ambient `OLLAMA_API_KEY` values are rejected. Readiness uses a separate two-second model-list probe; runtime inference preserves its independently configured timeout within the one-to-120-second safety bound. Both clients disable redirects and environment-proxy inheritance, so ambient `HTTP_PROXY`/`HTTPS_PROXY` settings cannot reroute loopback Ollama traffic. Readiness requires the configured text, code, and vision models. These restrictions apply to the LMS AI lane even if another non-LMS feature has broader provider settings.

See [Local AI models](local-ai-models.md) for model installation and hardware guidance.

## Administration and concurrency

Only session/API-key administrators and super administrators, or a validated account-wide LTI Administrator, can read or update `/llm/lms-policy`. Faculty and course-scoped LTI staff are denied. The API always scopes reads and row locks to the authenticated department.

The editor sends the revision returned by its last GET. If another administrator saves first, the stale update returns `409 policy_revision_conflict` with the current secret-free policy; it creates no mutation or audit event. Reload and review before saving again. An unready selected provider similarly returns `409 provider_not_ready`.

Successful policy and revision changes plus their allowlisted audit record commit in one database transaction. Audit details contain only old/new policy booleans and provider, old/new revisions, schema version, and outcome.

## Revocation semantics

Every LMS model operation re-reads policy and credentials before provider construction and again immediately before dispatch. Disabling the master control, removing a purpose, changing provider, revoking BYOK, or removing Gemini pilot approval therefore blocks later calls and queued work when it reaches execution. Already completed calls cannot be undone. A call already in flight at the moment of revocation may finish at the external provider; its result remains subject to post-call audit handling. Treat provider-side retention and cancellation as separate vendor controls.

## Immediate security follow-up

The pre-existing legacy authentication-key bug is deliberately outside this policy slice and was not modified. Fixing and regression-testing that legacy path is the immediate next security task; do not infer that this policy endpoint changes legacy-key behavior.

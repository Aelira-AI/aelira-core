# Scan AI usage fields

The legacy `ollama_used` and `ollama_calls` fields describe successful Ollama
provider operations observed within the scan's workspace runtime. They do not
describe all AI providers, requested options, image/equation counts, network
retries, or the correctness of generated content.

| Stored value | Meaning |
| --- | --- |
| `ollama_used=true`, positive `ollama_calls` | That many Ollama operations returned successful provider responses during this scan |
| `ollama_used=false`, `ollama_calls=0` | The instrumented scan observed no successful Ollama operations |
| Both `null` | No operation measurement was supplied; usage is unknown |

Disabled, unavailable and failed providers do not produce successful-call
counts. Successful cloud-provider responses do not count as Ollama usage. A
failed cloud attempt followed by a successful Ollama fallback counts only the
successful Ollama operation. Detection, description and validation can each
make their own call, so one image need not equal one call.

Usage is separate from remediation status, semantic quality and verification of
saved artifacts. For example, a valid provider response may be unsuitable as
alt text and require human review even though inference occurred.

Document, web/code and multimedia scan routes persist their runtime measurement.
The older `ScanService.store_*_scan` helpers accept an explicit `usage` snapshot;
without one they record unknown rather than deriving use from suggestions. The
legacy `store_latex_scan(..., ollama_used=...)` argument remains accepted for
compatibility but is not treated as evidence. Other uninstrumented writers also
default to unknown. The existing database columns already allow null values.

## Earlier records

This change does not reconstruct historical usage. Earlier writers inferred
flags or counts from requested options, findings, or object counts. Therefore an
existing true/false value is not itself evidence that inference did or did not
occur, and earlier null/default values cannot reconstruct call totals either.

Inspect the relevant installation before any correction, using a read-only
inventory such as:

```sql
SELECT s.scan_type, r.ollama_used, r.ollama_calls, COUNT(*)
FROM scans AS s
JOIN scan_results AS r ON r.scan_id = s.id
GROUP BY s.scan_type, r.ollama_used, r.ollama_calls
ORDER BY s.scan_type, r.ollama_used, r.ollama_calls;
```

Only correct identified rows when retained, scan-specific evidence establishes
the value. Shared server logs without request attribution, requested flags,
generated-looking suggestions and current provider settings are insufficient.
When prior measurement cannot be established, preserve that uncertainty;
never manufacture call counts from the number of images or findings.

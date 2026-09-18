# Office saved-package preservation corpus

This is the deterministic Office increment of [#374](https://github.com/Aelira-AI/aelira-core/issues/374). It complements the [PDF corpus](pdf-remediation-acceptance-corpus.md). Issue #374 remains open: generated fixtures from Python libraries are not multi-authoring-tool exports, rendered comparisons or human/assistive-technology evidence.

```bash
python scripts/office_preservation_corpus.py \
  --workdir /tmp/office-preservation-new-run \
  --revision FULL_COMMIT_SHA
```

Use a new directory for each run; existing evidence is never overwritten. The supplied revision must identify the checkout being tested. CI supplies its checkout SHA and retains the sources, candidates and `report.json`. The report also records the runner, fixture-generator and manifest digests, dependency versions, scanner profiles, configuration, individual source/candidate digests, actual finding counts and elapsed time. There is no score-based acceptance threshold.

## Required cases

The manifest fixes six cases, with expected behavior defined before execution:

| Format | Supported repair | Already-correct control | Protected content |
|---|---|---|---|
| DOCX | Set the source-authored title to `Course workbook` | Preserve that existing title | Body/table text, headers, footers, image bytes and hyperlinks |
| PPTX | Change the first title's foreground from `969696` to `696969` | Preserve the corrected color | Slide/shape order, text, notes, poster image, generated WAV bytes, relationships and timing markup |
| XLSX | Freeze the first worksheet's header at `A2` | Preserve the existing freeze pane | Values, formulas, named ranges, sheet order, chart references, comments, print area and hyperlinks |

The runner uses the real remediator and strict scanners, with AI disabled. Repair cases must have exactly one governed finding, persist the expected target and verify one actual fix. Controls must have no governed finding and no claimed fixes. Other findings can remain: these controls are already correct for the targeted operation, not universally accessible documents.

Source hashes must remain unchanged. Every ZIP member must remain present, with no unexpected additions. Binary members must match exactly. XML comparison retains text, element order, relationships and targets while normalizing attribute order. Only the intended title/color/freeze-pane nodes and the core modified timestamp are excluded; the target value is separately verified after reopening. Scanner regressions fail independently of package comparison. Intentional corruption tests prove that readable candidates with changed text, formulas, notes, links, timing or media fail this gate, including a damaged candidate whose scanner reports a successful fix.

## Evidence limits

These repository-authored fixtures are CC0 synthetic content. No customer files, external images, live provider or private metadata are included. Runtime dependencies remain under their own licenses. The WAV is real generated silence; storing it and timing markup unchanged does not prove playback or animation behavior in PowerPoint. Workbooks retain formula expressions; no claim is made about recalculation results.

Package equality outside declared repairs is a content-preservation observation. It does not establish rendered appearance, semantic reading order, description quality, application interoperability, WCAG/PDF-UA conformance or assistive-technology usability. The manifest/report retain these unavailable checks as `not_run`, with reasons and a maintainer owner; they do not contribute to passes.

Remaining #374 work includes authoring-tool diversity, richer PDF/LaTeX and safe-refusal cases, embedded-object coverage, rendered comparisons, semantic judgments, human/AT findings and time to accepted output. Delivery through the application belongs to #375. These six cases cannot close either issue by themselves.

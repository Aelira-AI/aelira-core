# Measured LaTeX converter compatibility

The required smoke compares the unchanged source and the exact Aelira LaTeXML preprocessor output for M10, M12 and P04, using both Pandoc and LaTeXML/latexmlpost. It also calls the public `LaTeXConverter.convert_to_html` entry point with the original sources and checks its decision, executed tools, accepted output or refusal reason. Engine output and accepted application delivery are separate observations.

These are synthetic structural controls, not package-wide compatibility guarantees, fidelity scores, accessibility certification or assistive-technology testing. `partial` means the bounded package control has some evidence; it never promises other commands, options or package combinations. `observed_failure` records a measured failure in this installed combination. Untested versions and declarations remain `unchecked` in the routing matrix.

## Installed baseline

The local ARM64 image `aelira-latex-metadata:local` had image identity `sha256:e7389d13c29ff0d326beba6e32f0cc615bc641f6585e197d0af9dfead788185a` when measured. Its actual reported tools were Pandoc **3.1.11.1**, LaTeXML and latexmlpost **0.8.8**, and LuaHBTeX **1.18.0**, whose banner reports **TeX Live 2025**. Do not infer the TeX version from the image's name or earlier descriptions.

The probe resolves only the fixed `physics.sty`, `siunitx.sty` and `babel.sty` names with `kpsewhich`, reads bounded files and extracts their package declarations. Observed versions were physics **1.3** (no date reported in its declaration), siunitx **3.4.6**, dated **2025-02-27**, and Babel **25.4**, dated **2025/02/14**. The [manifest](../../tests/fixtures/latex_compatibility/manifest.json) records these versions, exact package hashes, fixture hashes and expected observations. TeX package versions do not imply that a converter uses that TeX implementation: LaTeXML loads its own physics binding, while Pandoc interprets TeX through its own reader.

## Results in that baseline

| Control | Pandoc raw / preprocessed | LaTeXML raw / preprocessed | Public Aelira route and delivery |
| --- | --- | --- | --- |
| M10 physics bra/ket and second partial derivative | `observed_failure` / `observed_failure`: exit 0 with a math conversion warning; no MathML | `partial` / `partial`: clean conversion; presentation and XMath operator checks pass | LaTeXML selected; refused by existing `semantics_not_preserved` gate |
| M12 siunitx scientific notation and units | `partial` / `partial`: `3.00 × 10⁸`, metres per second preserved in MathML | `observed_failure` / `observed_failure`: expl3 relational-token errors and fatal error, exit 1 | Pandoc selected; accepted with independent notation/unit checks passing |
| P04 Babel, German text, `selectlanguage` and math | `partial` / `partial`: text and `v=3` survive; document language is empty | Raw `observed_failure` (Babel errors, exit 1); preprocessed `partial` (text/math survive but undefined `selectlanguage`, XML error node, postprocessor validation error and incorrect `lang=en`) | Pandoc selected; refused by existing `metadata_unsupported` gate |

M10 and M12 input bytes are unchanged by preprocessing. P04 preprocessing removes the Babel declaration but retains `selectlanguage`. Its LaTeXML and latexmlpost commands both return zero after preprocessing despite explicit errors. A successful process exit therefore cannot establish compatibility or permission to deliver a candidate. No existing gate is relaxed to make this smoke pass.

The native project decision also refuses physics with `requirements_conflict`: the sandboxed project path selects the Pandoc family of routes, while the measured physics control requires LaTeXML. This is a decision control; it does not claim a sandboxed project was rendered.

## Independent oracles and receipts

The smoke requires positive text and `x²+1` controls to succeed through each real toolchain before expected failures can count. Missing executables, timeouts, truncated process logs, changed package identities, changed converter versions, altered fixtures or an incomplete case inventory fail the smoke.

The case oracles inspect generated structures independently of Aelira's acceptance gates:

- M10 checks bra/ket delimiters and operands, addition, an order-two partial fraction, and the XMath operator tree. Resolving `XMRef` references must yield `plus(inner-product(phi, psi), partial-derivative(x, 2)(f))`. Merely finding the glyphs or the word “derivative” is insufficient.
- M12 checks the `3.00` mantissa, a base-10 exponent of 8, multiplication, metre and per-second structure.
- P04 checks the German sentences, the `v=3` MathML token sequence, and German document language. Preserved text alone does not pass the language check.

Each measured row records source, preprocessed converter input, generated HTML, raw intermediate XML and analysis SHA-256 values; absent outputs have null hashes. Analysis is canonical sorted JSON. XMath subtrees have a separate SHA-256 over their UTF-8 ElementTree serialization and contain resolved operator observations. XML and HTML hashes identify the exact bytes of an attempt; temporary search paths and generated metadata can make those hashes differ across attempts. Hashes are evidence identities, not snapshots whose equality substitutes for the structural checks.

Five additional saved-node controls use the existing M03/M04/M06/M14/M16 sources unchanged, with their hashes pinned in the manifest. They exercise real converter methods, real stage traces and a trace read back from the saved HTML:

| Saved-node control | Converter | Independent structure checked | Delivery observation |
| --- | --- | --- | --- |
| M03 | Pandoc | `x^{a_b}`: subscript `b` attached to `a` inside the exponent on `x` | Accepted |
| M04 | Pandoc | `x^a_b`: exponent `a` and subscript `b` share base `x`; equivalent combined or nested script nodes are normalized | Accepted |
| M06 | Pandoc | Every coordinate in the 2×3 matrix `[[1,0,-i],[i,2,3]]` | Accepted |
| M14 | LaTeXML | First row `E=mc²`, second row `E/c²=m`; distinct authored energy/mass labels and links to their exact saved row IDs | Refused by existing `semantics_not_preserved` gate |
| M16 | Pandoc | Sixteen ordered indexed fractions in four rows, then the final added `97q_end/(1+z²)` term in the fifth row | Accepted |

M14 legitimately emits four MathML nodes for two aligned equation rows. The oracle groups nodes by their saved row targets and checks the mathematics and references; it does not require matching source-expression and output-node counts. A trace can remain `unmapped` when converter annotations do not exactly match authored source bytes. That is recorded honestly, even where the separate structural oracle passes.

For M10, the public converter's LaTeXML parse stage must contain observed nodes with nonempty intermediate-semantics hashes, bound to the actual saved XML. Its preprocessing receipt must connect original bytes to the parser input. For accepted M12, the public final trace must bind the actual delivered HTML and original source, and the decision must bind the source and declared matrix version. These are required assertions, not merely fields emitted for inspection.

Bounded, deduplicated warning/error lines and process exits accompany the hashes. Filesystem paths are redacted from diagnostic text. The complete logs remain temporary; the repository contains only redistributable synthetic input and a reviewed manifest. The public-route receipt preserves the selected decision, its source binding and the real gates' diagnostics. Refused candidate bytes are explicitly marked undelivered even when their structures can be inspected.

## Running the required smoke

Run the production image with the two synthetic fixture directories mounted read-only. Production images exclude the test tree; the smoke uses the application and script built into the image.

```sh
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,mode=1777 \
  --mount type=bind,source="$PWD/tests/fixtures/latex_compatibility",target=/app/tests/fixtures/latex_compatibility,readonly \
  --mount type=bind,source="$PWD/tests/fixtures/latex_validation",target=/app/tests/fixtures/latex_validation,readonly \
  --entrypoint python "$PRODUCTION_IMAGE" \
  /app/scripts/smoke_latex_compatibility.py > compatibility-receipt.json
```

The default imports the application normally. The measured lightweight local image lacks unrelated ML dependencies required by `remediation/__init__.py`; only for that local image, append `--focused-imports`. This explicitly bypasses that package initializer while loading the real converter, metadata, semantics, diagnostics, routing matrix and PDF validation modules unchanged. It is not a full production dependency smoke. Production CI must use the default mode.

The raw/preprocessed experiment executes the exact AST method body from the checked-in converter and reports its hash, avoiding unrelated application initialization for the isolated measurement. The integrated experiment calls the imported real public converter and tests its decision and gates. These are distinct checks. The smoke exercises exactly 14 toolchain rows (two positive controls plus twelve case/mode/engine combinations), three integrated conversions, one native-project decision and five additional saved-node controls. A changed version or observation requires reviewing the emitted JSON and updating the manifest based on new measurements, not broadening an allowed failure or weakening a refusal.

## Source and representation provenance

Conversion diagnostics now retain a versioned route decision, requirement hashes,
source equation inventory, preprocessing hashes and observations of intermediate
and final bytes. Package/class matrix keys are distinct. Custom macro names are
inventoried as hashes with `unchecked` support; their declaration prefix hashes
are not hashes of complete definitions. The full source hash binds those bodies.
No selected HTML route silently retries another converter after failure. Project
HTML remains restricted to the existing Pandoc sandbox; a physics requirement
there is an explicit route conflict.

Expression, aligned-row, label and citation identities bind the document hash and
original UTF-8 byte spans. They repeat for identical source bytes; source revisions
produce new identities. Literal references and citations retain hashed targets,
including unresolved and ambiguous targets. Project receipts inventory bounded
original TEX members separately from the expanded analysis source, with explicit
incomplete coverage when limits or unsupported syntax apply. Their content hashes
allow comparison without presenting expanded-source offsets as original-file lines.

An `exact_annotation` association means a converter supplied an exact authored TEX
annotation for one source expression or row. Duplicate matches remain ambiguous.
It does not prove that the accompanying presentation tree means the same thing.
Separate structure, script-attachment, operator-scope and intermediate XMath hashes
bind the observed representation, including when an unchanged annotation accompanies
a changed expression. No equality of source-expression and output-node counts is
required. PDF and candidate DOCX/OMML observations have no implemented trustworthy
source association; they remain explicitly unmapped. This change adds no DOCX export.
All fidelity and reader usability statuses remain unassessed; author-supplied
annotations are referenced, never replaced with AI guesses or invented MathML intent.

# Functional LaTeX runtime readiness

`python scripts/smoke_latex_runtime.py` runs the `latex-runtime-v1` profile and
prints a JSON report. Exit zero requires every functional control to pass.
Executable discovery and `--version` output alone cannot make the profile ready.

The declared profile covers:

- LuaLaTeX with `fontspec`, Latin Modern Roman and English Babel.
- LuaLaTeX with German Babel (`ngerman`) and German characters.
- LaTeXML plus `latexmlpost`, producing HTML containing the expected MathML.
- Pandoc, producing HTML containing the expected MathML.

The PDF controls must open as a one-page document and retain the known sentence
and mathematical tokens. The HTML controls must retain the sentence and the
mathematical tokens inside a MathML element. These checks prove a small known
conversion works. They do not establish mathematical fidelity for arbitrary
documents, PDF/UA conformance or assistive-technology access.

## Service-identity check

Run against a built API image with its default non-root `USER`, with networking
disabled, an immutable root filesystem and a fresh writable temporary filesystem:

```sh
image=aelira-api:test
image_id=$(docker image inspect "$image" --format '{{.Id}}')
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,mode=1777 \
  --env AELIRA_RUNTIME_IMAGE_ID="$image_id" \
  --entrypoint /opt/venv/bin/python "$image" \
  /app/scripts/smoke_latex_runtime.py > latex-runtime.json
```

The production API and worker use the same image/user. Do not add `--user root`:
a privileged probe cannot establish service readiness. For an independently
configured worker image or user, repeat the probe under that configuration.
The CI Docker matrix requires this check on both AMD64 and ARM64, asserts UID
1000, and retains the report with the content-addressed local image ID, actual
architecture, tool/package versions and input/output hashes. A local image ID is
not a published registry-manifest digest; record that digest separately when
testing a released image. Both architecture jobs must pass before accepting the
image change. The existing reproducible-image checks remain required.

## Formats, fonts and diagnostics

The images explicitly install `texlive-luatex` and `texlive-lang-german` alongside
the existing TeX/LaTeXML/Pandoc packages. The production reproducibility cleanup
continues to remove generated format files. The converter and probe set
`TEXMFVAR`, `TEXMFCONFIG`, `TEXMFCACHE` and `XDG_CACHE_HOME` to private directories
inside each owned conversion attempt. Format and font helpers can initialize
there without writing into system directories or the service home. The two
passes of one conversion reuse that attempt's cache; different attempts remain
isolated. This trades cache reuse across documents for predictable ownership.

The report returns `ready`, `degraded` or `unavailable`, with per-control reasons
and repair guidance for missing tools, formats, fonts, language/packages,
unwritable scratch, timeouts and invalid output. Known font substitutions fail
even with exit zero; normal cold font-database creation is permitted. Each tool
execution has a timeout, and a timed-out probe kills its process group. The probe
contains only fixed synthetic inputs and runs with TeX shell escape disabled.
It never modifies uploaded source or changes a requested language to hide an
environment failure.

The probe is an explicit installation/CI diagnostic, not a recurring HTTP
liveness check. Run it when installing or upgrading an image. Ordinary converter
selection still uses executable discovery to choose a tool to attempt; it does
not claim functional readiness. Unsupported packages and other languages require
their own controls and are outside this profile.

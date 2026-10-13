# LaTeXML source-bound compatibility candidate

This directory is a **disposable probe recipe**, not a released or accepted runtime. The official upstream source is [LaTeXML](https://github.com/brucemiller/LaTeXML) at commit `0db6f47ea859391f9099e6e25e7405afad8467bf`, tree `2693c0c65c86f6b36370af18496d8283cf4f73ea`. That unreleased revision still prints `0.8.8`; the banner alone cannot identify these bytes. The only source change is [`siunitx-v3-adapter.patch`](siunitx-v3-adapter.patch), SHA-256 `8bea4050c667438c49e5a175dbaa52bb75415fd7eb4ec0a6508eda20ddf4795b`. [`siunitx.sty.ltxml`](siunitx.sty.ltxml) is a review copy of the patched binding, not an additional install input.

`prepare-source.sh` accepts an **existing** official Git repository, an absolute external Git executable, and an immutable native dependency image ID with verified Perl and Parse::RecDescent hashes. It verifies origin, commit, tree, Git blob bytes, all 760 tracked `lib`, `bin`, and `LICENSE` files, tracked modes, original license/binding hashes, the fixed patch, and the exact patched binding. It also verifies the pinned upstream `Makefile.PL` rule, `Version.in`, and math grammar. It never clones or fetches. Each call independently exports Git objects, applies the patch, and generates `Version.pm` and `MathGrammar.pm` offline under UID 1000, network none, read-only root, dropped capabilities and resource limits. The two generated hashes are pinned to observed outputs of the official rule and native tools; the revision substituted is the actual pinned upstream commit abbreviation `0db6f47`.

The package keeps the original 760-file `source-manifest.json` and `latexml-source.tar` at their independently reviewed hashes as tracked-source provenance. A separate `runtime-manifest.json` declares the official rule, templates, native generator image/tool identities, and two generated modules. `latexml-runtime.tar` contains exactly 762 files with pinned paths, bytes and modes. The v2 receipt binds both manifests and both archives. Git's tar writer adds group-write bits on export; the package records tracked Git modes. Two independent invocations must produce byte-equal complete packages.

Example with operator-owned paths on a host that has the verified upstream repository:

```sh
sh ./prepare-source.sh /usr/bin/git "$UPSTREAM_REPO" "$SCRATCH/package-a" \
  /usr/bin/docker "$IMMUTABLE_NATIVE_DEPENDENCY_IMAGE_ID" "$NATIVE_ARCHITECTURE" \
  "$PERL_SHA256:$RECDESCENT_SHA256"
sh ./prepare-source.sh /usr/bin/git "$UPSTREAM_REPO" "$SCRATCH/package-b" \
  /usr/bin/docker "$IMMUTABLE_NATIVE_DEPENDENCY_IMAGE_ID" "$NATIVE_ARCHITECTURE" \
  "$PERL_SHA256:$RECDESCENT_SHA256"
python3 accept_image.py \
  --docker /usr/bin/docker \
  --package-a "$SCRATCH/package-a" \
  --package-b "$SCRATCH/package-b" \
  --base-image-id "$IMMUTABLE_NATIVE_DEPENDENCY_IMAGE_ID" \
  --architecture "$NATIVE_ARCHITECTURE" \
  --out "$SCRATCH/image-acceptance"
```

`accept_image.py` requires a full local `sha256:` dependency image ID and checks its actual architecture, equal complete packages, safe runtime tar entries, every file hash and mode, and base layer ancestry. An image-architecture check alone does not prove native execution; record host, Docker daemon, and running container architectures separately. It builds offline with a per-command classic Docker builder setting because that builder accepts the local image ID directly as `FROM`; future builder support remains a portability gate. [`Dockerfile.probe`](Dockerfile.probe) installs the exact 762-file runtime and both manifests. [`verify_install.py`](verify_install.py) refuses missing, extra, changed, aliased, or wrongly moded files, shadowed commands, and generated modules that fail to load from `/opt/latexml-source/lib`. The host harness repeats installed verification as UID 1000 with network disabled, read-only root, dropped capabilities, no new privileges, resource limits and timeout. Preserve private diagnostics; export only reviewed bounded receipts.

The adapter avoids eager interpretation of the installed raw `expl3-code.tex`, maps guarded siunitx v3 `\qty`, `\qtylist`, `\qtyrange`, `\unit`, and `\per` forms to the existing LaTeXML formatters, and preserves physics `\qty` in either package load order. It does not establish complete siunitx support. The raw binding still has unsupported forms. Earlier probe prose describing public acceptance of ignored local options, legacy post-number optional syntax, and malformed numbers is **historical**: accepted main's current source guard refuses those unverified forms before public delivery. [`probe_integrated.py`](probe_integrated.py) uses a focused module shim and is not full-application proof.

The latest unchanged full application smoke on the disposable native ARM source-mounted image exited 1, `passed: false`, solely on 17 reviewed baseline/version differences. Normal application imports, existing source/corpus and delivery bindings, project refusal, saved-node controls, and separately run metadata scripts completed. A fresh 18-case normal-import synthetic consumer matrix accepted seven plain cases and refused eleven unverified cases. The packaging recipe does not change the compatibility manifest, oracle, fixture, product code, or source guards. Native amd64, committed both-architecture CI, final image, full application acceptance, dependency/provider/static XML/SVG provenance, all-copy and security gates remain separate.

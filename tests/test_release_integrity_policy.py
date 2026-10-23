"""Offline contracts for the Task23 release-integrity boundary."""

from __future__ import annotations

import os
import re
import json
import subprocess
from pathlib import Path

import pytest

from scripts.download_piper_voice import VOICE_ASSETS
from scripts.verify_final_os_packages import PLACEHOLDER_PACKAGE, PLACEHOLDER_PROVIDES

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_every_external_action_is_pinned_to_a_commented_full_sha() -> None:
    external_uses: list[tuple[Path, int, str]] = []
    for workflow in WORKFLOWS.glob("*.yml"):
        for line_number, line in enumerate(workflow.read_text().splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)(?:\s+#\s*(\S+))?", line)
            if not match or match.group(1).startswith("./"):
                continue
            external_uses.append((workflow, line_number, line))
            action, separator, revision = match.group(1).partition("@")
            assert separator and FULL_SHA.fullmatch(
                revision
            ), f"{workflow.name}:{line_number} must pin {action} to a full SHA"
            assert match.group(
                2
            ), f"{workflow.name}:{line_number} must comment the reviewed release"

    assert external_uses, "expected at least one external action"


def test_dependency_gates_audit_only_the_four_shipped_surfaces_and_block() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    dependency_job = workflow.split("  dependency-security:\n", 1)[1].split(
        "\n  lint:\n", 1
    )[0]
    audit_prerequisites = "scripts/ci_apt_install.sh libcairo2-dev pkg-config"
    strict_runtime_audit = "pip-audit --requirement requirements.txt --strict"
    strict_development_audit = "pip-audit --requirement requirements-dev.txt --strict"

    assert "pip-audit==2.10.0" in workflow
    assert audit_prerequisites in dependency_job
    assert strict_runtime_audit in dependency_job
    assert strict_development_audit in dependency_job
    assert dependency_job.index(audit_prerequisites) < dependency_job.index(
        strict_runtime_audit
    )
    assert "npm --prefix cli audit --audit-level=high" in workflow
    assert "npm --prefix dashboard audit --audit-level=high" in workflow
    assert "npm --prefix pa11y-runtime audit --audit-level=high" in workflow
    assert workflow.count(" audit --audit-level=high") == 3
    assert "dependency-review-action@" in workflow
    assert "fail-on-severity: high" in workflow
    assert "allow-licenses:" not in workflow


def test_api_image_builds_lxml_from_hash_verified_sources() -> None:
    api = (ROOT / "Dockerfile").read_text()
    script = (ROOT / "docker" / "build-lxml-wheel.sh").read_text()

    assert "sh /tmp/build-lxml-wheel.sh /tmp/lxml-wheel" in api
    assert "e.LIBXML_VERSION >= (2, 15, 4)" in api
    assert "sha256sum --check --strict" in script
    sources = {
        "lxml-6.1.3.tar.gz": (
            "45222d94ddd511536f3b2f7d9deae3b2339b4ce0f075f1ca25703b07cad9dd21"
        ),
        "libxml2-2.15.4.tar.xz": (
            "98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821"
        ),
        "libxslt-1.1.45.tar.xz": (
            "9acfe68419c4d06a45c550321b3212762d92f41465062ca4ea19e632ee5d216e"
        ),
        "zlib-1.3.2.tar.gz": (
            "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16"
        ),
        "libiconv-1.18.tar.gz": (
            "3b08f5f4f9b4eb82f151a7040bfd6fe6c6fb922efe4b1659c66ea933276965e8"
        ),
    }
    for name, digest in sources.items():
        assert name in script and digest in script
    # zlib.net removes superseded releases from its top level; the fossils
    # archive keeps every release at a permanent address.
    assert "fetch https://zlib.net/fossils/zlib-1.3.2.tar.gz" in script
    # The zlib repository's release asset carries the same bytes and is only a
    # fallback: it is passed to the same verifying helper with the same pin.
    zlib_call = script.split("fetch https://zlib.net/fossils/zlib-1.3.2.tar.gz", 1)[
        1
    ].split("\nfetch ", 1)[0]
    assert sources["zlib-1.3.2.tar.gz"] in zlib_call
    assert (
        "https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz"
        in zlib_call
    )
    # Every download goes through the verifying helper.
    assert script.count("fetch https://") == len(sources)
    assert script.count("curl ") == 1


def test_api_image_builds_pillow_from_hash_verified_sources() -> None:
    api = (ROOT / "Dockerfile").read_text()
    script = (ROOT / "docker" / "build-pillow-wheel.sh").read_text()
    builder = api.split("AS builder", 1)[1].split("AS pa11y-node", 1)[0]
    runtime = api.split("AS runtime", 1)[1]

    # The wheel is built from source in the builder, after lxml and before the
    # lock is installed, so the pinned requirement is already satisfied.
    assert "COPY docker/build-pillow-wheel.sh /tmp/build-pillow-wheel.sh" in builder
    assert "sh /tmp/build-pillow-wheel.sh /tmp/pillow-wheel" in builder
    assert "/tmp/pillow-wheel/pillow-13.0.0-*.whl" in builder
    assert builder.index("build-lxml-wheel.sh /tmp/lxml-wheel") < builder.index(
        "build-pillow-wheel.sh /tmp/pillow-wheel"
    )
    assert builder.index("build-pillow-wheel.sh /tmp/pillow-wheel") < builder.index(
        "pip install --no-cache-dir -r requirements.txt"
    )
    assert "pillow==13.0.0" in (ROOT / "requirements.txt").read_text().splitlines()
    # The build proves the libtiff that Pillow reports and that no wheel-private
    # library directory exists.
    assert "features.version('libtiff'); assert v == '4.7.2'" in builder
    assert "pillow.libs" in builder
    # pybind11 is a build requirement only and must not reach the image.
    assert '"pybind11==3.1.0"' in builder
    assert "pip uninstall --yes pybind11" in builder

    # Every archive is verified against a pinned digest before use.
    assert "sha256sum --check --strict" in script
    sources = {
        "tiff-4.7.2.tar.xz": (
            "4996f0c4f93094719b1ca5c6279b20e588773ba8a247533e486416fb662ddb88"
        ),
        "pillow-13.0.0.tar.gz": (
            "cdcd1ba258178f7356d74170c9a81ba4eab6a5d55748bc402a5a2a6fe6be3510"
        ),
    }
    for name, digest in sources.items():
        assert name in script and digest in script
    assert script.count("fetch https://") == len(sources)
    assert script.count("curl ") == 1

    # libtiff is linked statically from that build, never from the system, and
    # the published wheel's codec and text-shaping features are required.
    assert "--disable-shared --enable-static" in script
    assert 'TIFF_ROOT="$prefix"' in script
    assert "/usr/include/tiff.h" in script
    for feature in (
        "zlib",
        "jpeg",
        "tiff",
        "freetype",
        "raqm",
        "lcms",
        "webp",
        "jpeg2000",
        "xcb",
        "avif",
    ):
        assert f"-C {feature}=enable" in script
    for define in ("ZIP_SUPPORT", "JPEG_SUPPORT", "LZMA_SUPPORT", "ZSTD_SUPPORT"):
        assert define in script
    # Fixed build path, no debug information and mapped paths keep two builds
    # of the image byte-identical.
    assert "work=/tmp/pillow-build" in script
    assert "-g0 -ffile-prefix-map=$work=." in script

    # The shared libraries Pillow links are installed in the runtime image, the
    # development packages only in the builder, and libtiff in neither.
    for package in (
        "libjpeg62-turbo",
        "libfreetype6",
        "libraqm0",
        "liblcms2-2",
        "libwebp7",
        "libwebpdemux2",
        "libwebpmux3",
        "libopenjp2-7",
        "libavif16",
        "libxcb1",
    ):
        assert re.search(rf"^\s+{package} \\$", runtime, re.MULTILINE)
    for package in (
        "libjpeg-dev",
        "libfreetype-dev",
        "libraqm-dev",
        "liblcms2-dev",
        "libwebp-dev",
        "libopenjp2-7-dev",
        "libavif-dev",
        "libxcb1-dev",
    ):
        assert re.search(rf"^\s+{package} \\$", builder, re.MULTILINE)
    assert not re.search(r"^\s+libtiff\S* \\$", api, re.MULTILINE)


def test_api_and_quickstart_build_pyav_from_verified_sources() -> None:
    api = (ROOT / "Dockerfile").read_text()
    dev = (ROOT / "Dockerfile.dev").read_text()
    script = (ROOT / "docker" / "build-pyav-wheel.sh").read_text()

    for dockerfile in (api, dev):
        source = "sh /tmp/build-pyav-wheel.sh /tmp/pyav-wheel"
        assert source in dockerfile
        assert dockerfile.index(source) < dockerfile.index(
            "pip install --no-cache-dir -r requirements"
        )
        assert "/tmp/pyav-wheel/av-18.1.0-*.whl" in dockerfile
        assert '"Cython==3.2.9"' in dockerfile
        assert "LD_LIBRARY_PATH" not in dockerfile

    assert "COPY --from=builder /opt/ffmpeg9/lib /opt/ffmpeg9/lib" in api
    assert "sha256sum --check --strict" in script
    assert "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e" in script
    assert "47bfc286e1bc9de7ab4681fc2b575cd2460a66919d31ffe1bd5aa54fae531a28" in script
    assert "--enable-shared --disable-static" in script
    assert "--disable-autodetect" in script
    assert "-Wl,--disable-new-dtags,-rpath,$prefix/lib" in script


def test_api_image_does_not_ship_pi_heif() -> None:
    requirements = (ROOT / "requirements.txt").read_text().lower()

    # ocrmypdf 17 does not depend on pi_heif and loads the optional pillow_heif
    # module only when it is installed, so the bundled libheif and libde265 are
    # unused.
    assert "pi_heif" not in requirements and "pi-heif" not in requirements
    assert "pillow_heif" not in requirements and "pillow-heif" not in requirements
    assert (
        '"pi-heif": None'
        in (ROOT / "scripts" / "verify_final_python_packages.py").read_text()
    )
    assert (
        "pi_heif"
        not in (ROOT / "docs" / "DEPENDENCIES.md")
        .read_text()
        .split("### Transitive dependencies", 1)[1]
    )


def test_docker_ci_proves_the_image_libraries_and_ocr_in_the_built_image() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    publish = (WORKFLOWS / "publish-docker.yml").read_text()

    assert "scripts/smoke_image_libraries.py" in workflow
    assert (
        "--entrypoint /opt/venv/bin/python aelira-worker-isolation:test \\\n"
        "            /app/scripts/smoke_image_libraries.py"
    ) in workflow
    # Both the CI-built and the published image run the venv package check that
    # asserts the libtiff version and the absence of pillow.libs.
    assert "/app/scripts/verify_final_python_packages.py venv" in workflow
    assert "/app/scripts/verify_final_python_packages.py venv" in publish


def test_api_image_builds_psycopg2_from_the_hash_verified_source_archive() -> None:
    script = (ROOT / "docker" / "build-psycopg2-wheel.sh").read_text()
    frozen_inputs = (
        ROOT / "runtime-security" / "psycopg2" / "inputs.sha256"
    ).read_text()

    digest = "e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373"
    archive = "psycopg2_binary-2.9.13.tar.gz"
    # The image build and the frozen native recipe pin the same source bytes.
    assert f"{digest}  {archive}" in frozen_inputs
    assert archive in script and digest in script
    assert "sha256sum --check --strict" in script
    # Every download goes through the verifying helper, and the wheel is built
    # from that archive against the system libpq rather than fetched from PyPI.
    assert script.count("fetch https://") == 1
    assert script.count("curl ") == 1
    assert "--no-build-isolation" in script and "--no-deps" in script
    assert "static_libpq" not in script and "PSYCOPG" not in script


def test_production_dockerfiles_pin_bases_and_downloaded_voice_bytes() -> None:
    api = (ROOT / "Dockerfile").read_text()
    dashboard = (ROOT / "dashboard" / "Dockerfile").read_text()

    python_base = (
        "python:3.14-slim@sha256:"
        "a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170"
    )
    assert api.count(f"FROM {python_base}") == 4
    assert "pip install --no-cache-dir piper-tts" not in api
    assert "npm ci --omit=dev --no-audit --no-fund" in api
    assert {name: digest for name, _, digest in VOICE_ASSETS} == {
        "en_US-lessac-medium.onnx": (
            "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
        ),
        "en_US-lessac-medium.onnx.json": (
            "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0"
        ),
    }
    voice_download = "RUN python scripts/download_piper_voice.py"
    for dockerfile in (api, (ROOT / "Dockerfile.dev").read_text()):
        assert dockerfile.count(voice_download) == 1
        assert dockerfile.index(voice_download) < dockerfile.index("USER aelira")

    assert (
        "FROM node:22-alpine@sha256:"
        "c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32"
        " AS builder"
    ) in dashboard
    assert (
        "FROM nginx:alpine@sha256:"
        "db35bfc6b2951e7f8a72db5db120288c127ffaeeb4a6d4b95a26fead017d5913"
    ) in dashboard
    openssl_upgrade = "RUN apk upgrade --no-cache libcrypto3 libssl3"
    runtime = dashboard.split("# Production stage", 1)[1]
    assert runtime.count(openssl_upgrade) == 1
    assert "&& rm -f /var/log/apk.log" in runtime
    assert runtime.index(openssl_upgrade) < runtime.index("COPY --from=builder")


def test_api_images_replace_only_the_matching_python_expat_extensions() -> None:
    build_script = (ROOT / "docker" / "build-python-expat-modules.sh").read_text()
    verifier = (ROOT / "scripts" / "verify_final_python_packages.py").read_text()
    layer_gate = (ROOT / "scripts" / "verify_python_expat_layers.ts").read_text()
    debian_base = (
        "debian:trixie-slim@sha256:"
        "a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f"
    )
    for name in ("Dockerfile", "Dockerfile.dev"):
        dockerfile = (ROOT / name).read_text()
        assert f"FROM {debian_base} AS python-expat-base" in dockerfile
        assert "FROM python-expat-base AS runtime" in dockerfile
        assert "libexpat1=${EXPAT_DEB_VERSION}" in dockerfile
        assert "libexpat1-dev=${EXPAT_DEB_VERSION}" in dockerfile
        assert "COPY --from=python-expat-clean /usr/local/ /usr/local/" in dockerfile
        assert (
            "COPY --from=python-expat-modules /tmp/python-expat-modules/ "
            "/usr/local/lib/python3.14/lib-dynload/"
        ) in dockerfile
        assert dockerfile.index("RUN ldconfig") < dockerfile.index(
            "FROM python-expat-base AS runtime"
        )
        for module in ("pyexpat", "_elementtree"):
            assert (
                f"/usr/local/lib/python3.14/lib-dynload/{module}.cpython-314-*.so"
                in dockerfile
            )
        assert (
            "sh /tmp/build-python-expat-modules.sh /tmp/python-expat-modules"
            in dockerfile
        )

    assert (
        "c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73"
        in build_script
    )
    assert "sha256sum --check --strict" in build_script
    assert "--with-system-expat" in build_script
    assert 'make -j2 "Modules/pyexpat' in build_script
    assert '"Modules/_elementtree' in build_script
    assert 'EXPAT_REPORTED_VERSION = "expat_2.9.0"' in verifier
    assert "libexpat.so.1" in verifier
    assert '"docker", ["save", "--output", archive, image]' in layer_gate
    assert 'found.has("pyexpat")' in layer_gate
    assert 'found.has("_elementtree")' in layer_gate

    ci = (WORKFLOWS / "ci.yml").read_text()
    publish = (WORKFLOWS / "publish-docker.yml").read_text()
    assert ci.count("verify_python_expat_layers.ts") == 2
    assert publish.count("verify_python_expat_layers.ts") == 1
    assert "/app/scripts/verify_final_python_packages.py expat" in ci


def test_api_images_prove_the_pinned_pa11y_runtime_as_the_final_user() -> None:
    production = (ROOT / "Dockerfile").read_text()
    development = (ROOT / "Dockerfile.dev").read_text()
    smoke_command = "RUN python scripts/smoke_pa11y_runtime.py &&"
    configure_command = "RUN python scripts/configure_pa11y_chromium.py"
    verified_marker = (
        "/tmp/pa11y-runtime-verified "
        "/home/aelira/.local/share/aelira/pa11y-runtime-verified"
    )

    for dockerfile in (production, development):
        assert dockerfile.count("npm ci --omit=dev --no-audit --no-fund") == 1
        assert (
            dockerfile.count(
                "COPY pa11y-runtime/package.json pa11y-runtime/package-lock.json ./"
            )
            == 1
        )
        assert "ENV PUPPETEER_SKIP_DOWNLOAD=true" in dockerfile
        assert "ENV PA11Y_CONFIG_PATH=/app/config/pa11y.json" in dockerfile
        assert (
            "ENV PA11Y_CHROMIUM_PATH=/home/aelira/.local/bin/aelira-chromium"
            in dockerfile
        )
        assert dockerfile.count("USER aelira") == 1
        assert dockerfile.count(configure_command) == 1
        assert dockerfile.count(smoke_command) == 1
        assert dockerfile.count("FROM runtime AS pa11y-verified") == 1
        assert dockerfile.count("FROM runtime AS final") == 1
        assert dockerfile.count(verified_marker) == 1
        assert dockerfile.index("USER aelira") < dockerfile.index(configure_command)
        assert dockerfile.index(configure_command) < dockerfile.index(
            "FROM runtime AS pa11y-verified"
        )
        assert dockerfile.index("FROM runtime AS pa11y-verified") < dockerfile.index(
            smoke_command
        )
        assert dockerfile.index(smoke_command) < dockerfile.index(
            "FROM runtime AS final"
        )
        assert dockerfile.index("FROM runtime AS final") < dockerfile.index(
            verified_marker
        )

    config = (ROOT / "config" / "pa11y.json").read_text()
    assert '"executablePath": "/home/aelira/.local/bin/aelira-chromium"' in config
    assert '"--no-sandbox"' in config
    assert '"--disable-setuid-sandbox"' in config
    assert '"--disable-dev-shm-usage"' in config

    smoke = (ROOT / "scripts" / "smoke_pa11y_runtime.py").read_text()
    assert "os.geteuid() == 0" in smoke
    assert "SUPPORTED_NODE_MAJORS = {24}" in smoke
    assert 'EXPECTED_PA11Y_VERSION = "9.0.1"' in smoke
    assert 'EXPECTED_BROWSERS_VERSION = "3.2.4"' in smoke
    assert 'FORBIDDEN_RUNTIME_PACKAGES = ("basic-ftp", "extract-zip")' in smoke
    assert 'TemporaryDirectory(prefix="aelira-pa11y-smoke-")' in smoke
    assert 'smoke_env["HOME"] = smoke_home' in smoke
    assert 'smoke_env["XDG_CACHE_HOME"]' in smoke
    assert 'smoke_env["XDG_CONFIG_HOME"]' in smoke
    assert "env=smoke_env" in smoke
    assert "ThreadingHTTPServer" in smoke
    assert "scripts/fixtures/pa11y-smoke.html" in smoke


def test_pa11y_lock_omits_the_vulnerable_ftp_and_extractor_chain() -> None:
    manifest = json.loads((ROOT / "pa11y-runtime" / "package.json").read_text())
    lock = json.loads((ROOT / "pa11y-runtime" / "package-lock.json").read_text())

    assert manifest["dependencies"] == {"pa11y": "9.0.1"}
    assert manifest["overrides"] == {"@puppeteer/browsers": "3.2.4"}
    assert lock["packages"]["node_modules/pa11y"]["version"] == "9.0.1"
    browsers = lock["packages"]["node_modules/@puppeteer/browsers"]
    assert browsers["version"] == "3.2.4"
    for optional_peer in ("proxy-agent", "yauzl"):
        assert browsers["peerDependenciesMeta"][optional_peer]["optional"] is True
    assert not any(
        path.endswith(
            ("/basic-ftp", "/extract-zip", "/get-uri", "/proxy-agent", "/yauzl")
        )
        for path in lock["packages"]
    )


def test_pa11y_evidence_claims_are_runtime_derived() -> None:
    route = (ROOT / "src" / "api" / "education" / "web_scan_routes.py").read_text()
    modes = (ROOT / "src" / "scanners" / "scan_mode.py").read_text()
    dashboard = (
        ROOT
        / "dashboard"
        / "src"
        / "components"
        / "results"
        / "EngineComparisonStats.tsx"
    ).read_text()

    assert "if should_run_pa11y(mode):" in route
    assert "if False" not in route
    assert "estimate_coverage_for_engines(engines_used)" in route
    assert '"comprehensive": 95.0' not in route
    assert "estimated_coverage_pct = 90" not in dashboard
    assert "~90% coverage" not in dashboard
    assert "~95%+ coverage" not in dashboard
    assert "90% coverage" not in modes
    assert "95%+ coverage" not in modes


def test_api_dockerfile_normalizes_content_level_build_nondeterminism() -> None:
    api = (ROOT / "Dockerfile").read_text()

    assert api.count("ARG SOURCE_DATE_EPOCH=0") == 2
    assert api.count("PYTHONHASHSEED=0") == 2
    assert api.count("FORCE_SOURCE_DATE=1") == 1
    assert api.count("PERL_HASH_SEED=0") == 1
    assert api.count("PERL_PERTURB_KEYS=0") == 1
    assert api.count('export SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"') == 2
    assert "apt-get install -y --no-install-recommends" in api
    assert "&& update-language" in api
    assert "find /var/lib/texmf/web2c -type f -name '*.fmt' -delete" in api
    assert "/var/cache/fontconfig/*" in api
    assert "/var/cache/ldconfig/aux-cache" in api
    assert "/var/lib/texmf/ls-R" in api
    assert "/var/log/apt/*" in api
    assert "/var/log/alternatives.log" in api
    assert "/var/log/dpkg.log" in api
    assert "find /var/lib/texmf -type f -name '*.log' -delete" in api
    assert "npm cache clean --force" in api
    assert "rm -rf /root/.npm" in api
    assert "ENV SOURCE_DATE_EPOCH" not in api


def test_api_runtime_upgrades_available_debian_packages_before_install() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    update = "apt-get update"
    bounded_upgrade = (
        "timeout 10m apt-get -o Acquire::Retries=2 "
        "-o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 upgrade -y"
    )
    install = "apt-get install -y --no-install-recommends"

    assert runtime.count(bounded_upgrade) == 1
    assert (
        runtime.index(update) < runtime.index(bounded_upgrade) < runtime.index(install)
    )


def test_api_builder_removes_bootstrap_setuptools_before_reviewed_requirements() -> (
    None
):
    api = (ROOT / "Dockerfile").read_text()
    builder = api.split("# Stage 2: Runtime", 1)[0]
    uninstall = "python -m pip uninstall --yes setuptools"
    requirements = "pip install --no-cache-dir -r requirements.txt"

    assert builder.count(uninstall) == 1
    assert builder.index(uninstall) < builder.index(requirements)


def test_api_runtime_replaces_global_msgpack_with_reviewed_version() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    copy_requirements = "COPY requirements.txt /app/requirements.txt"
    copy_verifier = (
        "COPY scripts/verify_final_python_packages.py "
        "/app/scripts/verify_final_python_packages.py"
    )
    uninstall = "/usr/local/bin/python -m pip uninstall --yes msgpack"
    install = (
        "/usr/local/bin/python -m pip install --no-cache-dir "
        '"msgpack==$(/usr/local/bin/python '
        '/app/scripts/verify_final_python_packages.py msgpack-pin)"'
    )
    copy_venv = "COPY --from=builder /opt/venv /opt/venv"

    assert runtime.count(copy_requirements) == 1
    assert runtime.count(copy_verifier) == 1
    assert runtime.count(uninstall) == 1
    assert runtime.count(install) == 1
    assert runtime.index(copy_requirements) < runtime.index(uninstall)
    assert runtime.index(copy_verifier) < runtime.index(uninstall)
    assert runtime.index(uninstall) < runtime.index(install) < runtime.index(copy_venv)


def test_api_build_asserts_global_and_copied_venv_python_package_versions() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    copy_venv = "COPY --from=builder /opt/venv /opt/venv"
    global_assertion = (
        "/usr/local/bin/python /app/scripts/verify_final_python_packages.py global"
    )
    venv_assertion = (
        "/opt/venv/bin/python /app/scripts/verify_final_python_packages.py venv"
    )

    assert runtime.count(global_assertion) == 1
    assert runtime.count(venv_assertion) == 1
    assert runtime.index(copy_venv) < runtime.index(global_assertion)
    assert runtime.index(global_assertion) < runtime.index(venv_assertion)


def test_api_runtime_omits_unused_packages_and_verifies_their_absence() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    install, tail = runtime.split("&& update-language", 1)
    purge = "&& dpkg --purge --force-depends xdg-utils"
    cleanup = "&& rm -rf /var/lib/apt/lists/*"
    copy_verifier = (
        "COPY scripts/verify_final_os_packages.py "
        "/app/scripts/verify_final_os_packages.py"
    )
    os_assertion = "/usr/local/bin/python /app/scripts/verify_final_os_packages.py"

    # libxml2-utils is never installed; xdg-utils is a hard dependency of
    # texlive-base, so it is purged in the same layer before apt state is cleaned.
    assert "\n    libxml2-utils \\" not in install
    assert "\n    xdg-utils \\" not in install
    assert tail.count(purge) == 1
    assert tail.index(purge) < tail.index(cleanup)
    assert runtime.count(copy_verifier) == 1
    assert runtime.count(os_assertion) == 1
    assert runtime.index(copy_verifier) < runtime.index(os_assertion)
    assert runtime.index(os_assertion) < runtime.index("# Set working directory")

    ci_docker = (WORKFLOWS / "ci.yml").read_text().split("  docker:\n", 1)[1]
    publish = (WORKFLOWS / "publish-docker.yml").read_text()
    assert ci_docker.count("/app/scripts/verify_final_os_packages.py") == 1
    assert publish.count("/app/scripts/verify_final_os_packages.py") == 1


def test_api_runtime_satisfies_texlive_base_with_an_offline_placeholder() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    purge = "&& dpkg --purge --force-depends xdg-utils"
    cleanup = "&& rm -rf /var/lib/apt/lists/*"
    install = "&& dpkg --install /tmp/aelira-xdg-utils-absent.deb"
    remove = "&& rm -rf /tmp/aelira-xdg-utils-absent /tmp/aelira-xdg-utils-absent.deb"
    build = "dpkg-deb --root-owner-group --build /tmp/aelira-xdg-utils-absent "

    # Built, installed and cleaned up inside the RUN that purges xdg-utils, so
    # neither the recipe's files nor a broken dpkg state reach a later layer.
    assert runtime.count(purge) == 1
    start = runtime.index(purge)
    block = runtime[start : runtime.index(cleanup, start)]
    assert block.count(build) == 1
    assert block.index(build) < block.index(install) < block.index(remove)

    # The control file declares exactly the reviewed fields. No Depends,
    # Conflicts, Replaces or Source can pull in or hide behind another package.
    fields = dict(re.findall(r"^\s+'([A-Za-z-]+): ([^']*)' \\$", block, re.MULTILINE))
    assert set(fields) == {
        "Package",
        "Version",
        "Architecture",
        "Maintainer",
        "Provides",
        "Description",
    }
    assert fields["Package"] == PLACEHOLDER_PACKAGE
    assert fields["Version"] == "1.0"
    assert fields["Architecture"] == "all"
    assert (fields["Provides"],) == PLACEHOLDER_PROVIDES
    assert fields["Maintainer"] and fields["Description"]
    assert fields["Package"] != "xdg-utils"

    # It needs no network, and the release safety gate rejects any address at
    # the vendor domain, so the recipe carries no email address at all.
    for forbidden in ("apt-get", "curl", "wget", "http", "@"):
        assert forbidden not in block


def test_final_image_acceptance_runs_apt_get_check_on_both_architectures() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text().split("  docker:\n", 1)[1]
    publish = (WORKFLOWS / "publish-docker.yml").read_text()
    ci_step = ci.split(
        "      - name: Verify production package state and baked Piper synthesis\n", 1
    )[1].split("\n      - name:", 1)[0]
    publish_step = publish.split(
        "      - name: Verify final API image package state\n", 1
    )[1].split("\n      - name:", 1)[0]
    ci_verifier = "--entrypoint /usr/local/bin/python aelira-worker-isolation:test /app/scripts/verify_final_os_packages.py"
    ci_check = "docker run --rm --network none --user 0 --entrypoint apt-get aelira-worker-isolation:test check"
    publish_check = 'docker run --rm --user 0 --entrypoint apt-get "$IMAGE" check'

    # apt needs root to open its state, and runs next to the dpkg-level check.
    assert ci.count(ci_check) == 1
    assert publish.count(publish_check) == 1
    assert ci_step.index(ci_verifier) < ci_step.index(ci_check)
    assert publish_step.index("verify_final_os_packages.py") < publish_step.index(
        publish_check
    )
    # Neither step is conditional on one architecture, and both jobs run a
    # native amd64 and a native arm64 leg.
    for step in (ci_step, publish_step):
        assert "matrix.platform" not in step
        assert "matrix.arch" not in step
        assert "continue-on-error" not in step
    assert "platform: linux/amd64" in ci and "platform: linux/arm64" in ci
    assert "platform: linux/amd64" in publish and "platform: linux/arm64" in publish


DISTRUST_STEP_START = "RUN if grep -q '^mozilla/emSign_ECC_Root_CA_-_G3"
DISTRUST_STEP_END = "\n    fi"


def _distrust_step(dockerfile: str) -> str:
    """Return the shell of the step that deselects emSign ECC Root CA - G3."""
    start = dockerfile.index(DISTRUST_STEP_START)
    end = dockerfile.index(DISTRUST_STEP_END, start) + len(DISTRUST_STEP_END)
    return dockerfile[start + len("RUN ") : end]


def _runtime_stage(dockerfile: str) -> str:
    assert dockerfile.count("AS runtime\n") == 1
    return dockerfile.split("AS runtime\n", 1)[1]


@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.dev"])
def test_runtime_images_distrust_emsign_ecc_g3_after_the_last_apt_operation(
    name: str,
) -> None:
    stage = _runtime_stage((ROOT / name).read_text())
    step = _distrust_step(stage)
    before, after = stage.split(DISTRUST_STEP_START, 1)

    # Reproduced verbatim in both images, so a fix in one cannot miss the other.
    assert step == _distrust_step((ROOT / "Dockerfile").read_text())
    assert step == _distrust_step((ROOT / "Dockerfile.dev").read_text())
    # Only the G3 line is deselected, with a "!" prefix, then the store is
    # rebuilt from scratch so the bundle and the hashed links both drop it.
    assert step.count("emSign_ECC_Root_CA_-_G3") == 2
    for unaffected in (
        "emSign_Root_CA_-_G1",
        "emSign_Root_CA_-_C1",
        "emSign_ECC_Root_CA_-_C3",
    ):
        assert unaffected not in step
    assert "|!&|" in step
    assert step.count("update-ca-certificates --fresh") == 1
    assert step.index("sed -i") < step.index("update-ca-certificates --fresh")
    # A later apt upgrade could re-enable the root, so nothing installs after.
    assert "apt-get" in before
    assert "apt-get" not in after


def test_dockerfiles_verify_the_trust_store_where_each_image_is_built() -> None:
    api = _runtime_stage((ROOT / "Dockerfile").read_text())
    dev = _runtime_stage((ROOT / "Dockerfile.dev").read_text())
    os_assertion = "/usr/local/bin/python /app/scripts/verify_final_os_packages.py"

    # Production runs every check, including the trust store, after the step.
    assert api.count(os_assertion) == 1
    assert api.index(DISTRUST_STEP_START) < api.index(os_assertion)
    assert f"{os_assertion} trust-store" not in api
    # The developer image has no xdg-utils placeholder, so it runs only the
    # trust-store check, in the same layer as the step it verifies.
    copy = (
        "COPY scripts/verify_final_os_packages.py "
        "/app/scripts/verify_final_os_packages.py"
    )
    assert dev.count(copy) == 1
    # The second run checks Node's built-in store once Node is copied in.
    assert dev.count(os_assertion) == 2
    assert dev.count(f"{os_assertion} node-trust-store") == 1
    step_start = dev.index(DISTRUST_STEP_START)
    layer = dev[step_start : dev.index("\n\n", step_start)]
    assert dev.index(copy) < step_start
    assert layer.endswith(f"&& {os_assertion} trust-store")


def test_quickstart_ci_checks_the_developer_image_trust_store() -> None:
    ci = (WORKFLOWS / "ci.yml").read_text()
    quickstart = ci.split("\n  quickstart:\n", 1)[1].split("\n  docker:\n", 1)[0]
    check = (
        "docker compose -f docker-compose.quickstart.yml exec -T api \\\n"
        "            /usr/local/bin/python /app/scripts/verify_final_os_packages.py "
        "trust-store"
    )

    assert quickstart.count(check) == 1
    assert quickstart.index(
        "verify_final_python_packages.py quickstart"
    ) < quickstart.index(check)
    assert quickstart.count("verify_final_os_packages.py node-trust-store") == 1


def _gnu_sed() -> bool:
    result = subprocess.run(["sed", "--version"], capture_output=True, check=False)
    return result.returncode == 0 and b"GNU" in result.stdout


def _run_distrust_step(
    tmp_path: Path, conf_text: str
) -> tuple[subprocess.CompletedProcess[str], str, list[str]]:
    conf = tmp_path / "ca-certificates.conf"
    conf.write_text(conf_text)
    log = tmp_path / "update.log"
    stub = tmp_path / "bin"
    stub.mkdir()
    tool = stub / "update-ca-certificates"
    tool.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    tool.chmod(0o755)
    shell = _distrust_step((ROOT / "Dockerfile").read_text()).replace(
        "/etc/ca-certificates.conf", str(conf)
    )
    result = subprocess.run(
        ["sh", "-c", shell],
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, conf.read_text(), calls


EMSIGN_CONF = (
    "# Debian ca-certificates.conf\n"
    "mozilla/Entrust_Root_Certification_Authority.crt\n"
    "mozilla/emSign_ECC_Root_CA_-_C3.crt\n"
    "{g3}\n"
    "mozilla/emSign_Root_CA_-_C1.crt\n"
    "mozilla/emSign_Root_CA_-_G1.crt\n"
)


@pytest.mark.skipif(not _gnu_sed(), reason="the image build uses GNU sed")
def test_distrust_step_deselects_only_the_g3_root_and_rebuilds_the_store(
    tmp_path: Path,
) -> None:
    result, conf, calls = _run_distrust_step(
        tmp_path, EMSIGN_CONF.format(g3="mozilla/emSign_ECC_Root_CA_-_G3.crt")
    )

    assert result.returncode == 0, result.stderr
    assert conf == EMSIGN_CONF.format(g3="!mozilla/emSign_ECC_Root_CA_-_G3.crt")
    assert calls == ["--fresh"]


@pytest.mark.skipif(not _gnu_sed(), reason="the image build uses GNU sed")
@pytest.mark.parametrize(
    "g3",
    [
        # Debian's update that still ships the file but marks it deselected.
        "!mozilla/emSign_ECC_Root_CA_-_G3.crt",
        # Debian's update that removed the root from the list altogether.
        None,
    ],
)
def test_distrust_step_does_nothing_once_the_package_has_dropped_the_root(
    tmp_path: Path, g3: str | None
) -> None:
    template = EMSIGN_CONF if g3 else EMSIGN_CONF.replace("{g3}\n", "")
    text = template.format(g3=g3)

    result, conf, calls = _run_distrust_step(tmp_path, text)

    assert result.returncode == 0, result.stderr
    assert conf == text
    assert calls == []


@pytest.mark.skipif(not _gnu_sed(), reason="the image build uses GNU sed")
def test_distrust_step_fails_the_build_when_the_store_cannot_be_rebuilt(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "ca-certificates.conf"
    conf.write_text(EMSIGN_CONF.format(g3="mozilla/emSign_ECC_Root_CA_-_G3.crt"))
    stub = tmp_path / "bin"
    stub.mkdir()
    tool = stub / "update-ca-certificates"
    tool.write_text("#!/bin/sh\nexit 7\n")
    tool.chmod(0o755)
    shell = _distrust_step((ROOT / "Dockerfile").read_text()).replace(
        "/etc/ca-certificates.conf", str(conf)
    )

    result = subprocess.run(
        ["sh", "-c", shell],
        env={"PATH": f"{stub}:{os.environ['PATH']}"},
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7


def test_ci_reproducibility_jobs_use_an_oci_capable_buildx_driver() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    docker_job = workflow.split("  docker:\n", 1)[1]
    setup = (
        "uses: docker/setup-buildx-action@"
        "f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1"
    )

    assert setup in docker_job
    assert docker_job.index(setup) < docker_job.index(
        "scripts/verify_reproducible_image.sh"
    )


def test_immutable_image_gate_precedes_receipts_and_signs_version_index() -> None:
    workflow = (WORKFLOWS / "publish-docker.yml").read_text()

    for action in (
        "anchore/sbom-action@66cbf4bc1f1c0d2edc94016e65bc221b6bb0ad6c # v0.24.3",
        "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # 0.36.0",
        "sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2",
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2",
    ):
        assert action in workflow
    pinned_cosign_install = (
        "uses: sigstore/cosign-installer@"
        "6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2\n"
        "        with:\n"
        "          cosign-release: 'v2.6.1'"
    )
    assert workflow.count(pinned_cosign_install) == 2
    assert "format: spdx-json" in workflow
    final_package_gate = "Verify final API image package state"
    assert workflow.count(final_package_gate) == 1
    assert "if: matrix.image == 'aelira-core-api'" in workflow
    assert 'docker pull "$IMAGE"' in workflow
    assert workflow.count("/app/scripts/verify_final_python_packages.py") == 2
    assert "/usr/local/bin/python" in workflow
    assert "/opt/venv/bin/python" in workflow
    assert workflow.index(final_package_gate) < workflow.index(
        "Generate SPDX JSON SBOM for immutable image"
    )
    assert "exit-code: '1'" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert "ignore-unfixed: true" in workflow
    assert "trivyignores: .trivyignore.inventory" in workflow
    assert "trivyignores: .trivyignore" in workflow
    assert "subject-digest: ${{ steps.build.outputs.digest }}" in workflow
    assert "push-to-registry: true" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "GITHUB_REPOSITORY: ${{ github.repository }}" in workflow
    assert 'gh attestation verify "oci://$IMAGE@$DIGEST"' in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert workflow.index("gh attestation verify") > workflow.index(
        "Attest immutable image provenance"
    )
    assert workflow.index("Write digest receipt") > workflow.index(
        "gh attestation verify"
    )
    assert 'cosign sign --yes "$IMAGE@$DIGEST"' in workflow
    assert "cosign verify" in workflow
    assert "https://token.actions.githubusercontent.com" in workflow
    assert workflow.index("Upload digest receipt") > workflow.index("cosign verify")

    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "VERSION_DIGEST=" in workflow
    assert 'cosign sign --yes "$image@$VERSION_DIGEST"' in workflow
    assert 'cosign verify "$image@$VERSION_DIGEST"' in workflow


def test_trivy_preserves_unfixed_inventory_and_blocks_actionable_findings() -> None:
    workflow = (WORKFLOWS / "publish-docker.yml").read_text()
    inventory = workflow.split(
        "      - name: Inventory all high and critical vulnerabilities\n", 1
    )[1].split("\n      - name: Upload Trivy vulnerability inventory", 1)[0]
    upload = workflow.split("      - name: Upload Trivy vulnerability inventory\n", 1)[
        1
    ].split("\n      - name: Scan immutable image", 1)[0]
    blocking = workflow.split(
        "      - name: Scan immutable image for fixed high and critical vulnerabilities\n",
        1,
    )[1].split("\n      - name: Attest immutable image provenance", 1)[0]

    for expected in (
        "format: json",
        "output: ${{ matrix.image }}-${{ matrix.arch }}.trivy.json",
        "exit-code: '0'",
        "severity: HIGH,CRITICAL",
        "ignore-unfixed: false",
        "trivyignores: .trivyignore.inventory",
    ):
        assert expected in inventory
    for expected in (
        "name: trivy-${{ matrix.image }}-${{ matrix.arch }}-${{ github.run_attempt }}",
        "path: ${{ matrix.image }}-${{ matrix.arch }}.trivy.json",
        "retention-days: 90",
        "if-no-files-found: error",
        "overwrite: false",
    ):
        assert expected in upload
    for expected in (
        "format: table",
        "exit-code: '1'",
        "severity: HIGH,CRITICAL",
        "ignore-unfixed: true",
        "trivyignores: .trivyignore",
    ):
        assert expected in blocking
    assert inventory.count("trivyignores:") == 1
    assert blocking.count("trivyignores:") == 1
    assert workflow.index(
        "Inventory all high and critical vulnerabilities"
    ) < workflow.index(
        "Scan immutable image for fixed high and critical vulnerabilities"
    )


def test_trivy_inventory_ignore_contains_no_vulnerability_ids() -> None:
    inventory_ignore = ROOT / ".trivyignore.inventory"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", inventory_ignore.name],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert tracked.returncode == 0
    assert inventory_ignore.is_file()
    assert not re.search(
        r"(?m)^\s*(?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[23456789cfghjmpqrvwx]{4}(?:-[23456789cfghjmpqrvwx]{4}){2})\s*$",
        inventory_ignore.read_text(),
    )


def test_trivy_blocking_ignore_has_exact_bounded_layer_false_positives() -> None:
    blocking_ignore = ROOT / ".trivyignore"
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", blocking_ignore.name],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert tracked.returncode == 0
    assert blocking_ignore.is_file()
    allowlist = blocking_ignore.read_text()
    vulnerability_ids = re.findall(
        r"(?m)^\s*((?:CVE-[0-9]{4}-[0-9]{4,}|GHSA-[23456789cfghjmpqrvwx]{4}(?:-[23456789cfghjmpqrvwx]{4}){2}))\s*$",
        allowlist,
    )
    assert vulnerability_ids == ["GHSA-6v7p-g79w-8964", "CVE-2025-47273"]
    owners = re.findall(r"(?m)^# owner: (.+)$", allowlist)
    assert owners == ["security-team", "security-team"]
    assert all("@" not in owner for owner in owners)
    assert allowlist.count("# expires: 2026-10-31") == 2
    justifications = re.findall(r"(?m)^# justification: (.+)$", allowlist)
    assert len(justifications) == 2
    assert all("#160" in justification for justification in justifications)
    assert all(
        "trivy 0.74.0" in justification.lower()
        and "syft 1.52.0" in justification.lower()
        and "final-image gate" in justification.lower()
        for justification in justifications
    )
    documentation = (ROOT / "docs" / "RELEASE_INTEGRITY.md").read_text()
    assert "two active bounded false-positive exemptions" in documentation
    assert "GHSA-6v7p-g79w-8964" in documentation
    assert "CVE-2025-47273" in documentation
    assert "2026-10-31" in documentation
    assert "issue #160" in documentation
    assert "Trivy 0.74.0" in documentation
    assert "Syft 1.52.0" in documentation
    assert "scripts/verify_final_python_packages.py" in documentation
    assert "before SBOM generation" in documentation
    assert "complete unsuppressed inventory" in documentation


def test_release_requires_verified_annotated_tag_and_attaches_exact_sboms() -> None:
    workflow = (WORKFLOWS / "release.yml").read_text()

    assert workflow.count('      - "v*"') == 1
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "GITHUB_REPOSITORY: ${{ github.repository }}" in workflow
    assert 'git rev-parse "$TAG_NAME^{tag}"' in workflow
    assert 'gh api "repos/$GITHUB_REPOSITORY/git/tags/$TAG_OBJECT"' in workflow
    assert ".verification.verified == true" in workflow

    assert "cyclonedx-bom==7.3.1" in workflow
    assert "@cyclonedx/cyclonedx-npm@6.0.1" in workflow
    assert "requirements.txt" in workflow
    assert "cli/package-lock.json" in workflow
    assert "dashboard/package-lock.json" in workflow
    assert "python.cdx.json" in workflow
    assert "cli.cdx.json" in workflow
    assert "dashboard.cdx.json" in workflow
    assert "name: dependency-sboms-${{ github.run_attempt }}" in workflow
    assert (
        "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2"
        in workflow
    )

    assert (
        "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2"
        in workflow
    )
    assert "pattern: dependency-sboms-+([0-9])" in workflow
    assert (
        "pattern: sbom-@(aelira-core-api|aelira-core-dashboard)-"
        "@(amd64|arm64)-+([0-9])" in workflow
    )
    assert "pattern: dependency-sboms-*\n" not in workflow
    assert "pattern: sbom-*-*" not in workflow
    assert "EXPECTED_CYCLONEDX=3" in workflow
    assert "EXPECTED_SPDX=4" in workflow
    assert "${#CYCLONEDX[@]}" in workflow
    assert "${#SPDX[@]}" in workflow
    assert 'gh release create "$TAG_NAME"' in workflow
    assert "--verify-tag" in workflow
    assert '"${CYCLONEDX[@]}"' in workflow
    assert '"${SPDX[@]}"' in workflow

    assert workflow.index("git/tags/$TAG_OBJECT") < workflow.index("docker-publish:")
    assert workflow.index("docker-publish:") < workflow.index("npm-publish:")
    assert workflow.index("npm-publish:") < workflow.index("github-release:")


def test_release_artifact_producer_names_are_isolated_by_run_attempt() -> None:
    docker = (WORKFLOWS / "publish-docker.yml").read_text()
    release = (WORKFLOWS / "release.yml").read_text()
    attempt = "${{ github.run_attempt }}"

    upload_pattern = re.compile(
        r"(?ms)^      - name: .+?\n"
        r"        uses: actions/upload-artifact@.+?"
        r"(?=^      - |^  [a-zA-Z0-9_-]+:|\Z)"
    )
    upload_blocks = upload_pattern.findall(docker) + upload_pattern.findall(release)
    assert len(upload_blocks) == 5
    for block in upload_blocks:
        names = re.findall(r"^          name: (.+)$", block, re.MULTILINE)
        assert len(names) == 1
        assert attempt in names[0]

    for expected in (
        "name: sbom-${{ matrix.image }}-${{ matrix.arch }}-" + attempt,
        "name: receipt-${{ matrix.image }}-${{ matrix.arch }}-" + attempt,
        f"pattern: receipt-*-{attempt}",
        f"name: verified-digest-receipts-{attempt}",
    ):
        assert expected in docker
    assert docker.count(f"name: verified-digest-receipts-{attempt}") == 2
    assert release.count(f"name: dependency-sboms-{attempt}") == 1

    # Attempt isolation belongs to the artifact namespace, not release payload names.
    for canonical_payload in (
        "path: ${{ matrix.image }}-${{ matrix.arch }}.spdx.json",
        "path: ${{ matrix.image }}-${{ matrix.arch }}.json",
        "path: dependency-sboms/*.cdx.json",
    ):
        assert canonical_payload in docker + release
    assert "github.run_attempt }}.json" not in docker


def test_ci_is_read_only_and_runs_reproducibility_and_allowlist_gates() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()

    assert re.search(r"^permissions:\n  contents: read$", workflow, re.MULTILINE)
    assert "python scripts/verify_trivy_allowlist.py .trivyignore" in workflow
    assert workflow.count("scripts/verify_reproducible_image.sh") == 2
    assert "scripts/verify_reproducible_image.sh . Dockerfile" in workflow
    assert (
        "scripts/verify_reproducible_image.sh dashboard "
        "dashboard/Dockerfile" in workflow
    )
    assert workflow.count("${{ matrix.platform }}") >= 2


def test_release_integrity_documentation_is_fail_closed_and_truthful() -> None:
    documentation = (ROOT / "docs" / "RELEASE_INTEGRITY.md").read_text()
    runtime_requirements = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]
    development_lines = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "requirements-dev.txt").read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]
    development_requirements = [
        line for line in development_lines if not line.startswith("-r ")
    ]

    assert development_lines[0] == "-r requirements.txt"
    assert len(runtime_requirements) == 154
    assert len(development_requirements) == 30
    assert (
        f"**{len(runtime_requirements)}** runtime dependency entries" in documentation
    )
    assert f"**{len(development_requirements)}** development-only" in documentation
    assert (
        f"All {len(runtime_requirements) + len(development_requirements)} "
        "application dependencies"
    ) in documentation
    assert all(
        re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^=\s]+", line)
        for line in runtime_requirements + development_requirements
    )
    assert not any(
        "--hash=" in line for line in runtime_requirements + development_requirements
    )
    for required_text in (
        "requirements.txt",
        "requirements-dev.txt",
        "cli/package-lock.json",
        "dashboard/package-lock.json",
        "CycloneDX JSON",
        "SPDX JSON",
        "HIGH,CRITICAL",
        "fixed/actionable",
        "currently-unfixed",
        "remain visible",
        "not silently exempted",
        "complete unsuppressed inventory",
        "`.trivyignore.inventory`",
        "blocking scan",
        "`.trivyignore`",
        "owner",
        "justification",
        "future expiry",
        "90-day",
        "not part of the exact seven",
        "https://token.actions.githubusercontent.com",
        "signed annotated tag",
        "four receipts",
        "coordinated gated publication",
        "partial registry state",
        "not an atomic registry mutation",
        "rerun",
        "isolated artifact namespace",
        "regenerates and validates all four receipts",
        "linux/amd64",
        "linux/arm64",
        "`--pull=false`",
        "no hashes",
        "deferred",
        "Python 3.12, 3.13, and 3.14",
        "fail closed",
    ):
        assert required_text in documentation
    assert "Atomic publication" not in documentation


def test_release_has_one_tag_trigger_and_no_second_publication_path() -> None:
    tag_triggers: list[str] = []
    publication_commands: dict[str, set[str]] = {
        "gh release create": set(),
        "npm publish --access public": set(),
        "docker buildx imagetools create": set(),
    }
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        if re.search(r"(?m)^\s+tags:\s*$", text):
            tag_triggers.append(path.name)
        for command in publication_commands:
            if command in text:
                publication_commands[command].add(path.name)

    assert tag_triggers == ["release.yml"]
    assert publication_commands == {
        "gh release create": {"release.yml"},
        "npm publish --access public": {"publish-npm.yml"},
        "docker buildx imagetools create": {"publish-docker.yml"},
    }

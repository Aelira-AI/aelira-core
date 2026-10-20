# Production Dockerfile for Aelira Backend
# Multi-stage build for smaller image size

# Build the Debian system library from its pinned source package and the
# reviewed CVE-2026-6653 backport. This stage is never a shipped layer.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS libxml2-deb-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential debhelper liblzma-dev pkg-config quilt rename zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY docker/libxml2-6653/ /tmp/libxml2-input/
RUN sh /tmp/libxml2-input/build-debs.sh

# Stage 1: Builder
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder

ARG SOURCE_DATE_EPOCH=0

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    patch \
    cmake \
    ninja-build \
    nasm \
    libpq-dev \
    libcairo2-dev \
    pkg-config \
    curl \
    ca-certificates \
    xz-utils \
    # Image libraries Pillow is compiled against. libtiff is deliberately not
    # here: Pillow links the hash-verified libtiff that its build script builds.
    libjpeg-dev \
    zlib1g-dev \
    libfreetype-dev \
    libraqm-dev \
    liblcms2-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    libavif-dev \
    libdav1d-dev=1.5.1-1 \
    libxcb1-dev \
    liblzma-dev \
    libzstd-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies. lxml is built first from
# its pinned source release against hash-verified libxml2 2.15.4 and libxslt
# 1.1.45; the published wheels bundle an older, affected libxml2. Pillow is
# built against hash-verified libtiff 4.7.2, psycopg2-binary is built against
# Debian's libpq, and PyAV/OpenCV share the FFmpeg 9.0.2 prefix. Their pinned
# requirements are then satisfied.
COPY requirements.txt .
COPY docker/build-lxml-wheel.sh /tmp/build-lxml-wheel.sh
COPY docker/build-pillow-wheel.sh /tmp/build-pillow-wheel.sh
COPY docker/build-psycopg2-wheel.sh /tmp/build-psycopg2-wheel.sh
COPY docker/build-pyav-wheel.sh /tmp/build-pyav-wheel.sh
COPY docker/build-opencv-wheel.sh /tmp/build-opencv-wheel.sh
COPY docker/opencv-ffmpeg9-pr29533.diff /tmp/opencv-ffmpeg9-pr29533.diff
RUN export SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" PYTHONHASHSEED=0; \
    pip install --no-cache-dir "pip==26.2.1" && \
    python -m pip uninstall --yes setuptools && \
    pip install --no-cache-dir "setuptools==84.0.0" && \
    sh /tmp/build-lxml-wheel.sh /tmp/lxml-wheel && \
    pip install --no-cache-dir --no-deps /tmp/lxml-wheel/lxml-6.1.3-*.whl && \
    rm -rf /tmp/lxml-wheel /tmp/build-lxml-wheel.sh && \
    pip install --no-cache-dir "pybind11==3.1.0" && \
    sh /tmp/build-pillow-wheel.sh /tmp/pillow-wheel && \
    pip install --no-cache-dir --no-deps /tmp/pillow-wheel/pillow-12.3.0-*.whl && \
    rm -rf /tmp/pillow-wheel /tmp/build-pillow-wheel.sh && \
    python -m pip uninstall --yes pybind11 && \
    sh /tmp/build-psycopg2-wheel.sh /tmp/psycopg2-wheel && \
    pip install --no-cache-dir --no-deps /tmp/psycopg2-wheel/psycopg2_binary-2.9.13-*.whl && \
    rm -rf /tmp/psycopg2-wheel /tmp/build-psycopg2-wheel.sh && \
    pip install --no-cache-dir "Cython==3.2.9" && \
    sh /tmp/build-pyav-wheel.sh /tmp/pyav-wheel && \
    pip install --no-cache-dir --no-deps /tmp/pyav-wheel/av-18.1.0-*.whl && \
    rm -rf /tmp/pyav-wheel /tmp/build-pyav-wheel.sh && \
    python -m pip uninstall --yes Cython && \
    sh /tmp/build-opencv-wheel.sh /tmp/opencv-wheel /tmp/opencv-ffmpeg9-pr29533.diff && \
    pip install --no-cache-dir --no-deps /tmp/opencv-wheel/opencv_python-5.0.0.93-*.whl && \
    rm -rf /tmp/opencv-wheel /tmp/build-opencv-wheel.sh /tmp/opencv-ffmpeg9-pr29533.diff && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "import lxml.etree as e; assert e.LIBXML_VERSION >= (2, 15, 4), e.LIBXML_VERSION; assert e.LIBXSLT_VERSION >= (1, 1, 45), e.LIBXSLT_VERSION" && \
    python -c "from PIL import features; v = features.version('libtiff'); assert v == '4.7.2', v" && \
    python -c "import av; assert av.__version__ == '18.1.0'; assert av.library_versions['libavcodec'] == (63, 1, 102)" && \
    python -c "import cv2, importlib.metadata as m; assert m.version('opencv-python') == '5.0.0.93'; assert cv2.__version__ == '5.0.0'" && \
    test ! -e "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/pillow.libs" && \
    python -m pip uninstall --yes pip

# The official image's bundled Expat modules must not occur in any shipped
# layer. Remove both before copying its otherwise unchanged /usr/local tree
# into the fresh Debian runtime.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS python-expat-clean
RUN rm /usr/local/lib/python3.14/lib-dynload/pyexpat.cpython-314-*.so \
       /usr/local/lib/python3.14/lib-dynload/_elementtree.cpython-314-*.so

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS python-expat-modules
ARG EXPAT_DEB_VERSION=2.9.0-1~deb13u1
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc make libc6-dev libexpat1=${EXPAT_DEB_VERSION} \
    libexpat1-dev=${EXPAT_DEB_VERSION} pkgconf xz-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY docker/build-python-expat-modules.sh /tmp/build-python-expat-modules.sh
RUN sh /tmp/build-python-expat-modules.sh /tmp/python-expat-modules

# Pa11y needs Node at runtime, but Debian's npm package pulls its full build
# toolchain into the final image. Resolve the reviewed, locked production tree
# separately and copy only Node and those dependencies into the API image.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS pa11y-node

ENV PUPPETEER_SKIP_DOWNLOAD=true
WORKDIR /opt/pa11y-runtime
COPY pa11y-runtime/package.json pa11y-runtime/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && \
    npm cache clean --force && \
    rm -rf /root/.npm

# A fresh Debian root excludes the old modules from every shipped layer,
# while the official /usr/local tree keeps the interpreter ABI.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS python-expat-base

ARG SOURCE_DATE_EPOCH=0
ARG EXPAT_DEB_VERSION=2.9.0-1~deb13u1
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHON_VERSION=3.14.8 \
    PYTHON_SHA256=c2215904f02b175596dc49351585104f4bc20341e1c47378b26a2c274360ce73

# Match the official slim interpreter's runtime library closure. Clean
# generated apt state in this layer so it cannot affect image reproducibility.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates netbase tzdata libbz2-1.0 libc6 libdb5.3t64 \
    libffi8 libgdbm6t64 liblzma5 libncursesw6 libreadline8t64 \
    libsqlite3-0 libssl3t64 libtinfo6 libuuid1 libzstd1 zlib1g \
    libexpat1=${EXPAT_DEB_VERSION} \
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* \
    && rm -f /var/log/dpkg.log /var/log/alternatives.log \
        /etc/machine-id /var/lib/dbus/machine-id /var/cache/ldconfig/aux-cache
COPY --from=python-expat-clean /usr/local/ /usr/local/
COPY --from=python-expat-modules /tmp/python-expat-modules/ /usr/local/lib/python3.14/lib-dynload/
RUN ldconfig && rm -f /var/cache/ldconfig/aux-cache

# Stage 2: Runtime
FROM python-expat-base AS runtime

# The global repair and the final-image checks read the reviewed runtime pin.
COPY --from=libxml2-deb-builder /tmp/libxml2-debs/ /tmp/libxml2-debs/
COPY requirements.txt /app/requirements.txt
COPY scripts/verify_final_python_packages.py /app/scripts/verify_final_python_packages.py
COPY scripts/verify_final_os_packages.py /app/scripts/verify_final_os_packages.py

# Install runtime dependencies + Playwright system dependencies + LaTeXML stack.
# TeX format dumps are content-nondeterministic even with a fixed epoch, so
# omit them; Kpathsea recreates only the requested format in the user's cache.
RUN export SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" FORCE_SOURCE_DATE=1 \
        PYTHONHASHSEED=0 PERL_HASH_SEED=0 PERL_PERTURB_KEYS=0; \
    apt-get update && \
    timeout 10m apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 upgrade -y && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    ffmpeg \
    libpq5 \
    curl \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    # LaTeX/MathML conversion stack (Higher Ed STEM support)
    # Full accessible PDF pipeline: LaTeXML + accessibility packages
    latexml \
    libxslt1.1 \
    # Shared libraries the source-built Pillow links at run time
    libjpeg62-turbo \
    libfreetype6 \
    libraqm0 \
    liblcms2-2 \
    libwebp7 \
    libwebpdemux2 \
    libwebpmux3 \
    libopenjp2-7 \
    libavif16 \
    libdav1d7=1.5.1-1 \
    libxcb1 \
    liblzma5 \
    libzstd1 \
    imagemagick \
    ghostscript \
    texlive-base \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-science \
    texlive-luatex \
    texlive-lang-german \
    pandoc \
    && update-language \
    # Replace the just-installed stock library before this RUN commits a
    # layer. No image layer therefore contains the vulnerable system libxml2.
    && apt-get install -y --no-install-recommends /tmp/libxml2-debs/libxml2_*.deb \
    && test "$(dpkg-query -W -f='${Version}' libxml2)" = '2.12.7+dfsg+really2.9.14-2.1+deb13u3+aelira1' \
    && sha256sum --check --strict /tmp/libxml2-debs/libxml2.sha256 \
    && mkdir -p /usr/share/doc/aelira-libxml2-6653 \
    && cp /tmp/libxml2-debs/receipt.txt /tmp/libxml2-debs/libxml2.sha256 /usr/share/doc/aelira-libxml2-6653/ \
    && rm -rf /tmp/libxml2-debs \
    # xdg-utils is a hard Depends of texlive-base, so apt cannot remove it
    # without removing TeX. Its only callers are interactive (texdoc, LuaTeX
    # os.openurl, ImageMagick's browse delegate) and none runs in a server, so
    # remove the package itself with dpkg. That leaves texlive-base with an
    # unmet dependency, which breaks apt in any image built FROM this one, so
    # install an empty local package that Provides xdg-utils. It ships no
    # files and is not named xdg-utils, so no scanner inventories xdg-utils.
    # No network is needed. scripts/verify_final_os_packages.py pins the
    # result and CI runs apt-get check against the final image.
    && dpkg --purge --force-depends xdg-utils \
    && mkdir -p -m 0755 /tmp/aelira-xdg-utils-absent/DEBIAN \
    && printf '%s\n' \
        'Package: aelira-xdg-utils-absent' \
        'Version: 1.0' \
        'Architecture: all' \
        'Maintainer: Aelira container image build' \
        'Provides: xdg-utils' \
        'Description: empty stand-in for the removed xdg-utils package' \
        ' Satisfies the dependency that texlive-base declares on xdg-utils' \
        ' without installing xdg-email or any other file.' \
        > /tmp/aelira-xdg-utils-absent/DEBIAN/control \
    && dpkg-deb --root-owner-group --build /tmp/aelira-xdg-utils-absent /tmp/aelira-xdg-utils-absent.deb \
    && dpkg --install /tmp/aelira-xdg-utils-absent.deb \
    && rm -rf /tmp/aelira-xdg-utils-absent /tmp/aelira-xdg-utils-absent.deb \
    && rm -rf /var/lib/apt/lists/* /var/cache/fontconfig/* /var/log/apt/* \
    && rm -f /etc/machine-id /var/lib/dbus/machine-id \
        /var/cache/ldconfig/aux-cache /var/lib/texmf/ls-R \
        /var/log/alternatives.log /var/log/dpkg.log \
    && find /var/lib/texmf -type f -name '*.log' -delete \
    && find /var/lib/texmf/web2c -type f -name '*.fmt' -delete \
    && /usr/local/bin/python -m pip uninstall --yes msgpack \
    && /usr/local/bin/python -m pip install --no-cache-dir "msgpack==$(/usr/local/bin/python /app/scripts/verify_final_python_packages.py msgpack-pin)" \
    && /usr/local/bin/python -m pip uninstall --yes pip

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/ffmpeg9/lib /opt/ffmpeg9/lib
COPY --from=builder /opt/ffmpeg9/licenses /opt/ffmpeg9/licenses
ENV PATH="/opt/venv/bin:$PATH"

# Copy the locked Pa11y runtime without Debian's npm toolchain.
COPY --from=pa11y-node /usr/local/bin/node /usr/local/bin/node
COPY --from=pa11y-node /opt/pa11y-runtime/node_modules /opt/pa11y-runtime/node_modules
RUN ln -s /opt/pa11y-runtime/node_modules/pa11y/bin/pa11y.js /usr/local/bin/pa11y

RUN /usr/local/bin/python /app/scripts/verify_final_python_packages.py global && \
    /opt/venv/bin/python /app/scripts/verify_final_python_packages.py venv && \
    /usr/local/bin/python /app/scripts/verify_final_os_packages.py

# Set working directory
WORKDIR /app

# Copy application code
COPY . .

# Ensure dashboard dist is included (build it before Docker build)
# Dashboard should be pre-built: cd dashboard && npm run build

# Download Piper voice model for TTS accessibility (as root, before user switch)
RUN python scripts/download_piper_voice.py

# Create non-root user for security
RUN useradd -m -u 1000 aelira && \
    mkdir -p /app/uploads && \
    chown -R aelira:aelira /app && \
    mkdir -p /home/aelira/.cache && \
    chown -R aelira:aelira /home/aelira
USER aelira

# Set HOME and Playwright environment variables
ENV HOME=/home/aelira
ENV PLAYWRIGHT_BROWSERS_PATH=/home/aelira/.cache/ms-playwright
ENV PA11Y_CONFIG_PATH=/app/config/pa11y.json
ENV PA11Y_CHROMIUM_PATH=/home/aelira/.local/bin/aelira-chromium

# Install Playwright Chromium browser (baked into image)
# This runs as 'aelira' user and installs to /home/aelira/.cache/ms-playwright
RUN playwright install chromium
RUN python scripts/configure_pa11y_chromium.py

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint runs alembic migrations then starts the configurable API workers.
# Long-running scans execute in the separate durable worker service.
ENTRYPOINT ["/app/entrypoint.sh"]

# Prove the browser runtime in a disposable stage. The shipped image starts
# from the clean pre-smoke runtime so Chromium state cannot affect its bytes.
FROM runtime AS pa11y-verified
RUN python scripts/smoke_pa11y_runtime.py && \
    printf 'verified\n' > /tmp/pa11y-runtime-verified

FROM runtime AS final
COPY --from=pa11y-verified --chown=aelira:aelira \
    /tmp/pa11y-runtime-verified /home/aelira/.local/share/aelira/pa11y-runtime-verified

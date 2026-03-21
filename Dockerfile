# Production Dockerfile for Aelira Backend
# Multi-stage build for smaller image size

# Stage 1: Builder
FROM python:3.12-slim AS builder

# Install system dependencies for building Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libpq-dev \
    libcairo2-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir piper-tts

# Stage 2: Runtime
FROM python:3.12-slim

# Install runtime dependencies + Playwright system dependencies + LaTeXML stack + Node.js for Pa11y
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    ffmpeg \
    libpq5 \
    curl \
    # Node.js for Pa11y accessibility testing
    nodejs \
    npm \
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
    libxml2-utils \
    libxslt1.1 \
    imagemagick \
    ghostscript \
    texlive-base \
    texlive-latex-base \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-science \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY . .

# Ensure dashboard dist is included (build it before Docker build)
# Dashboard should be pre-built: cd dashboard && npm run build

# Install Pa11y globally for multi-engine accessibility testing (as root)
# Pa11y can run both axe-core and HTML_CodeSniffer engines
RUN npm install -g pa11y

# Download Piper voice model for TTS accessibility (as root, before user switch)
RUN mkdir -p /app/data/piper-voices && \
    curl -L -o /app/data/piper-voices/en_US-lessac-medium.onnx \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true" && \
    curl -L -o /app/data/piper-voices/en_US-lessac-medium.onnx.json \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true"

# Create non-root user for security
RUN useradd -m -u 1000 aelira && \
    chown -R aelira:aelira /app && \
    mkdir -p /home/aelira/.cache && \
    chown -R aelira:aelira /home/aelira
USER aelira

# Set HOME and Playwright environment variables
ENV HOME=/home/aelira
ENV PLAYWRIGHT_BROWSERS_PATH=/home/aelira/.cache/ms-playwright

# Install Playwright Chromium browser (baked into image)
# This runs as 'aelira' user and installs to /home/aelira/.cache/ms-playwright
RUN playwright install chromium

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Entrypoint runs alembic migrations then starts uvicorn
# NOTE: Using 1 worker until we migrate to proper task queue (Celery/RQ)
# Multiple workers cause deadlocks with sync Playwright in BackgroundTasks
ENTRYPOINT ["/app/entrypoint.sh"]

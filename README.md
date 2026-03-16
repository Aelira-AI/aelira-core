# Aelira Core — Open-Source Accessibility Compliance Platform

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

AI-powered WCAG 2.1 AA compliance for higher education. Scans and auto-remediates PDFs, Word documents, PowerPoints, Excel spreadsheets, LaTeX equations, websites, video/audio, and source code. Built for the April 2026 ADA Title II deadline.

## Self-Hosting Quick Start

**Prerequisites:** Docker and Docker Compose, 4GB+ RAM

```bash
# Clone the repository
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core

# Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env with your settings (database, Redis, AI provider)

# Start all services
docker compose -f backend/docker-compose.dev.yml up -d --build

# Verify
curl http://localhost:8000/health
```

**Services available at:**
- API: http://localhost:8000
- Dashboard: http://localhost:5173
- PostgreSQL: localhost:5432
- Redis: localhost:6379

## Architecture

| Component | Technology |
|-----------|-----------|
| API | FastAPI (Python 3.11+) |
| Database | PostgreSQL 16 + SQLAlchemy 2.0 |
| Cache | Redis |
| AI | Ollama (local) or Gemini API (cloud) |
| Dashboard | React 19 + Vite + TypeScript |
| Auth | Magic links + OAuth + API keys + JWT |

## Document Processors (8/8)

| Processor | Features |
|-----------|----------|
| PDF | OCR (Tesseract), structure detection, PDF/UA compliance, auto-remediation |
| Word (DOCX) | Heading structure, alt text, lists, table headers |
| PowerPoint (PPTX) | Contrast checks, CVD simulation, slide structure |
| Excel (XLSX) | Table headers, chart descriptions, sheet naming |
| LaTeX | MathML conversion, physics/chemistry notation (ChemFig, mhchem, TikZ) |
| Website | Multi-page crawling, axe-core scanning, AI-generated code fixes |
| Multimedia | Video/audio transcription, WebVTT captions, scene detection |
| Code | Static HTML/CSS/JS analysis, ARIA attributes, semantic structure |

## Platform Integrations (6/6)

| Platform | Integration |
|----------|-------------|
| Google Workspace | OAuth 2.0 + Drive API |
| Microsoft 365 | OAuth 2.0 + Graph API |
| Canvas LMS | LTI 1.3 |
| Blackboard | LTI 1.3 |
| Moodle | OAuth 2.0 + REST API |
| D2L Brightspace | OAuth 2.0 + Valence API |

## Configuration

Key environment variables (see `backend/.env.example` for all options):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `GEMINI_API_KEY` | Google Gemini API key (for cloud AI) |
| `OLLAMA_URL` | Ollama server URL (for local AI, default: http://localhost:11434) |
| `JWT_SECRET` | Secret for JWT token signing |
| `SMTP_HOST` | SMTP server for magic link emails |

## Development

```bash
# Start dev environment
docker compose -f backend/docker-compose.dev.yml up -d --build

# Run backend tests
docker exec aelira-api-dev pytest

# Run specific test
docker exec aelira-api-dev pytest tests/test_web_scanner_e2e.py -v

# Dashboard development (from dashboard directory)
cd backend/dashboard && npm install && npm run dev
```

### Project Structure

```
backend/
├── src/
│   ├── api/              # REST endpoints (47 education endpoints)
│   │   └── education/    # Document scanning, remediation, compliance
│   ├── education/        # Document processors + PDF checker classes
│   ├── ai/               # LLM provider abstraction (Gemini, Ollama)
│   ├── auth/             # Magic links, OAuth, JWT, API keys
│   ├── db/               # SQLAlchemy models, Alembic migrations
│   ├── integrations/     # LMS and cloud platform connectors
│   └── middleware/       # Quota, security, CORS
├── dashboard/            # React 19 + Vite admin interface
├── tests/                # pytest + Playwright test suite
└── docker-compose.yml    # Production deployment
```

## CLI

For command-line accessibility testing, see **[aelira-cli](https://github.com/Aelira-AI/aelira-cli)** — a standalone CLI tool with 31 commands, file watching, CSV export, and CI/CD integration. MIT licensed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

AGPL-3.0 — see [LICENSE](LICENSE) for details.

## Links

- **Website:** [aelira.ai](https://aelira.ai)
- **CLI Tool:** [Aelira-AI/aelira-cli](https://github.com/Aelira-AI/aelira-cli)
- **Documentation:** [docs.aelira.ai](https://docs.aelira.ai)

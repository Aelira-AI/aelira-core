# Aelira Core

Open-source accessibility compliance platform with AI-powered document remediation.

Aelira scans documents for WCAG 2.1 AA violations and automatically generates fixes — not just reports, but working remediated files. Built for higher education institutions facing accessibility compliance deadlines.

## Features

**Document Scanning**
- PDF (with OCR for scanned documents)
- Microsoft Word, PowerPoint, Excel
- LaTeX and MathML
- HTML and websites (Playwright + axe-core)
- Images (AI alt text generation)
- Video/audio (transcription and captioning)

**AI Remediation**
- Automatic fix generation for detected issues
- Multi-provider AI: Gemini, Ollama (self-hosted)
- PDF structure tree tagging (PDF/UA compliance)
- Table accessibility detection and repair
- Reading order analysis and correction
- Color contrast fixes

**Platform Integrations**
- Canvas LTI 1.3 (embedded course navigation, deep linking, grade passback)
- Blackboard, Moodle, Brightspace
- Google Workspace (Drive, Docs, Slides, Sheets)
- Microsoft 365 (OneDrive, SharePoint)

**Dashboard**
- React + Vite admin UI
- Scan results with issue-by-issue breakdown
- Remediation review workflow
- Compliance trending and analytics
- Multi-tenant department management

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core
cp .env.example .env
# Edit .env with your database and SMTP settings
docker compose -f docker-compose.dev.yml up -d
```

The API will be available at `http://localhost:8000` and the dashboard at `http://localhost:5173`.

### Local Development

```bash
# Backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --port 8000

# Dashboard
cd dashboard
npm install
npm run dev
```

### Database Setup

```bash
# Run migrations
alembic upgrade head
```

## Architecture

```
aelira-core/
├── src/
│   ├── api/              # FastAPI route handlers (27 routers)
│   ├── auth/             # Authentication (magic links, OAuth, JWT)
│   ├── config/           # Settings and configuration
│   ├── db/               # SQLAlchemy models, database session
│   ├── education/        # Document processors and remediators
│   │   ├── pdf_scanner.py
│   │   ├── pdf_remediator.py
│   │   ├── docx_processor.py
│   │   ├── pptx_processor.py
│   │   ├── xlsx_processor.py
│   │   ├── latex_processor.py
│   │   ├── website_scanner.py
│   │   └── ...
│   ├── integrations/     # LMS and cloud storage integrations
│   ├── ai/               # AI provider abstraction (Gemini, Ollama)
│   ├── mailer/           # Transactional email service
│   ├── middleware/        # Rate limiting, quotas, CORS
│   └── jobs/             # Background job processing
├── dashboard/            # React + Vite admin UI
├── tests/                # Backend test suite
├── alembic/              # Database migrations
└── docker-compose.dev.yml
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI, Python 3.11+, SQLAlchemy 2.0 |
| Database | PostgreSQL 16 |
| Cache | Redis |
| Dashboard | React 19, Vite 8, TypeScript, Tailwind CSS |
| AI | Gemini 2.0 Flash, Ollama (Qwen, Gemma) |
| PDF | pikepdf, PyMuPDF, pdfplumber, OCRmyPDF |
| Office | python-docx, python-pptx, openpyxl |
| Web | Playwright, axe-core |
| Video | faster-whisper, PySceneDetect, piper-tts |

## Configuration

Copy `.env.example` to `.env` and configure:

- **Database:** `DATABASE_URL` (PostgreSQL connection string)
- **Redis:** `REDIS_URL` (for caching and rate limiting)
- **SMTP:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` (for magic link emails)
- **JWT:** `JWT_SECRET` (for authentication tokens)
- **AI:** `GEMINI_API_KEY` or Ollama endpoint (for AI remediation)
- **OAuth:** Google/Microsoft client credentials (for cloud integrations)
- **LTI:** Canvas/Blackboard credentials (for LMS integration)

See `.env.example` for the full list with descriptions.

## API Documentation

Once running, interactive API docs are available at:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## Testing

```bash
# Backend tests
pytest tests/ -v

# Dashboard tests
cd dashboard
npm run test:unit    # Unit tests
npm run lint         # ESLint
npx tsc --noEmit     # Type check
npm run build        # Build verification
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and pull request guidelines.

## Security

See [SECURITY.md](SECURITY.md) for our vulnerability reporting policy.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

## Links

- [Website](https://aelira.ai)
- [CLI Tool](https://github.com/Aelira-AI/aelira-cli) (MIT licensed)
- [Issue Tracker](https://github.com/Aelira-AI/aelira-core/issues)

# Contributing to Aelira Core

Thank you for your interest in contributing to Aelira! We're building an open-source accessibility compliance platform to help organizations meet WCAG 2.1 standards, and we welcome contributions from the community.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to conduct@aelira.ai.

## Getting Started

### Prerequisites

- **Python 3.14** — Backend API (`.python-version`, `Dockerfile`, `Dockerfile.dev`, and CI all pin this)
- **Node.js 20+** — Dashboard frontend (`dashboard/Dockerfile`, CI)
- **Docker** — For running services locally
- **PostgreSQL 16** — Database (or use Docker)
- **Redis** — Cache and session store (or use Docker)

### Development Setup

```bash
# Clone the repo
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core

# Copy environment file
cp .env.example .env
# Edit .env with your configuration

# Option 1: Docker (recommended)
docker compose -f docker-compose.dev.yml up -d

# Option 2: Local Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Running the API

```bash
# With Docker
docker compose -f docker-compose.dev.yml up -d

# Without Docker
source venv/bin/activate
uvicorn src.api.main:app --reload --port 8000
```

### Running the Dashboard

```bash
cd dashboard
npm install
npm run dev
```

## Project Structure

```
aelira-core/
├── src/                     # FastAPI backend
│   ├── api/                 # API route handlers
│   ├── auth/                # Authentication (magic links, OAuth, JWT)
│   ├── config/              # Settings and configuration
│   ├── db/                  # Database models and session handling
│   ├── education/           # Document processors and remediators
│   ├── integrations/        # LMS integrations (Canvas, Blackboard, etc.)
│   ├── ai/                  # AI provider abstraction
│   ├── mailer/              # Email service
│   └── middleware/          # Rate limiting, quotas, CORS
├── dashboard/               # React + Vite admin UI
│   └── src/                 # TypeScript source
├── tests/                   # Backend test suite
├── alembic/                 # Database migrations
├── Dockerfile               # Production container
└── docker-compose.dev.yml   # Local development stack
```

## Making Contributions

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make your changes
4. Run tests to ensure nothing is broken
5. Commit your changes (see commit message format below)
6. Push to your fork and submit a Pull Request

## Code Style

### Python

- Type hints on all function signatures
- Pydantic models for API request/response schemas
- Structured logging with contextual fields
- Follow existing patterns in the codebase
- Lint with `ruff check .` and format-check with `black --check src/ tests/ scripts/` — this is what CI runs (`.github/workflows/ci.yml`); `black` is pinned to `26.1.0`

### TypeScript (Dashboard)

- Strict mode enabled
- Explicit types (avoid `any`)
- Error boundaries on page components
- Loading states on async operations

## Testing

### Backend Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_severity_determinism.py -v

# Run with coverage
pytest --cov=src
```

### Dashboard Tests

```bash
cd dashboard
npm run test:unit    # Unit tests
npm run test         # Playwright e2e tests
```

## Commit Messages

We follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
type(scope): description

# Examples:
feat(pdf): add table structure detection
fix(scanner): handle empty alt text correctly
docs(readme): update development setup
test(auth): add magic link expiry tests
refactor(api): simplify quota middleware
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`

## Pull Request Process

1. Update documentation if you changed public APIs
2. Add tests for new functionality
3. Ensure all tests pass
4. Keep PRs focused — one feature or fix per PR
5. Write a clear description of what changed and why

## Getting Help

- **Bug reports:** Open a [GitHub Issue](https://github.com/Aelira-AI/aelira-core/issues/new)
- **Feature requests:** Open a [GitHub Issue](https://github.com/Aelira-AI/aelira-core/issues/new)
- **General questions:** Open a [GitHub Discussion](https://github.com/Aelira-AI/aelira-core/discussions)

Look for issues labeled [`good first issue`](https://github.com/Aelira-AI/aelira-core/labels/good%20first%20issue) — these are great for new contributors!

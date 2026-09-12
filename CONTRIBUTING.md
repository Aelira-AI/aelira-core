# Contributing to Aelira Core

Thank you for your interest in contributing to Aelira! We're building an open-source accessibility compliance platform to help organizations meet WCAG 2.1 standards, and we welcome contributions from the community.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to conduct@aelira.ai.

## Getting Started

### Prerequisites

- **Python 3.14** — Backend API (`.python-version`, `Dockerfile`, `Dockerfile.dev`, and CI all pin this)
- **Node.js 22+** — Dashboard frontend (`dashboard/Dockerfile`, CI)
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
pip install -r requirements-dev.txt
```

### Running the API

```bash
# With Docker
docker compose -f docker-compose.dev.yml up -d

# Without Docker
source venv/bin/activate
uvicorn src.api.main:app --reload --port 8000 --no-proxy-headers
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

Open or agree on a GitHub issue before starting implementation. Record the problem,
scope, and testable acceptance criteria, and apply at least two relevant labels
(ask a maintainer if you cannot apply labels). Link the issue from the pull request.

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
- Use structured logging with event and outcome fields. Authentication paths must follow the privacy contract in `SECURITY.md`: never log direct identifiers, credentials, tokens, headers, cookies, or unfiltered exception text; use opaque record or request identifiers when correlation is necessary.
- Follow existing patterns in the codebase
- Lint with `ruff check .` and format-check with `black --check src/ tests/ scripts/` — this is what CI runs (`.github/workflows/ci.yml`); `black` is pinned to `26.3.1`

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

### Issue traceability

Every ordinary human-authored pull request must include at least one standalone
closing declaration in its description, such as `Closes #123`. Use one declaration
per line, with a blank line before and after the declaration paragraph.
`Fixes` and `Resolves` are also accepted, as are full issue URLs or
`Aelira-AI/aelira-core#123` references. The linked items must be real issues in this
repository, not pull requests, and each must have at least two distinct relevant
labels. References in comments, code blocks, blockquotes, or example prose do not
count. Use plain Markdown in the description: raw HTML outside comments or fenced
code blocks causes the check to fail closed. Put HTML examples in fenced code
blocks and use Markdown image syntax for screenshots. Mixed-prose paragraphs
cannot declare issues.
The check verifies label count; maintainers review label relevance and acceptance
criteria.

Do not create an issue after the fact and present it as the original report.
If tracking was missed, clearly mark the issue as a retrospective backfill,
record the affected release and actual evidence, and link the implementing PR.
Close it only when its acceptance criteria have been verified. Keep unfinished
verification and follow-up defects open.

The only automated exception is a PR authored by GitHub's authenticated
`dependabot[bot]` account with type `Bot`, changing only existing root
`requirements.txt` / `requirements-dev.txt` or the dashboard/CLI `package.json`
and `package-lock.json` files. Added, removed, or renamed files are not exempt.
Action workflow updates and all other bots follow the normal issue policy.
Titles and labels cannot grant an exception.

The metadata-only `Issue traceability` workflow reads trusted `main` code and
current GitHub API metadata, never PR-head code. It publishes its status against
the live PR head, not the base SHA. PRs sharing a head SHA are checked together;
that SHA passes only when all of them meet the policy. API or response uncertainty cannot produce a
successful check. A preflight marks known heads pending before running trusted
policy tests; event/list heads can invalidate a status but cannot grant success.
PR changes, issue label/edit/state changes, and repository label edits/deletions
trigger serialized rechecks;
maintainers can use **Run workflow** to recheck all open PRs.

Installing the workflow does not itself make it a merge requirement. After it is
merged and a status has been observed, repository administrators must require
the exact `Issue traceability` status from GitHub Actions in the `main` ruleset.
Activation and a blocked-merge test are separate verification steps. The first
policy PR needs manual issue review because its trusted base predates the check.

GitHub metadata and commit statuses are not transactional. Event delivery,
workflow startup failures, and edits after the final read can leave a previously
successful status stale until a recheck completes. A listing outage can prevent
discovering all affected PR heads, and a status-write outage prevents invalidation.
Do not merge while a metadata
recheck is pending or failed; rerun it after changing linked issues. Rechecks are
bounded to 1,000 open PRs and 3,000 dependency files and fail if these limits are
exceeded. This check enforces traceability, not correctness of the implementation.

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

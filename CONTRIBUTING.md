# Contributing to Aelira Core

Thank you for your interest in contributing to Aelira! This guide will help you get started.

## Reporting Bugs

Open a [GitHub Issue](https://github.com/Aelira-AI/aelira-core/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Docker version, Python version)

## Suggesting Features

Open a [GitHub Issue](https://github.com/Aelira-AI/aelira-core/issues) with the `feature` label. Describe the use case and how it would help accessibility compliance.

## Development Setup

```bash
# Clone and start dev environment
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core
cp backend/.env.example backend/.env
docker compose -f backend/docker-compose.dev.yml up -d --build

# Run tests
docker exec aelira-api-dev pytest
```

## Code Style

- **Python:** [Ruff](https://docs.astral.sh/ruff/) for linting, [Black](https://black.readthedocs.io/) for formatting
- **TypeScript (Dashboard):** ESLint
- **Commits:** `type(scope): description` (e.g. `feat(pdf): add OCR support`)

```bash
# Check formatting
cd backend && python3 -m ruff check src/ && python3 -m black --check src/
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch from `main`
3. Write tests for new functionality
4. Ensure all tests pass
5. Submit a PR with a clear description

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).

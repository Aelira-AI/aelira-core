# Self-Hosting Aelira

This guide covers deploying Aelira on your own infrastructure.

## Prerequisites

- Docker and Docker Compose
- 4GB+ RAM (8GB recommended for AI processing)
- PostgreSQL 16 (included in Docker Compose)
- Redis (included in Docker Compose)
- Domain name with SSL (for production)

## Quick Start

```bash
git clone https://github.com/Aelira-AI/aelira-core.git
cd aelira-core

# Configure
cp backend/.env.example backend/.env
# Edit backend/.env with your settings

# Start
docker compose -f backend/docker-compose.dev.yml up -d --build

# Verify
curl http://localhost:8000/health
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `REDIS_URL` | Yes | — | Redis connection string |
| `JWT_SECRET` | Yes | — | Secret for JWT signing (generate a random 64-char string) |
| `GEMINI_API_KEY` | No | — | Google Gemini API key (cloud AI provider) |
| `OLLAMA_URL` | No | `http://localhost:11434` | Ollama server URL (local AI) |
| `LLM_PROVIDER` | No | `gemini` | Primary AI provider (`gemini` or `ollama`) |
| `SMTP_HOST` | No | — | SMTP server for magic link emails |
| `SMTP_USER` | No | — | SMTP username |
| `SMTP_PASSWORD` | No | — | SMTP password |
| `CORS_ORIGINS` | No | `http://localhost:5173` | Allowed CORS origins (comma-separated) |

## AI Setup

### Option 1: Ollama (Local, Free)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull recommended models
ollama pull llama3.2
ollama pull moondream    # For image analysis

# Set in .env
LLM_PROVIDER=ollama
OLLAMA_URL=http://localhost:11434
```

### Option 2: Google Gemini (Cloud)

```bash
# Get API key from https://aistudio.google.com/apikey
# Set in .env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-api-key
```

## Reverse Proxy (Production)

Example Traefik configuration:

```yaml
# In your Traefik dynamic config
http:
  routers:
    aelira-api:
      rule: "Host(`api.yourdomain.com`)"
      service: aelira-api
      tls:
        certResolver: letsencrypt
  services:
    aelira-api:
      loadBalancer:
        servers:
          - url: "http://localhost:8000"
```

## Database Migrations

Migrations run automatically on startup. To run manually:

```bash
docker exec aelira-api-dev alembic upgrade head
```

## Upgrading

```bash
cd aelira-core
git pull origin main
docker compose -f backend/docker-compose.dev.yml up -d --build
```

## Troubleshooting

### API won't start
- Check `docker logs aelira-api-dev`
- Verify `DATABASE_URL` and `REDIS_URL` are correct
- Ensure PostgreSQL and Redis containers are healthy

### AI not working
- For Ollama: verify `ollama list` shows pulled models
- For Gemini: verify API key is valid
- Check `LLM_PROVIDER` in `.env`

### Dashboard can't connect to API
- Verify CORS_ORIGINS includes the dashboard URL
- Check the API is running: `curl http://localhost:8000/health`

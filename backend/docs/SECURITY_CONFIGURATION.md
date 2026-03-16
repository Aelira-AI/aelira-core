# Backend Security & Configuration Guide

**Last Updated:** October 31, 2025  
**Version:** v0.13.1

---

## Overview

This document describes the security features, configuration management, and testing infrastructure implemented in v0.13.1.

---

## Security Features

### 1. CORS Configuration

**Production Mode:**
- CORS restricted to specific domains:
  - `https://aelira.ai` (main website)
  - `https://dashboard.aelira.ai` (dashboard)
- Configured via `src/config/settings.py`
- Environment variable: `ENV=production`

**Development Mode:**
- CORS allows all origins for easier testing
- Configured via `ENV=development`

**Implementation:**
- Centralized in `src/config/settings.py`
- Environment-based configuration
- Applied in `src/api/main.py` middleware

### 2. API Key Authentication

**Production Mode:**
- All endpoints require valid API key (except `/health`)
- API keys validated via `get_api_key_or_mock()` dependency
- Keys stored hashed with bcrypt (never plaintext)
- Rate limiting applied per API key

**Development Mode:**
- Mock credentials allowed for testing
- Enables rapid development without API key management

**Protected Endpoints:**
All 13 education endpoints require API keys:
- PDF scan
- PowerPoint scan
- LaTeX convert
- Image alt text (single & batch)
- Multimedia transcribe
- Web scan
- Code scan
- Scan history
- Scan details
- Scan HTML retrieval
- Department stats

### 3. Redis-Based Rate Limiting

**Features:**
- Scalable rate limiting using Redis
- Graceful fallback to in-memory storage if Redis unavailable
- Per-API-key rate limits (default: 100 requests/hour)
- Configurable per key via `rate_limit_per_hour` field

**Rate Limit Headers:**
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

**Implementation:**
- Module: `src/auth/redis_rate_limiter.py`
- Automatic Redis connection management
- Error handling with fallback to memory
- Hour-based sliding window

### 4. File Size Validation

**File Size Limits:**
- PDF: 50MB
- PowerPoint: 50MB
- Image: 10MB
- Video: 500MB
- Code: 10MB

**Purpose:**
- Prevents DoS attacks via large file uploads
- Protects server resources
- Configurable via `src/config/settings.py`

**Implementation:**
- Validation occurs before file processing
- Returns HTTP 413 (Payload Too Large) for oversized files
- Clear error messages to users

---

## Configuration Management

### Settings Module

**Location:** `src/config/settings.py`

**Features:**
- Centralized configuration using Pydantic Settings
- Environment variable support
- `.env` file loading
- Type-safe configuration
- Production vs development defaults

**Key Settings:**
```python
# Environment
ENV: str = "development"  # or "production"
DEBUG: bool = False

# CORS
cors_origins: List[str] = [
    "https://aelira.ai",
    "https://dashboard.aelira.ai",
    "http://localhost:3000",  # Development
]

# Redis
redis_url: str = "redis://localhost:6379/0"
redis_enabled: bool = True

# Ollama
ollama_host: str = "http://localhost:11434"

# File Limits
max_file_size_pdf: int = 50 * 1024 * 1024  # 50MB
max_file_size_image: int = 10 * 1024 * 1024  # 10MB
max_file_size_video: int = 500 * 1024 * 1024  # 500MB

# Rate Limiting
default_rate_limit_per_hour: int = 100
```

### Environment Variables

**Required for Production:**
```bash
# Environment
ENV=production
DEBUG=false

# Database
DATABASE_URL=postgresql://user:password@host:5432/database

# Redis
REDIS_URL=redis://redis:6379/0
REDIS_ENABLED=true

# Ollama
OLLAMA_HOST=http://ollama:11434

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
```

---

## Testing Infrastructure

### Integration Tests

**Location:** `tests/test_api_integration.py`

**Test Coverage:**
- ✅ Health endpoint tests
- ✅ Authentication tests (valid/invalid API keys)
- ✅ Rate limiting tests (headers and enforcement)
- ✅ File size validation tests
- ✅ CORS header tests
- ✅ Error handling tests
- ✅ Database integration tests (with graceful skipping)

**Test Classes:**
- `TestHealthEndpoint` - Public health checks
- `TestAuthentication` - API key validation
- `TestRateLimiting` - Rate limit headers and enforcement
- `TestFileSizeValidation` - File size limits
- `TestCORSHeaders` - CORS configuration
- `TestScanHistory` - Database operations
- `TestDepartmentStats` - Statistics endpoints
- `TestErrorHandling` - Error responses

### Running Tests

```bash
# Run all integration tests
pytest tests/test_api_integration.py -v

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test class
pytest tests/test_api_integration.py::TestHealthEndpoint -v

# Run in Docker container
docker exec aelira-api-dev python -m pytest tests/test_api_integration.py -v
```

### Test Configuration

**Files:**
- `pytest.ini` - Pytest settings and markers
- `tests/conftest.py` - Shared fixtures and test environment

**Markers:**
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slow-running tests
- `@pytest.mark.requires_api` - Tests requiring external APIs
- `@pytest.mark.requires_db` - Tests requiring database

---

## Deployment Considerations

### Production Checklist

- [ ] Set `ENV=production` in environment variables
- [ ] Configure `REDIS_URL` to production Redis instance
- [ ] Set `DATABASE_URL` to production database
- [ ] Configure CORS origins for production domains
- [ ] Set secure `SECRET_KEY` for API key generation
- [ ] Enable Redis persistence (AOF or RDB)
- [ ] Configure Redis max memory and eviction policy
- [ ] Set up monitoring for Redis connection health
- [ ] Configure file size limits based on infrastructure capacity
- [ ] Set appropriate rate limits per tier

### Development Setup

**Quick Start:**
```bash
# Set environment variables
export ENV=development
export DEBUG=true
export REDIS_ENABLED=false  # Use in-memory fallback

# Run tests
pytest tests/test_api_integration.py -v
```

**Docker Compose:**
The `docker-compose.dev.yml` file includes:
- Redis service (for rate limiting)
- PostgreSQL service (for database)
- Ollama service (for AI models)

---

## Security Best Practices

1. **Never commit API keys or secrets**
   - Use `.env` files (gitignored)
   - Use environment variables in production
   - Rotate keys regularly

2. **Monitor rate limiting**
   - Track rate limit violations
   - Alert on suspicious patterns
   - Adjust limits per customer tier

3. **Validate all inputs**
   - File size limits enforced
   - File type validation
   - URL validation for web scanner

4. **Secure Redis**
   - Use password authentication in production
   - Bind to localhost or private network
   - Enable TLS if available

5. **Monitor authentication failures**
   - Log invalid API key attempts
   - Alert on brute force attempts
   - Implement account lockout after failures

---

## Troubleshooting

### Redis Connection Issues

**Symptoms:**
- Rate limiting falls back to in-memory
- Logs show "Redis connection failed"

**Solutions:**
1. Check Redis is running: `docker ps | grep redis`
2. Verify Redis URL: `echo $REDIS_URL`
3. Test connection: `docker exec aelira-api-dev python -c "from src.auth.redis_rate_limiter import get_redis_client; print(get_redis_client())"`
4. Check Redis logs: `docker logs aelira-redis-dev`

### API Key Authentication Failures

**Symptoms:**
- Endpoints return 401 Unauthorized
- "Invalid API key" errors

**Solutions:**
1. Verify API key format: `Bearer {key}` in Authorization header
2. Check API key exists in database
3. Verify key hasn't expired (`expires_at` field)
4. Check key is active (`is_active=true`)
5. Verify key hash matches stored hash

### File Size Validation Errors

**Symptoms:**
- Endpoints return 413 Payload Too Large
- "File size exceeds limit" errors

**Solutions:**
1. Check file size against limits in `src/config/settings.py`
2. Verify file size calculation (may be different from `Content-Length` header)
3. Adjust limits if needed (balance security vs usability)

---

## References

- **Security Review:** `BACKEND_REVIEW.md` (Section 5: Security Review)
- **Implementation Status:** `IMMEDIATE_ACTIONS_STATUS.md`
- **Rate Limiting:** `src/auth/redis_rate_limiter.py`
- **Configuration:** `src/config/settings.py`
- **Tests:** `tests/test_api_integration.py`

---

*Last Updated: October 31, 2025*


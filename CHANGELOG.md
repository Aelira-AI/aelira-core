# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.1] - 2026-08-16

### Security

- Bearer token hardening: access tokens are type-gated and checked against live sessions, so refresh tokens can no longer be replayed as access credentials and revoked tokens stop working at logout
- CSRF enforcement on cookie-authenticated dashboard mutations via a double-submit `X-CSRF-Token`; the dashboard's blanket exemptions were removed
- OAuth callback CSRF protection: Google/Microsoft connect and callback now use server-side one-time state, and the workspace binding comes only from verified state metadata
- `/google/connect` now requires authentication (development fallback removed)
- OAuth login domain matching uses exact domain equality (a substring match could be bypassed by look-alike domains)
- Sitemap XML parsing switched to `defusedxml` (XXE)

### Changed

- Faculty gamification and leaderboards are now opt-in and off by default
- LMS integration maturity labeled honestly: Canvas is production-verified; others range from beta to untested
- Documentation claims accuracy pass; SECURITY.md supported-versions table aligned; dashboard licence declared
- Upload paths genericized and dashboard debug logging removed

### Dependencies

- bcrypt 5.0.0, redis 8.1.0, websockets 17.0.1, python-pptx 1.0.2, av 18.1.0, packaging 26.3, setuptools 84.0.0, click 8.3.3
- Dashboard and CLI npm minor/patch groups; docker build actions updated

## [0.9.0] - 2026-08-15

### Added

- Initial public release
- FastAPI backend with document scanning and AI-powered remediation
- React + Vite admin dashboard
- Document processors: PDF, DOCX, PPTX, XLSX, LaTeX, HTML, images, video
- AI remediation with bring-your-own-model support: Gemini, OpenAI, Anthropic, xAI, any OpenAI-compatible endpoint, or fully local via Ollama
- WCAG 2.1 AA compliance scanning and reporting
- Authentication: magic links, Google OAuth, Microsoft OAuth
- Multi-tenant department system with tier-based quotas
- LMS integrations: Canvas LTI 1.3, Blackboard, Moodle, Brightspace
- Cloud storage integrations: Google Workspace, Microsoft 365
- PDF/UA compliance tagging and structure tree manipulation
- Reading order analysis and correction
- Table accessibility detection and remediation
- Color contrast analysis
- OCR for scanned documents
- Video captioning and audio description
- GDPR-compliant account management and data deletion
- Database schema management with Alembic migrations
- Docker development environment

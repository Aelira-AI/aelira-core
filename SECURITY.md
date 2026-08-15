# Security Policy

## Supported Versions

We release patches for security vulnerabilities in the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.9.x   | :white_check_mark: |

As a pre-1.0 project we support the current 0.9.x line with security updates; older 0.x releases are not maintained.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report security vulnerabilities by emailing:

**security@aelira.ai**

You should receive a response within 48 hours. If you don't hear back, please follow up to ensure we received your original message.

### What to Include

Please include the following information in your report:

- **Type of vulnerability** (e.g., SQL injection, XSS, authentication bypass)
- **Location** - Full paths of source file(s) related to the issue
- **Configuration** - Any special configuration required to reproduce
- **Steps to reproduce** - Step-by-step instructions
- **Proof of concept** - Code or screenshots if possible
- **Impact** - How an attacker could exploit this vulnerability
- **Suggested fix** (optional) - If you have ideas on how to fix it

### What to Expect

1. **Acknowledgment** - We'll acknowledge receipt within 48 hours
2. **Assessment** - We'll assess the severity and impact within 7 days
3. **Updates** - We'll keep you informed of our progress
4. **Resolution** - We aim to resolve critical issues within 30 days
5. **Credit** - We'll credit you in our release notes (unless you prefer anonymity)

## Disclosure Timeline

- **Critical vulnerabilities:** Patched within 7 days, disclosure after 30 days
- **High severity:** Patched within 14 days, disclosure after 60 days
- **Medium/Low severity:** Patched within 30 days, disclosure after 90 days

We follow responsible disclosure practices. Please do not publicly disclose the vulnerability until we've had a chance to address it.

## Security Measures

### What We Do

- **Dependency scanning** - Automated via Dependabot
- **Release safety gate** - Every release is scanned for secrets, credentials, and internal identifiers before publication (`scripts/verify_release_safety.py`)
- **Input validation** - Pydantic models validate all API inputs
- **SQL injection prevention** - SQLAlchemy ORM; no user input is interpolated into SQL
- **Authentication** - API keys with bcrypt hashing
- **Rate limiting** - Redis-based per-key limits

### Self-Hosted Security

If you self-host Aelira, please ensure:

1. **Use HTTPS** - Never expose the API over plain HTTP
2. **Firewall rules** - Restrict access to necessary ports only
3. **Database security** - Use strong passwords, restrict network access
4. **Keep updated** - Apply security patches promptly
5. **Monitor logs** - Watch for unusual activity

### Data Handling

- **AI data flows are your choice** - With the Ollama provider, all AI inference runs locally and documents never leave your infrastructure. With a cloud provider (Gemini is the default), document content is sent to that provider's API - review their data terms before enabling it on sensitive content
- **No user tracking** - We don't collect analytics on self-hosted instances
- **Minimal data storage** - Scan results are stored only for your access
- **No credential storage** - We never store your passwords (bcrypt hashes only)

## Known Security Considerations

### Document Processing

- **PDF processing** - Uses Tesseract OCR; ensure PDFs are from trusted sources
- **PowerPoint processing** - Uses python-pptx; macro execution is disabled
- **File uploads** - Size limits enforced; validate file types before processing

### AI Models

- **Local execution available** - AI inference can run fully locally via the Ollama provider; cloud providers (Gemini) are opt-in per deployment
- **Model integrity** - Download models only from official Ollama sources
- **Prompt injection** - Input sanitization applied to AI prompts

## Bug Bounty

We don't currently have a formal bug bounty program, but we deeply appreciate security researchers who help us improve, and we will credit you publicly (with your permission) in the release notes and the attribution list below.

## Contact

- **Security issues:** security@aelira.ai
- **General questions:** hello@aelira.ai
- **Code of Conduct:** conduct@aelira.ai

## Attribution

Thank you to all security researchers who have helped improve Aelira's security:

*No vulnerabilities have been reported yet.*

---

Last updated: August 2026

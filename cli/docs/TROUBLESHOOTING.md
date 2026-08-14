# Aelira CLI Troubleshooting Guide

**Version:** v0.4.0
**Last Updated:** March 17, 2026

Common issues and their solutions.

---

## Table of Contents

- [Installation Issues](#installation-issues)
- [Connection Issues](#connection-issues)
- [Authentication Issues](#authentication-issues)
- [Scanning Issues](#scanning-issues)
- [Export Issues](#export-issues)
- [File Watcher Issues](#file-watcher-issues)
- [Progress and Retry](#progress-and-retry)
- [Performance Issues](#performance-issues)
- [Error Messages](#error-messages)
- [Platform-Specific Issues](#platform-specific-issues)
- [CI Environments](#ci-environments)

---

## Installation Issues

### npm install fails

**Problem:** `npm install -g @aelira/cli` fails with permission errors

**Solution:**
```bash
# Option 1: Use sudo (not recommended)
sudo npm install -g @aelira/cli

# Option 2: Fix npm permissions (recommended)
mkdir ~/.npm-global
npm config set prefix '~/.npm-global'
export PATH=~/.npm-global/bin:$PATH
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc

# Then install without sudo
npm install -g @aelira/cli
```

---

### Command not found after installation

**Problem:** `aelira: command not found`

**Solution:**
```bash
# Check if CLI is installed
npm list -g @aelira/cli

# Check PATH
echo $PATH

# Add npm bin to PATH
export PATH=$(npm bin -g):$PATH

# Make permanent
echo 'export PATH=$(npm bin -g):$PATH' >> ~/.bashrc
source ~/.bashrc
```

---

## Connection Issues

### Cannot connect to backend

**Problem:** `Error: connect ECONNREFUSED 127.0.0.1:8000`

**Symptoms:**
```
❌ Error: Cannot connect to backend API at http://localhost:8000
```

**Solutions:**

**1. Check if backend is running:**
```bash
# Check Docker containers
docker ps | grep aelira

# If not running, start backend
cd backend
docker-compose -f docker-compose.dev.yml up -d
```

**2. Verify backend health:**
```bash
curl http://localhost:8000/api/scan/health

# Expected output:
# {"status":"healthy","version":"v0.18.0"}
```

**3. Use custom API URL:**
```bash
aelira scan https://example.com --api-url http://localhost:8000

# Or set in config
echo '{"apiUrl":"http://localhost:8000"}' > ~/.aelirarc.json
```

**4. Skip AI analysis (local only):**
```bash
aelira scan https://example.com --local
```

---

### SSL certificate errors

**Problem:** `Error: unable to verify the first certificate`

**Solution:**
```bash
# Temporary (not recommended for production)
export NODE_TLS_REJECT_UNAUTHORIZED=0

# Better: Use HTTP for local development
aelira scan https://example.com --api-url http://localhost:8000

# Best: Fix SSL certificate or use --insecure flag
aelira scan https://example.com --insecure
```

---

## Authentication Issues

### auth login hangs

**Problem:** `aelira auth login` sends no email or appears to hang

**Cause:** The magic link method requires an institutional email address (`.edu`, `.ac.uk`, and similar). Non-institutional email addresses are rejected by the backend before any email is sent.

**Solution:** Use your university-issued email address:
```bash
aelira auth login --email yourname@university.edu
```

---

### Invalid or expired token

**Problem:** Clicking the magic link returns "Invalid or expired token"

**Cause:** Magic link tokens expire after 15 minutes.

**Solution:** Request a new link:
```bash
aelira auth login --email yourname@university.edu
```

---

### API key not working

**Problem:** Requests fail with `401 Unauthorized` even though a key is configured

**Solutions:**

**1. Verify your current key:**
```bash
aelira config validate
```

**2. Check which key is active:**
```bash
aelira config show
```

**3. Keys may have been revoked from the dashboard.** Generate a new key from your dashboard (`http://localhost:3000` for a local instance) and reconfigure:
```bash
aelira config set apiKey <new-key>
```

---

### Rate limited on login

**Problem:** Login returns `429 Too Many Requests`

**Cause:** Too many login attempts in a short period trigger the rate limiter.

**Solution:** Wait a few minutes before trying again:
```bash
# Wait, then retry
aelira auth login --email yourname@university.edu
```

---

## Scanning Issues

### Scan hangs or times out

**Problem:** Scan never completes, just shows "Processing..."

**Symptoms:**
```
🔍 Scanning: https://example.com
⏳ Processing...
(hangs forever)
```

**Solutions:**

**1. Increase timeout:**
```bash
aelira scan https://example.com --timeout 60000  # 60 seconds
```

**2. Check if page loads:**
```bash
# Test with curl
curl -I https://example.com

# Test with Playwright directly
npx playwright open https://example.com
```

**3. Use verbose mode to see where it hangs:**
```bash
aelira scan https://example.com --verbose
```

**4. Try local file instead:**
```bash
# Download HTML first
curl https://example.com > page.html

# Scan local file
aelira scan ./page.html --local
```

---

### PDF scanning fails

**Problem:** PDF scan produces errors or incorrect results

**Symptoms:**
```
❌ Error: Failed to process PDF: Invalid PDF structure
```

**Solutions:**

**1. Verify PDF is valid:**
```bash
# Check if PDF opens
open document.pdf  # macOS
xdg-open document.pdf  # Linux
```

**2. Check file size:**
```bash
# PDFs > 10MB may cause issues
ls -lh document.pdf

# If too large, try splitting
pdftk document.pdf cat 1-10 output part1.pdf
aelira scan pdf part1.pdf
```

**3. Check PDF permissions:**
```bash
# Some PDFs are password-protected or restricted
qpdf --decrypt --password=yourpassword input.pdf output.pdf
aelira scan pdf output.pdf
```

**4. Re-save PDF:**
```bash
# Open in Preview/Acrobat and "Save As" new PDF
# This often fixes corrupted PDFs
```

---

### PowerPoint scanning fails

**Problem:** PPTX scan produces errors or incorrect results

**Symptoms:**
```
❌ Error: Failed to process PPTX: Unsupported file format
```

**Solutions:**

**1. Verify the file extension is supported:**
```bash
aelira scan presentation.pptx
```

**2. If the file is corrupted, re-save it from PowerPoint or LibreOffice.**

**3. Convert to PDF as a fallback:**
```bash
libreoffice --headless --convert-to pdf presentation.pptx
aelira scan presentation.pdf
```

---

## Export Issues

### CSV export requires --output flag

**Problem:** `aelira export --format csv` fails or outputs garbled text to the terminal

**Cause:** CSV output must be written to a file. JSON can be printed to stdout, but CSV requires the `--output` flag.

**Solution:**
```bash
aelira export --format csv --output scans.csv
```

---

### No scans found

**Problem:** Export returns an empty result set

**Cause:** The API key you have configured determines which department's scans are returned. If the key belongs to a different department, no scans will be visible.

**Solution:** Verify the active key and its associated department:
```bash
aelira config show
```

If the wrong key is active, update it:
```bash
aelira config set apiKey <correct-key>
```

---

### Export is slow

**Problem:** `aelira export` takes a long time for large result sets

**Cause:** The export command fetches full details for each scan individually. Large scan histories amplify this.

**Solution:** Use `--limit` to reduce the number of scans fetched:
```bash
aelira export --limit 50 --output scans.csv
```

---

## File Watcher Issues

### Recursive watching not supported on Linux

**Problem:** The `aelira watch` command only detects changes in the top-level directory on Linux, not subdirectories

**Cause:** Node.js `fs.watch` recursive mode is only supported on macOS and Windows. On Linux, only the top-level directory is monitored.

**Solution:** On Linux, either watch subdirectories explicitly or use a tool like `inotifywait`:
```bash
# Watch a flat directory
aelira watch ./uploads/

# Or use inotifywait as a wrapper on Linux
inotifywait -m -r -e close_write ./uploads/ | while read dir event file; do
  aelira scan "$dir$file"
done
```

---

### Watch command exits immediately

**Problem:** `aelira watch` starts and exits with no output

**Cause:** The watcher performs a `/health` check against the backend on startup. If the backend is unreachable, the watcher exits.

**Solution:** Ensure the backend is running and reachable:
```bash
curl http://localhost:8000/health

# Or against a custom API URL
curl "$AELIRA_API_URL/health"
```

---

### Files not being detected

**Problem:** Files added to the watched directory are not picked up

**Cause:** The `--extensions` filter may not include the file type you are adding. The default set is `.pdf,.docx,.pptx,.xlsx,.html,.htm,.tex,.css,.js`.

**Solution:** Explicitly include the extensions you need:
```bash
aelira watch ./uploads/ --extensions .pdf,.docx,.odt
```

---

## Progress and Retry

### Scan timed out

**Problem:** A scan fails with a timeout error for large documents

**Cause:** Large documents can exceed the backend's processing time. The CLI automatically retries on transient errors (3 retries with exponential backoff), but persistent timeouts indicate the document needs more time than the retry budget allows.

**Solution:** Check the scan status after the fact, as processing may continue server-side:
```bash
aelira history
```

For large documents, increasing the timeout may also help:
```bash
aelira scan document.pdf --timeout 120000
```

---

### Request timed out after Xs

**Problem:** The CLI prints `Request timed out after Xs` and exits

**Cause:** Backend processing took longer than the configured request timeout.

**Solution:** Check whether the scan completed asynchronously:
```bash
aelira history
```

If the scan is still missing, resubmit with a higher timeout:
```bash
aelira scan document.pdf --timeout 120000
```

---

## Performance Issues

### Slow image processing

**Problem:** PDF with many images takes 30+ minutes

**Symptoms:**
```
📄 Scanning PDF: document.pdf (18 images)
[1/18] Analyzing image... (taking 2-3 minutes each)
```

**Solutions:**

**1. Check if Moondream2 model is being used:**
```bash
# Verify in backend logs
docker logs aelira-ollama | grep moondream

# Should see: "Model: moondream" (1.7 GB)
# NOT: "Model: llama3.2-vision" (7.8 GB)
```

**2. Reduce image count:**
```bash
# Skip alt text generation for testing
aelira scan pdf document.pdf

# Or process smaller batches
pdftk input.pdf cat 1-5 output small.pdf
aelira scan pdf small.pdf --generate-alt-text
```

**3. Check system resources:**
```bash
# Monitor CPU/memory
top
htop

# Check Docker resources
docker stats

# Increase Docker memory if needed
# Docker Desktop → Preferences → Resources → Memory: 8GB
```

**4. Use parallel processing:**
```bash
# Split PDF and process in parallel
pdftk input.pdf burst
ls pg_*.pdf | parallel -j 4 aelira scan pdf {}
```

---

### High memory usage

**Problem:** CLI or backend consuming too much RAM

**Solutions:**

**1. Check Docker memory:**
```bash
docker stats

# If high, restart containers
docker-compose -f docker-compose.dev.yml restart
```

**2. Process files in smaller batches:**
```bash
# Instead of --batch on 1000 files
find ./pdfs/ -name "*.pdf" | head -50 | xargs -I {} aelira scan pdf {}
```

**3. Increase available memory:**
```bash
# Docker Desktop → Settings → Resources → Memory: 8GB
# Or use docker-compose resource limits
```

---

## Error Messages

### `Error: EMFILE: too many open files`

**Problem:** Batch processing too many files at once

**Solution:**
```bash
# Increase file descriptor limit (macOS/Linux)
ulimit -n 10000

# Make permanent (macOS)
sudo launchctl limit maxfiles 10000 unlimited

# Process in smaller batches
find ./files/ -name "*.pdf" | xargs -n 10 -I {} aelira scan pdf {}
```

---

### `Error: Playwright browser not installed`

**Problem:** Missing Playwright dependencies

**Solution:**
```bash
# Install Playwright browsers
npx playwright install

# Or install with dependencies
npx playwright install --with-deps
```

---

### `Error: Command failed with exit code 1`

**Problem:** Generic error, need more details

**Solution:**
```bash
# Run with verbose logging
aelira scan https://example.com --verbose

# Check logs
cat ~/.aelira/logs/latest.log

# Enable debug mode
DEBUG=* aelira scan https://example.com
```

---

### `Error: Timeout of 30000ms exceeded`

**Problem:** Page takes too long to load

**Solutions:**

**1. Increase timeout:**
```bash
aelira scan https://example.com --timeout 60000
```

**2. Add load delay for SPAs:**
```bash
aelira scan https://example.com --load-delay 5000
```

**3. Check if site is accessible:**
```bash
curl -I https://example.com
ping example.com
```

---

## Platform-Specific Issues

### macOS Issues

**Problem:** Gatekeeper blocks CLI execution

**Solution:**
```bash
# Allow CLI to run
xattr -d com.apple.quarantine $(which aelira)

# Or allow in System Preferences
# System Preferences → Security & Privacy → Allow
```

**Problem:** Docker not running

**Solution:**
```bash
# Start Docker Desktop
open -a Docker

# Wait for Docker to start
until docker info > /dev/null 2>&1; do sleep 1; done
echo "Docker is ready"
```

---

### Windows Issues

**Problem:** Path with spaces causes errors

**Solution:**
```bash
# Use quotes for paths with spaces
aelira scan pdf "C:\Users\Name\Documents\My Files\document.pdf"

# Or use short path
aelira scan pdf "C:\Users\Name\DOCUME~1\MYFILE~1\document.pdf"
```

**Problem:** Docker commands not working

**Solution:**
```bash
# Use PowerShell or WSL2
# Install WSL2 first:
wsl --install

# Then run commands in WSL2
wsl
cd /mnt/c/Users/Name/Projects/Aelira
aelira scan https://example.com
```

---

### Linux Issues

**Problem:** Permission denied for Docker

**Solution:**
```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and back in, then verify
docker ps
```

**Problem:** Missing dependencies

**Solution:**
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
  libnss3 \
  libnspr4 \
  libatk1.0-0 \
  libatk-bridge2.0-0 \
  libcups2 \
  libdrm2 \
  libxkbcommon0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libgbm1 \
  libasound2

# Then reinstall Playwright
npx playwright install --with-deps
```

---

## CI Environments

### Config directory pollution in CI

**Problem:** The CLI writes config files to `~/.aelira/`, which can pollute the home directory of a shared CI runner or cause permission conflicts.

**Solution:** Set the `AELIRA_CONFIG_DIR` environment variable to a temporary directory so each CI run gets an isolated config location:

```bash
export AELIRA_CONFIG_DIR=$(mktemp -d)
aelira config set apiKey "$AELIRA_API_KEY"
aelira scan document.pdf
```

This is especially important for GitHub Actions, GitLab CI, and other shared runners where multiple jobs may run concurrently as the same OS user.

---

## Debug Mode

### Enable verbose logging

```bash
# CLI verbose mode
aelira scan https://example.com --verbose

# Environment variable
DEBUG=* aelira scan https://example.com

# Log to file
aelira scan https://example.com --verbose > scan.log 2>&1
```

### Check configuration

```bash
# Show current config
cat ~/.aelirarc.json

# Validate config
aelira config --validate

# Reset to defaults
rm ~/.aelirarc.json
aelira config --init
```

### Test backend connection

```bash
# Health check
curl http://localhost:8000/api/scan/health

# Test PDF endpoint
curl -X POST http://localhost:8000/api/scan/pdf \
  -F "file=@test.pdf" \
  -F "generate_alt_text=false"

# Check logs
docker-compose -f docker-compose.dev.yml logs api --tail 100
```

---

## Getting Help

### Collect diagnostic information

```bash
#!/bin/bash
# diagnostic.sh - Collect system information

echo "=== Aelira CLI Diagnostics ==="
echo

echo "CLI Version:"
aelira --version

echo -e "\nNode Version:"
node --version

echo -e "\nnpm Version:"
npm --version

echo -e "\nDocker Status:"
docker ps | grep aelira || echo "No Aelira containers running"

echo -e "\nBackend Health:"
curl -s http://localhost:8000/api/scan/health || echo "Backend not accessible"

echo -e "\nSystem Info:"
uname -a

echo -e "\nAvailable Memory:"
free -h || vm_stat | grep "Pages free"

echo -e "\nConfig File:"
cat ~/.aelirarc.json 2>/dev/null || echo "No config file found"

echo -e "\nRecent Logs:"
tail -20 ~/.aelira/logs/latest.log 2>/dev/null || echo "No logs found"
```

Run diagnostic:
```bash
chmod +x diagnostic.sh
./diagnostic.sh > diagnostic-report.txt

# Share diagnostic-report.txt when asking for help
```

---

### Submit a bug report

**Include in your report:**
1. CLI version (`aelira --version`)
2. Operating system (macOS/Windows/Linux)
3. Node version (`node --version`)
4. Docker version (`docker --version`)
5. Command that failed
6. Full error message
7. Diagnostic report (see above)

**Where to report:**
- **GitHub Issues:** https://github.com/Aelira-AI/aelira-core/issues
- **Discord:** https://discord.gg/aelira (if available)

---

## FAQ

### Q: Why is the CLI so slow?

**A:** Most likely causes:
1. Using LLaMA Vision instead of Moondream2 (check backend logs)
2. Processing many images at once
3. Insufficient Docker memory (<4GB)
4. Slow internet connection (for website scans)

---

### Q: Can I use the CLI without the backend?

**A:** Yes, for website scanning only:
```bash
aelira scan https://example.com --local
```

For PDF/PPT/LaTeX/video processing, backend is required.

---

### Q: How do I update the CLI?

**A:**
```bash
# Update to latest version
npm update -g @aelira/cli

# Or reinstall
npm uninstall -g @aelira/cli
npm install -g @aelira/cli

# Verify version
aelira --version
```

---

### Q: Can I run multiple scans in parallel?

**A:** Yes:
```bash
# GNU Parallel (recommended)
find ./pdfs/ -name "*.pdf" | parallel -j 4 aelira scan pdf {}

# xargs (alternative)
find ./pdfs/ -name "*.pdf" | xargs -P 4 -I {} aelira scan pdf {}

# Background jobs
aelira scan pdf file1.pdf & \
aelira scan pdf file2.pdf & \
aelira scan pdf file3.pdf & \
wait
```

---

## Still Need Help?

1. **Check documentation:** https://github.com/Aelira-AI/aelira-core/tree/main/cli/docs
2. **Search existing issues:** https://github.com/Aelira-AI/aelira-core/issues
3. **Ask on Discord:** https://discord.gg/aelira
4. **Open a new issue:** https://github.com/Aelira-AI/aelira-core/issues/new

---

**Last Updated:** March 17, 2026
**CLI Version:** v0.4.0

**Made with 💜 by the Aelira team**

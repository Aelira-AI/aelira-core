# Aelira CLI troubleshooting

**Version:** v0.9.11 · **CLI Version:** v0.9.11

Check the installed command's help before adding flags: `aelira scan pdf --help`, for example. See the [command reference](COMMANDS.md) and [tested example syntax](EXAMPLES.md).

## Installation and command discovery

The CLI requires Node.js 22 or newer. Install or update it with your package manager:

```bash
npm install -g @aelira/cli
npm list -g @aelira/cli
node --version
```

If your shell cannot find `aelira`, confirm the global installation succeeded and that your package manager's executable directory is in `PATH`. On macOS/Linux, npm's global executables are under `$(npm prefix -g)/bin`. With a Node version manager, use the same Node installation for installing and running the CLI. Open a new shell after changing your shell configuration.

If browser-based scans report missing Chromium, install the browser matching the CLI's Playwright dependency. For a repository checkout, run from `cli/` after installing its dependencies:

```bash
./node_modules/.bin/playwright install chromium
```

On supported Linux distributions, Playwright's `install --with-deps chromium` can also provision browser system dependencies. These are browser prerequisites for local web scans, not for CLI argument/help inspection.

## Backend connection refused or unhealthy

Document/media processing and API-backed commands need a reachable server. Confirm the selected endpoint:

```bash
aelira config show
aelira config set api-url http://localhost:8000
aelira config validate
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/api/health
```

The server exposes `/health` and `/api/health`. A successful health response is not proof that a particular model, provider, job worker, or authenticated endpoint is available. Use the actual host configured for your deployment when checking a remote server.

For a local development checkout, run Compose from the **repository root**, where the Compose files live:

```bash
docker compose -f docker-compose.quickstart.yml up -d
docker compose -f docker-compose.quickstart.yml ps
docker compose -f docker-compose.quickstart.yml logs --tail 100 api
```

The [quickstart Compose configuration](../../docker-compose.quickstart.yml) is for local development. Follow the [repository setup instructions](../../README.md) for provider setup and deployment choices. If you started a different Compose configuration, use that same file when inspecting its services.

The API URL resolves from `--api-url`, then `AELIRA_API_URL`, then the active profile, then the localhost default. An environment override can therefore explain why changing a profile did not change the selected endpoint.

For a local-only HTML check:

```bash
aelira scan ./dist/index.html --local
```

## TLS certificate errors

Check that the configured URL uses the correct hostname and that the server presents a valid certificate chain. For an organization-managed certificate authority, configure Node's trusted certificates according to your organization's setup. Fix the certificate/trust configuration before retrying API commands.

## Authentication and configuration

```bash
aelira auth login
aelira config show
aelira config profile list
```

`auth login` prompts for the method and email/key; it has no email flag. If a magic link fails or expires, request a new link through the prompt. If login is rate-limited, follow the server's retry guidance before trying again.

For API authorization failures, verify the active profile and any `AELIRA_API_KEY` override. `config validate` checks backend health only. A revoked key or a key for another department can still fail protected requests even when health succeeds. Replace the stored key interactively with `auth login`, or set an issued key from an environment variable:

```bash
aelira config set api-key "$AELIRA_API_KEY"
```

Configuration lives at `~/.aelira/config.json`, or `$AELIRA_CONFIG_DIR/config.json` when overridden. Use `config show` rather than posting the raw file in a bug report. Reconfigure with the positional `init` action:

```bash
aelira config init
```

## Unknown command, flag, or output format

Use a scanner-specific command for documents and media. Plain `scan` accepts URLs and HTML; it does not select a document scanner based on a PDF or PowerPoint extension.

```bash
aelira scan pdf document.pdf
aelira scan ppt presentation.pptx
aelira scan pdf ./documents/ --format json --output pdf-results.json
```

Directory processing is built into the document scanners. The PDF scanner's OCR control is `--skip-ocr`. PowerPoint scanning returns findings; remediation uses a previous scan ID with `aelira remediate`. See command help for the exact flags and accepted formats for each scanner.

For structured files, choose an implemented output path such as `--format json --output results.json`. Do not infer that an accepted format creates every possible artifact: video output is JSON, and a PDF input scan does not accept the web scanner's PDF-report option.

## Web scan times out

Confirm the target page loads in your browser, then adjust the web scanner's page-load timeout or settling delay:

```bash
aelira scan https://example.com --timeout 60000 --load-delay 5000
```

These flags apply to web page loading. Document scanners do not expose a timeout flag. For backend document timeouts, inspect server/worker logs and the scan's status in the dashboard before resubmitting. `aelira history` reads local history and cannot determine whether a server job is still running.

## PDF or PowerPoint processing fails

Confirm the file exists, opens in its native application, and is a supported format. For encrypted or corrupted documents, obtain an accessible, readable copy from the document owner or resave the original. Check backend/worker logs for the actual rejection rather than assuming a fixed file-size limit or a particular AI model.

Start with one file and save its results:

```bash
aelira scan pdf document.pdf --format json --output pdf-results.json
aelira scan ppt presentation.pptx --format json --output ppt-results.json
```

AI-dependent results depend on the deployment's configured providers. Consult the server setup documentation when logs report missing models or credentials.

## Export errors or missing records

CSV export requires a destination; JSON can also be written to a file:

```bash
aelira export --format csv --output scans.csv
aelira export --format json --limit 20 --output scans.json
```

`export` uses the backend history visible to your credentials. It includes scans with reported issues and successfully fetched details. Zero-issue scans and failed detail fetches are omitted. If records are missing, check the active key/department and backend results. Lowering `--limit` reduces detail requests for large histories.

## Watcher does not detect files

The command is `aelira scan watch`. Ensure the directory exists, the backend health check succeeds, and the extension filter includes the files you change:

```bash
aelira scan watch ./uploads --extensions .pdf,.docx,.pptx --debounce 1000
aelira scan watch ./uploads/subfolder --no-recursive
```

The current watcher disables recursive mode on Linux, so watch needed subdirectories separately. It scans changes after startup, not every pre-existing file. Supported dispatch extensions are `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.htm`, `.tex`, `.css`, and `.js`; adding an unsupported extension to the filter does not add a scanner. Stop with Ctrl+C.

## CI configuration

Set `AELIRA_CONFIG_DIR` to an isolated directory when a CI job writes configuration, and provide credentials through the runner's secret environment:

```bash
export AELIRA_CONFIG_DIR="$(mktemp -d)"
aelira config set api-url https://api.example.edu
```

Local web CI scans need Chromium and the built target files. API-backed scans additionally need network access, credentials, and the relevant server components. See [CI examples](EXAMPLES.md#gate-a-build-in-ci).

## Report a problem

Include the CLI version (from `npm list -g @aelira/cli` for a global install), Node version, operating system, exact command, and error message. For a checkout, include its revision. Review shared output for credentials, private URLs, and document contents; `config show` masks the configured key but still displays endpoint and department information.

Report reproducible issues in the [Aelira issue tracker](https://github.com/Aelira-AI/aelira-core/issues). Include relevant server/worker log excerpts for API processing failures.

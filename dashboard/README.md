# Aelira Dashboard

The customer-facing dashboard for the Aelira accessibility scanning platform. React + TypeScript single-page app that talks to the FastAPI backend for document/website scanning, issue tracking, compliance reporting, and Canvas LTI integration.

## Tech Stack

Versions below are read directly from [`package.json`](package.json) — check there for the current pinned ranges.

- **React 19** with **Vite 8** for the dev server and build
- **React Router 7** for client-side routing
- **TypeScript 5** — the entire `src/` tree is `.ts`/`.tsx`, no `.jsx`
- **Tailwind CSS 4** with a custom semantic design token system
- **TanStack Query** for server-state caching
- **Axios** for the API client
- **Recharts** for charts
- **Lucide React** for icons
- **Playwright** for end-to-end tests, **axe-playwright** for automated accessibility scans

## Getting Started

### Prerequisites

- Node.js 20+ (matches CI, see `.github/workflows/ci.yml`)
- npm

### Setup

```bash
# Install dependencies
npm install

# Start the dev server (default: http://localhost:5173)
npm run dev

# Type-check + build for production
npm run build

# Preview the production build locally
npm run preview

# Lint
npm run lint
```

### Tests

```bash
npm run test        # Playwright end-to-end tests
npm run test:ui     # Playwright with the interactive UI runner
npm run test:auth   # Just the auth flow spec (tests/auth.spec.ts)
npm run test:unit   # Node's built-in test runner over tests/unit/*.test.js
npm run test:all    # Unit tests + full Playwright suite
```

## Environment Variables

Copy [`.env.example`](.env.example) to `.env` and fill in real values. Everything in `.env.example` is authoritative — this section mirrors it:

| Variable | Required | Purpose |
|---|---|---|
| `VITE_API_URL` | Yes | Backend API base URL. `http://localhost:8000` in development, your API domain in production. Read in `src/api/client.ts`, `src/api/ltiClient.ts`, and elsewhere. |
| `VITE_UMAMI_WEBSITE_ID` | No | Umami (privacy-focused, open-source analytics) website ID. Only loads when the user consents to analytics cookies via the cookie banner. |
| `VITE_UMAMI_URL` | No | Umami instance URL, paired with the ID above. |

### `VITE_DEV_MODE`

`VITE_DEV_MODE=true` (not part of `.env.example`, set it yourself if you need it) toggles a warning banner on the Settings page next to the API key display — nothing more. **It is not an authentication bypass.** Authentication is always validated against the backend regardless of this flag; `tests/auth.spec.ts` (see the `'VITE_DEV_MODE does not bypass authentication'` test) asserts this directly, and `.env.example` states it explicitly. Do not rely on this flag for anything security-relevant.

## Design System

The dashboard uses a semantic CSS-variable token system for automatic light/dark theming, defined in [`src/index.css`](src/index.css).

### Semantic classes

- `.text-primary` / `.text-secondary` / `.text-tertiary` / `.text-accent` — text
- `.bg-surface-primary` / `.bg-surface-secondary` / `.bg-surface-tertiary` — backgrounds
- `.input`, `.card`, `.btn-primary`, `.btn-secondary` — components

Prefer these over inline `dark:` Tailwind utilities — they keep theming centralized in `src/index.css` instead of scattered across components.

```css
/* src/index.css — light mode */
:root {
  --content-primary: #1F1D1A;
  --content-secondary: #5C564D;
  --surface-primary: #FDFBF7;
  /* ... */
}

/* dark mode */
.dark {
  --content-primary: #F5F2ED;
  --content-secondary: #B5AFA5;
  --surface-primary: #1A1816;
  /* ... */
}
```

Dark mode is controlled by the `dark` class on `<html>`, managed by `ThemeContext` (`src/context/ThemeContext.tsx`) and toggled via `ThemeToggle` (`src/components/ThemeToggle.tsx`), which calls `useTheme()` rather than touching `classList` directly.

The exact hex values above live only in `src/index.css` — treat that file as the source of truth; it changes independently of this README.

## Project Structure

```
dashboard/
├── src/
│   ├── api/              # API clients (client.ts, ltiClient.ts)
│   ├── components/       # Reusable UI components
│   │   ├── auth/         # Auth-related components
│   │   ├── charts/       # Recharts-based chart components
│   │   ├── layout/       # Navbar, Sidebar
│   │   ├── results/      # Scan result components
│   │   ├── review/       # Document review / table structure editing
│   │   ├── settings/     # Settings page subcomponents
│   │   └── upload/       # File upload components
│   ├── context/          # React context providers (Theme, Toast, ...)
│   ├── hooks/             # Shared hooks (e.g. useFocusTrap)
│   ├── pages/             # Route pages
│   ├── types/             # Shared TypeScript types
│   ├── utils/             # Utility functions
│   ├── index.css          # Global styles + design token system
│   └── main.tsx            # App entry point
├── public/                # Static assets (favicons, logos, manifest)
├── tests/                 # Playwright + unit tests
├── Dockerfile
├── nginx.conf
└── package.json
```

## Accessibility

The dashboard targets WCAG 2.1 AA. Two docs cover this in more depth:

- [`ACCESSIBILITY.md`](ACCESSIBILITY.md) — audit notes and the accessibility patterns in use across components
- [`SCREEN_READER_TESTING.md`](SCREEN_READER_TESTING.md) — a manual screen-reader testing checklist for the dashboard's own flows

Recent work worth knowing about if you're touching interactive components:

- `useFocusTrap` (`src/hooks/useFocusTrap.ts`) — a reusable hook that traps Tab/Shift+Tab within a container while active, wraps at both ends, and restores focus to the trigger element on close. Used by `TableStructureEditor`'s scope selector (`src/components/review/TableStructureEditor.tsx`).
- Decorative click-to-dismiss backdrops (the `CookieBanner` preferences overlay, the `Sidebar` mobile overlay) use `role="presentation"` and `aria-hidden="true"` — they're not exposed as interactive controls to assistive tech; each modal has an explicit close button for keyboard users instead.
- `IssuesByTypeChart`'s bar/pie switcher is a labelled `role="group"` with `aria-label="Chart type"`, and each button carries `aria-pressed` reflecting the active chart type.

## Deployment

The dashboard ships as a static build served by nginx. See [`Dockerfile`](Dockerfile) and [`nginx.conf`](nginx.conf).

```bash
docker build \
  --build-arg VITE_API_URL=https://api.your-domain.com \
  --build-arg VITE_WEBSITE_URL=https://your-domain.com \
  -t aelira-dashboard .
docker run -p 80:80 aelira-dashboard
```

Vite environment variables are baked in at build time via `ARG`/`ENV` in the Dockerfile — set them at `docker build` time, not at container runtime. The build stage (`node:20-alpine`) runs `npm ci && npm run build`; the runtime stage (`nginx:alpine`) copies `dist/` and `nginx.conf`, which handles SPA fallback routing, gzip, cache headers for `/assets/`, and a `/health` endpoint.

## API Integration

The dashboard talks to the FastAPI backend through an Axios instance in [`src/api/client.ts`](src/api/client.ts):

```ts
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
  withCredentials: true,
});
```

Representative scan endpoints (see `src/api/education/scan_routes.py` and `web_scan_routes.py` on the backend):

- `POST /api/education/pdf/scan` — scan a PDF
- `POST /api/education/web/scan` — scan a website
- `GET /api/education/scans` — scan history
- `GET /api/education/scans/{id}` — scan detail
- `GET /api/education/scans/{id}/progress` — poll scan progress

## Contributing

- Use the semantic design token classes (`.text-primary`, `.bg-surface-*`, etc.) instead of inline `dark:` utilities.
- Follow existing component patterns — check a similar component before inventing a new one.
- Test both light and dark themes when touching UI.
- Maintain WCAG 2.1 AA as the accessibility floor; see [`ACCESSIBILITY.md`](ACCESSIBILITY.md).
- Everything is TypeScript already — no `.jsx`/`.js` components to migrate.

## Related Documentation

- [Accessibility audit notes](ACCESSIBILITY.md)
- [Screen reader testing checklist](SCREEN_READER_TESTING.md)
- [Project changelog](../CHANGELOG.md)

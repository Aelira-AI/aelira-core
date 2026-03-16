# Aelira Dashboard

**Version:** v0.3.0 (November 30, 2025) - Phase 4 Complete ✅

Customer-facing dashboard for the Aelira accessibility scanning platform with comprehensive issue management, team collaboration, compliance certificates, and Canvas LTI integration support.

## Tech Stack

- **React 18.3** with Vite 7.1 for fast development
- **React Router 7.1** for client-side routing
- **Tailwind CSS v4** with custom semantic design system
- **Lucide React** for icons
- **Axios** for API client

## Design System

The dashboard uses a semantic token architecture with CSS variables for automatic light/dark mode theming.

### Semantic Classes

#### Text
- `.text-primary` - Main content text (auto theme)
- `.text-secondary` - Secondary text
- `.text-tertiary` - Muted text
- `.text-accent` - Accent/brand text

#### Components
- `.input` - Form inputs with theme support
- `.card` - Container component
- `.btn-primary` - Primary button
- `.btn-secondary` - Secondary button

#### Backgrounds
- `.bg-surface-primary` - Main background
- `.bg-surface-secondary` - Secondary background
- `.bg-surface-tertiary` - Tertiary background

### CSS Variables

Defined in [`src/index.css`](src/index.css):

```css
/* Light Mode */
:root {
  --content-primary: #0F172A;
  --content-secondary: #475569;
  --surface-primary: #FFFFFF;
  /* ... */
}

/* Dark Mode */
.dark {
  --content-primary: #F8FAFC;
  --content-secondary: #94A3B8;
  --surface-primary: #0F172A;
  /* ... */
}
```

### Best Practices

**✅ DO:**
```jsx
<input className="input" />
<label className="text-primary" />
<div className="card" />
```

**❌ DON'T:**
```jsx
<input className="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100" />
<label className="text-gray-900 dark:text-gray-100" />
<div className="bg-gray-50 dark:bg-gray-800" />
```

Use semantic token classes instead of inline Tailwind utilities for better maintainability and consistency.

## Project Structure

```
dashboard/
├── src/
│   ├── api/              # API client and configuration
│   ├── components/       # Reusable UI components
│   │   ├── layout/      # Layout components (Navbar, Sidebar)
│   │   ├── results/     # Scan result components
│   │   └── upload/      # File upload components
│   ├── context/         # React context providers
│   ├── pages/           # Route pages
│   ├── index.css        # Global styles + design system
│   └── main.jsx         # App entry point
├── public/              # Static assets
└── package.json
```

## Development

### Prerequisites

- Node.js 18+
- npm 9+

### Setup

```bash
# Install dependencies
npm install

# Start dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

### Environment Variables

Create `.env` file:

```env
VITE_API_URL=http://localhost:8000  # Backend API URL
```

## Features

### Phase 4: Advanced Features (November 30, 2025) ✅ **LATEST**

**Issue Management Page (`/issues`):**

- Comprehensive issue filtering by severity, type, status, date range
- Issue assignment to team members
- Add notes/comments to tracked issues
- Mark issues as resolved/ignored
- Bulk actions for issue management
- Status workflow: open → in-progress → resolved

**Team Collaboration Features:**

- Issue assignment with user selection
- Issue notes and comments
- Priority levels (critical, high, medium, low)
- Resolution tracking with timestamps

**Compliance Certificates:**

- PDF certificate generation via API
- Department name, date, compliance score, scan summary
- QR code for online verification
- Certificate download button in dashboard

**Canvas LTI Support:**

- LTI launch integration ready
- Deep linking content picker
- Grade passback support
- Session management for LMS users

---

### Week 4: Trend Visualization & Deadline Tracking (November 3, 2025) ✅

**TrendGraph Component:**

- 30-day compliance trend line chart with Recharts
- Trend direction indicator (improving/declining/stable with score delta)
- WCAG threshold reference lines (70 = warning, 90 = excellent)
- Custom tooltip (date, score, scan count)
- Empty state handling ("Not enough data to show trend")
- Color-coded legend (Excellent 90+, Good 70-89, Needs Work <70)

**DeadlineWidget Component:**

- April 24, 2026 countdown (days remaining: ~540)
- Current compliance score display with color coding
- Estimated hours remaining (2 hours per issue heuristic)
- Time elapsed progress bar (visual % of time passed)
- Status-based alerts:
  - Critical: <90 days + <90% compliance
  - Warning: <180 days + <80% compliance
  - Ahead: ≥90% compliance
  - On Track: Normal progress

**Dashboard Integration:**

- Stats cards (total scans, avg compliance, files processed, issues)
- DeadlineWidget section (April 2026 countdown)
- TrendGraph section (30-day compliance history)
- Priority Issues section (top 5 lowest compliance scans)
- Recent Scans section (empty state with "Upload File" CTA)

**API Client Enhancements:**

- `getGeneralStats()` - General stats without department ID
- `getPriorityIssues(deptId, limit)` - Top issues by severity
- `getComplianceTrend(deptId, days)` - Historical compliance data

**Database Updates:**

- Migration `9b4baa757e4e` adds `progress` and `progress_message` columns
- All 8 tables operational (see `MIGRATION_SAFETY_GUIDE.md`)

### Authentication
- Dev mode bypass for rapid iteration (`VITE_DEV_MODE=true`)
- API key authentication (Bearer token via Axios interceptors)
- Protected routes via AuthContext
- Auto-redirect on 401 errors

### Scan Types

- **PDF** - Document accessibility scanning with OCRmyPDF
- **PowerPoint** - Presentation scanning with CVD simulation
- **Word** - Document accessibility scanning (NEW)
- **Excel** - Spreadsheet accessibility scanning (NEW)
- **LaTeX** - Mathematical content with chemistry/physics support (ENHANCED)
- **Image** - Alt text generation with Moondream2 AI
- **Video** - Multimedia transcription with Whisper
- **Website** - Live website scanning with Playwright + axe-core
- **Code** - HTML/CSS/JS static analysis

### Real-Time Progress
- WebSocket-based progress updates (planned)
- Polling-based progress for website scans
- Animated progress bars

### Results & Reports
- Detailed WCAG 2.1/2.2 compliance scoring
- Issue categorization (Critical, Serious, Moderate, Low)
- AI-generated code fixes (Qwen Coder)
- PDF report generation
- Fixed HTML download

## Dark Mode Support

The dashboard fully supports dark mode with WCAG 2.1 AA contrast compliance.

### Implementation

Dark mode is controlled by the `dark` class on the `<html>` element:

```jsx
// ThemeToggle component
const toggleTheme = () => {
  document.documentElement.classList.toggle('dark');
};
```

All semantic token CSS variables automatically adapt to the theme.

### Recent Fixes (v0.17.0)

- ✅ Fixed text visibility issues in dark mode
- ✅ Refactored WebsiteScanner to use design system
- ✅ Eliminated CSS specificity issues
- ✅ WCAG 2.1 AA compliant contrast ratios

See [`DARK_MODE_CONTRAST_FIXES_v0.17.0.md`](../DARK_MODE_CONTRAST_FIXES_v0.17.0.md) for details.

## API Integration

The dashboard connects to the FastAPI backend:

```javascript
// src/api/client.js
import axios from 'axios';

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  timeout: 300000, // 5 minutes
  headers: {
    'Content-Type': 'application/json',
  },
});
```

### Endpoints

- `POST /api/education/pdf/scan` - Scan PDF
- `POST /api/education/web/scan` - Scan website
- `GET /api/education/scans` - Get scan history
- `GET /api/education/scans/{id}` - Get scan details
- `GET /api/education/scans/{id}/progress` - Poll scan progress

## Performance

- **Hot Module Reload** - Instant updates during development
- **Code Splitting** - Route-based lazy loading (planned)
- **Optimized Assets** - Vite's built-in optimizations
- **CSS Variables** - Efficient theme switching

## Contributing

### Component Guidelines

1. **Use semantic design tokens** - Avoid inline `dark:` utilities
2. **Follow existing patterns** - Check similar components
3. **Test both themes** - Verify light and dark modes
4. **Maintain accessibility** - WCAG 2.1 AA minimum
5. **Use TypeScript** (planned) - Add type safety

### Code Style

- React functional components with hooks
- Named exports for components
- Descriptive variable names
- Comments for complex logic

## Related Documentation

- [Backend Integration Testing](../INTEGRATION_COMPLETE.md)
- [Dark Mode Fixes](../DARK_MODE_CONTRAST_FIXES_v0.17.0.md)
- [Deployment Guide](../DEPLOYMENT_GUIDE_v0.16.0.md)

## Version

**Current:** v0.3.0 (Phase 4 Complete - Advanced Features + LTI Integration)

See [main CHANGELOG](../../CHANGELOG.md) for full version history.

### New in v0.3.0

- Issue Management page with filtering and assignment
- Team collaboration features (notes, priority, status)
- Compliance certificate download
- Canvas LTI integration support
- Word and Excel document scanning
- Enhanced LaTeX support (chemistry, physics)

---

**Built with ❤️ for accessibility**

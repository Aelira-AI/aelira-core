import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

// Apply dark mode BEFORE React renders to prevent flash
// Check localStorage first (syncs with marketing site's next-themes)
// Then fall back to system preference
function applyInitialTheme(): void {
  // LTI iframe: always force light mode — ignore stored preferences
  if (window.location.pathname.startsWith('/lti/')) {
    document.documentElement.classList.remove('dark');
    return;
  }

  const stored = localStorage.getItem('theme');
  const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

  // next-themes stores 'dark', 'light', or 'system'
  const shouldBeDark = stored === 'dark' || (stored !== 'light' && systemPrefersDark);

  if (shouldBeDark) {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }
}

applyInitialTheme();

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

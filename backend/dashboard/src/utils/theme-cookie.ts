/**
 * Cross-subdomain theme cookie utility
 *
 * Shares theme preference between aelira.ai and dashboard.aelira.ai
 * Uses cookies instead of localStorage for cross-subdomain support.
 */

const COOKIE_NAME = 'aelira-theme';
const COOKIE_MAX_AGE = 31536000; // 1 year in seconds

/**
 * Get the cookie domain for cross-subdomain sharing
 * Returns .aelira.ai for production, undefined for localhost
 */
function getCookieDomain(): string | undefined {
  const hostname = window.location.hostname;

  // Localhost - don't set domain (cookies work on same origin)
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return undefined;
  }

  // Production - use root domain for cross-subdomain sharing
  // aelira.ai, dashboard.aelira.ai, etc. all share .aelira.ai
  if (hostname.endsWith('aelira.ai')) {
    return '.aelira.ai';
  }

  // Other domains (e.g., preview deployments) - don't set domain
  return undefined;
}

/**
 * Get theme from cookie
 */
export function getThemeCookie(): 'light' | 'dark' | null {
  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === COOKIE_NAME) {
      if (value === 'light' || value === 'dark') {
        return value;
      }
    }
  }
  return null;
}

/**
 * Set theme cookie for cross-subdomain sharing
 */
export function setThemeCookie(theme: 'light' | 'dark'): void {
  const domain = getCookieDomain();
  const domainPart = domain ? `; domain=${domain}` : '';

  document.cookie = `${COOKIE_NAME}=${theme}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax${domainPart}`;
}

/**
 * Remove theme cookie (revert to system preference)
 */
export function removeThemeCookie(): void {
  const domain = getCookieDomain();
  const domainPart = domain ? `; domain=${domain}` : '';

  document.cookie = `${COOKIE_NAME}=; path=/; max-age=0${domainPart}`;
}

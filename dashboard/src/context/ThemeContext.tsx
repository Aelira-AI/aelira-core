import React, { useEffect, useState, ReactNode } from 'react';
import { getThemeCookie, setThemeCookie } from '../utils/theme-cookie';
import { ThemeContext } from './theme-context';
import type { Theme } from './theme-context';

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps): React.ReactElement {
  const isLTI = window.location.pathname.startsWith('/lti/');

  const [theme, setThemeState] = useState<Theme>(() => {
    // LTI iframe: always light — ignore cookies/localStorage/system
    if (isLTI) {
      return 'light';
    }
    // Priority: cookie (cross-subdomain) > localStorage > system preference
    const cookieTheme = getThemeCookie();
    if (cookieTheme) {
      return cookieTheme;
    }
    const stored = localStorage.getItem('theme');
    if (stored === 'dark' || stored === 'light') {
      return stored;
    }
    // If 'system' or not set, check system preference
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  useEffect(() => {
    // Apply theme class to document
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [theme]);

  // Listen for system preference changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = (e: MediaQueryListEvent): void => {
      // Never auto-switch in LTI iframe
      if (isLTI) return;
      const stored = localStorage.getItem('theme');
      // Only auto-switch if user hasn't explicitly set a preference
      if (!stored || stored === 'system') {
        setThemeState(e.matches ? 'dark' : 'light');
      }
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, [isLTI]);

  const setTheme = (newTheme: Theme): void => {
    setThemeState(newTheme);
    // Store preference in both localStorage and cookie (cross-subdomain)
    localStorage.setItem('theme', newTheme);
    setThemeCookie(newTheme);
  };

  const toggleTheme = (): void => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

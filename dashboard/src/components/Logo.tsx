import React, { useState, useEffect } from 'react';

// ============================================================================
// Types
// ============================================================================

interface LogoProps {
  width?: number;
  height?: number;
  className?: string;
}

type Theme = 'dark' | 'light';

// ============================================================================
// Helper
// ============================================================================

// Initialize theme from DOM (avoids setState in effect)
const getInitialTheme = (): Theme => {
  if (typeof document !== 'undefined') {
    return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  }
  return 'light';
};

// ============================================================================
// Component
// ============================================================================

export function Logo({ width = 200, height = 60, className = '' }: LogoProps): React.ReactElement {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    // Watch for theme changes via MutationObserver
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'class') {
          const isDark = document.documentElement.classList.contains('dark');
          setTheme(isDark ? 'dark' : 'light');
        }
      });
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });

    return () => observer.disconnect();
  }, []);

  // Branding is swappable so an institution can deploy this under its own mark
  // without forking. Defaults are the Aelira assets; see BRANDING.md for the
  // trademark position, which is that the code licence grants no rights to the
  // name or the marks.
  const brandName = import.meta.env.VITE_BRAND_NAME || 'Aelira';
  const darkLogo =
    import.meta.env.VITE_LOGO_DARK ||
    '/aelira logo horizontal - dark mode Transparent bg.svg';
  const lightLogo =
    import.meta.env.VITE_LOGO_LIGHT ||
    '/aelira logo horizontal - light mode Transparent bg.svg';

  return (
    <img
      src={theme === 'dark' ? darkLogo : lightLogo}
      alt={brandName}
      width={width}
      height={height}
      className={className}
      style={{ width: `${width}px`, height: 'auto' }}
    />
  );
}

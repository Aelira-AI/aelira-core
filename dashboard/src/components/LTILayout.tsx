import React, { useEffect } from 'react';

interface LTILayoutProps {
  children: React.ReactNode;
  error?: string | null;
  loading?: boolean;
}

export function LTILayout({ children, error, loading }: LTILayoutProps): React.ReactElement {
  // Prevent Dark Reader from processing the iframe (causes double-inversion)
  // and force theme from system preference, ignoring stored cookie/localStorage
  useEffect(() => {
    // Add Dark Reader lock meta tag
    const meta = document.createElement('meta');
    meta.name = 'darkreader-lock';
    document.head.appendChild(meta);

    // Force light mode in LTI iframe — Canvas defaults to light,
    // and we can't reliably detect the host page's theme from inside
    // an iframe. Ignore OS dark mode and stored preferences.
    document.documentElement.classList.remove('dark');

    return () => {
      document.head.removeChild(meta);
    };
  }, []);
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen"
           style={{ backgroundColor: 'var(--surface-primary)' }}>
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 mx-auto mb-4"
               style={{ borderColor: 'var(--accent-primary)' }} />
          <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>Loading...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen"
           style={{ backgroundColor: 'var(--surface-primary)' }}>
        <div className="text-center max-w-md p-8">
          <p className="text-lg font-medium mb-2" style={{ color: 'var(--content-primary)' }}>
            Session Error
          </p>
          <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-4" style={{ backgroundColor: 'var(--surface-primary)', color: 'var(--content-primary)' }}>
      {children}
    </div>
  );
}

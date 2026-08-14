import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { LogOut, User } from 'lucide-react';
import { ThemeToggle } from '../ThemeToggle';
import { Logo } from '../Logo';

// ============================================================================
// Component
// ============================================================================

export function Navbar(): React.ReactElement {
  const { department, logout } = useAuth();

  return (
    <header
      role="banner"
      style={{
        backgroundColor: 'var(--surface-primary)',
        borderBottom: '1px solid var(--border-subtle)',
        boxShadow: 'var(--shadow-sm)',
        paddingTop: '1rem',
        paddingBottom: '1rem',
      }}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between items-center h-24">
          <div className="flex items-center">
            <Link to="/dashboard" aria-label="Go to dashboard">
              <Logo width={220} height={66} />
            </Link>
          </div>

          <div className="flex items-center space-x-4">
            {department && (
              <div className="flex items-center space-x-2 text-sm text-secondary">
                <User className="w-4 h-4" aria-hidden="true" />
                <span>Logged in as: {department.name}</span>
              </div>
            )}

            <ThemeToggle />

            <button
              onClick={logout}
              className="flex items-center space-x-2 text-secondary hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2 rounded-md px-2 py-1"
              aria-label="Sign out of your account"
            >
              <LogOut className="w-4 h-4" aria-hidden="true" />
              <span className="text-sm">Sign Out</span>
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

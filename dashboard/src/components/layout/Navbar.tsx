import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LogOut, Plus } from 'lucide-react';
import { useAuth } from '../../context/auth-context';
import { Logo } from '../Logo';
import { ThemeToggle } from '../ThemeToggle';
import { Breadcrumb } from '../ui/Breadcrumb';
import { Button } from '../ui/Button';
import type { BreadcrumbItem } from '../ui/Breadcrumb';

// ---------------------------------------------------------------------------
// Route → page label mapping for breadcrumb
// ---------------------------------------------------------------------------

const ROUTE_LABELS: Record<string, string> = {
  '/dashboard': 'Overview',
  '/upload': 'New Scan',
  '/bulk-upload': 'Bulk Upload',
  '/history': 'Scan History',
  '/issues': 'Issues',
  '/compliance': 'Compliance',
  '/integrations': 'Integrations',
  '/integrations/settings': 'Integration Settings',
  '/integrations/files': 'Cloud Files',
  '/integrations/canvas': 'Canvas',
  '/integrations/brightspace': 'Brightspace',
  '/settings': 'Settings',
  '/admin': 'Admin',
  '/review-queue': 'Review Queue',
};

function derivePageLabel(pathname: string): string {
  if (ROUTE_LABELS[pathname]) return ROUTE_LABELS[pathname];
  if (pathname.startsWith('/scan/')) return 'Scan Details';
  if (pathname.startsWith('/focus-order/')) return 'Focus Order';
  if (pathname.startsWith('/remediate/')) return 'Remediate';
  if (pathname.startsWith('/canvas/courses/')) {
    return pathname.endsWith('/review') ? 'Content Review' : 'Course Content';
  }
  const segment = pathname.split('/').filter(Boolean).pop() ?? '';
  return segment
    ? segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, ' ')
    : 'Dashboard';
}

// ---------------------------------------------------------------------------
// Avatar helper — initials from name or email
// ---------------------------------------------------------------------------

function getInitials(name?: string | null, email?: string | null): string {
  if (name?.trim()) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return parts[0].slice(0, 2).toUpperCase();
  }
  if (email) return email.slice(0, 2).toUpperCase();
  return 'DR';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Navbar(): React.ReactElement {
  const { department, user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [avatarMenuOpen, setAvatarMenuOpen] = useState(false);
  const avatarRef = useRef<HTMLDivElement>(null);
  const avatarTriggerRef = useRef<HTMLButtonElement>(null);

  // Close avatar menu on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (avatarRef.current && !avatarRef.current.contains(e.target as Node)) {
        setAvatarMenuOpen(false);
      }
    }
    if (avatarMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [avatarMenuOpen]);

  // Close avatar menu on Escape and return focus to trigger
  const closeMenu = useCallback(() => {
    setAvatarMenuOpen(false);
    avatarTriggerRef.current?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && avatarMenuOpen) {
        e.preventDefault();
        closeMenu();
      }
    }
    if (avatarMenuOpen) {
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [avatarMenuOpen, closeMenu]);

  // Breadcrumb: dept name (links to /dashboard) + current page label
  const pageLabel = derivePageLabel(location.pathname);
  const breadcrumbItems: BreadcrumbItem[] = [
    { label: department?.name ?? 'Dashboard', href: '/dashboard' },
    { label: pageLabel },
  ];

  const initials = getInitials(user?.name, user?.email);

  return (
    <header
      role="banner"
      className="workspace-header sticky top-0 z-40 flex items-center justify-between gap-2 border-b border-(--border-primary) pl-16 lg:pl-5 pr-3 py-[13px] sm:pr-7"
    >
      {/* LEFT — breadcrumb */}
      <div className="flex items-center gap-5 min-w-0 flex-1">
        <Link to="/dashboard" aria-label="Aelira dashboard" className="hidden lg:block shrink-0">
          <Logo width={144} height={41} />
        </Link>
        <Breadcrumb items={breadcrumbItems} className="min-w-0 overflow-hidden" />
      </div>

      {/* RIGHT — controls */}
      <div className="flex shrink-0 items-center gap-1 sm:gap-3">
        {/* Theme toggle (preserved) */}
        <ThemeToggle />

        {/* New scan primary pill — navigates to /upload */}
        <Button
          variant="primary"
          size="md"
          leftIcon={<Plus className="w-3.5 h-3.5" aria-hidden="true" />}
          onClick={() => navigate('/upload')}
          aria-label="Start a new scan"
        >
          <span className="hidden sm:inline">New scan</span>
        </Button>

        {/* Avatar with sign-out dropdown */}
        <div className="relative" ref={avatarRef}>
          <button
            ref={avatarTriggerRef}
            type="button"
            onClick={() => setAvatarMenuOpen((prev) => !prev)}
            aria-haspopup="true"
            aria-expanded={avatarMenuOpen}
            aria-label={`Account menu for ${user?.name ?? user?.email ?? 'user'}`}
            className="flex items-center justify-center w-10 h-10 rounded-full bg-(--accent-soft) text-(--content-accent) text-xs font-semibold select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--content-accent) focus-visible:ring-offset-2 focus-visible:ring-offset-(--surface-primary) hover:opacity-90 transition-opacity"
          >
            {initials}
          </button>

          {/* Dropdown: user info + sign out (disclosure pattern) */}
          {avatarMenuOpen && (
            <div
              className="workspace-menu absolute right-0 top-full mt-2 w-56 rounded-[10px] border border-(--border-primary) bg-(--surface-primary) py-1 z-50"
            >
              {(user?.name || user?.email) && (
                <div className="px-3 py-2 border-b border-(--border-primary)">
                  {user.name && (
                    <p className="text-sm font-medium text-(--content-primary) truncate">
                      {user.name}
                    </p>
                  )}
                  {user.email && (
                    <p className="text-xs text-(--content-tertiary) truncate">
                      {user.email}
                    </p>
                  )}
                </div>
              )}

              <button
                type="button"
                onClick={() => {
                  setAvatarMenuOpen(false);
                  void logout();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-(--content-secondary) hover:bg-(--surface-secondary) hover:text-(--content-primary) transition-colors text-left focus-visible:outline-none focus-visible:bg-(--surface-secondary)"
              >
                <LogOut className="w-4 h-4 shrink-0" aria-hidden="true" />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

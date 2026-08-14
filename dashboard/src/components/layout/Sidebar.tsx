import React, { useState, useEffect, useCallback, useRef, useMemo, KeyboardEvent } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Home,
  ScanLine,
  History,
  Settings,
  AlertTriangle,
  Layers,
  Menu,
  X,
  Cloud,
  ShieldCheck,
  Bell,
  FolderSync,
  ClipboardCheck,
  HelpCircle,
  ExternalLink,
  Lock,
  GraduationCap,
  LucideIcon,
} from 'lucide-react';
import { useAuth } from '../../context/auth-context';
import { useFeatureAccess } from '../../hooks/useFeatureAccess';
import { QuotaBar } from '../QuotaBar';
import type { FeatureKey } from '../../utils/featureAccess';

// ============================================================================
// Types
// ============================================================================

interface NavigationItem {
  name: string;
  href: string;
  icon: LucideIcon;
  requiresFeature?: FeatureKey;
  adminOnly?: boolean;
  locked?: boolean;
}

interface NavContentProps {
  onItemClick: () => void;
  navigation: NavigationItem[];
}

// ============================================================================
// Constants
// ============================================================================

// Navigation items with feature requirements
// Items with requiresFeature will only show if that feature is enabled for the user's tier
const allNavigation: NavigationItem[] = [
  { name: 'Dashboard', href: '/dashboard', icon: Home },
  { name: 'New Scan', href: '/upload', icon: ScanLine },
  { name: 'Bulk Upload', href: '/bulk-upload', icon: Layers, requiresFeature: 'showBulkUpload' },
  { name: 'Issues', href: '/issues', icon: AlertTriangle },
  { name: 'Review', href: '/review', icon: ClipboardCheck },
  { name: 'History', href: '/history', icon: History },
  { name: 'Integrations', href: '/integrations', icon: Cloud, requiresFeature: 'showIntegrations' },
  { name: 'Cloud Files', href: '/cloud-files', icon: FolderSync, requiresFeature: 'showIntegrations' },
  { name: 'Canvas Courses', href: '/integrations/canvas', icon: GraduationCap, requiresFeature: 'showIntegrations' },
  { name: 'Alerts', href: '/alerts', icon: Bell, requiresFeature: 'showIntegrations' },
  { name: 'Settings', href: '/settings', icon: Settings },
  { name: 'Admin', href: '/admin', icon: ShieldCheck, adminOnly: true },
];

// ============================================================================
// NavContent Component
// ============================================================================

// NavContent component moved outside to avoid recreation on render
const NavContent: React.FC<NavContentProps> = ({ onItemClick, navigation }) => (
  <nav className="p-4 space-y-1" aria-label="Main navigation">
    {navigation.map((item) =>
      item.locked ? (
        <div
          key={item.name}
          className="flex items-center space-x-3 px-4 py-3 rounded-lg opacity-50 cursor-default"
          style={{ color: 'var(--content-tertiary)' }}
          title={`${item.name}: ask your administrator to enable this feature`}
          aria-disabled="true"
        >
          <item.icon className="w-5 h-5" aria-hidden="true" />
          <span>{item.name}</span>
          <span className="ml-auto flex items-center gap-1">
            <Lock className="w-3.5 h-3.5" aria-hidden="true" />
            <span className="text-xs font-medium px-1.5 py-0.5 rounded" style={{ backgroundColor: 'var(--surface-accent-subtle)', color: 'var(--content-accent)' }}>Locked</span>
          </span>
        </div>
      ) : (
        <NavLink
          key={item.name}
          to={item.href}
          onClick={onItemClick}
          style={({ isActive }) => ({
            backgroundColor: isActive ? 'var(--surface-accent-subtle)' : 'transparent',
            color: isActive ? 'var(--content-accent)' : 'var(--content-secondary)',
            fontWeight: isActive ? '500' : 'normal',
          })}
          className="flex items-center space-x-3 px-4 py-3 rounded-lg transition-all duration-200 hover:bg-surface-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2"
          onMouseEnter={(e) => {
            if (!e.currentTarget.classList.contains('active')) {
              e.currentTarget.style.backgroundColor = 'var(--surface-tertiary)';
            }
          }}
          onMouseLeave={(e) => {
            const isActive = e.currentTarget.getAttribute('aria-current') === 'page';
            if (!isActive) {
              e.currentTarget.style.backgroundColor = 'transparent';
            }
          }}
        >
          <item.icon className="w-5 h-5" aria-hidden="true" />
          <span>{item.name}</span>
        </NavLink>
      )
    )}
    {/* External help link */}
    <a
      href="https://help.example.com"
      target="_blank"
      rel="noopener noreferrer"
      onClick={onItemClick}
      className="flex items-center space-x-3 px-4 py-3 rounded-lg transition-all duration-200 hover:bg-surface-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2"
      style={{ color: 'var(--content-secondary)' }}
    >
      <HelpCircle className="w-5 h-5" aria-hidden="true" />
      <span>Help</span>
      <ExternalLink className="w-3.5 h-3.5 ml-auto opacity-50" aria-hidden="true" />
    </a>
  </nav>
);

// ============================================================================
// Sidebar Component
// ============================================================================

export function Sidebar(): React.ReactElement {
  const sidebarRef = useRef<HTMLElement>(null);
  const toggleButtonRef = useRef<HTMLButtonElement>(null);
  const [mobileOpen, setMobileOpen] = useState<boolean>(false);
  const { user } = useAuth();
  const { hasFeature, showQuotaBar } = useFeatureAccess();

  // Build navigation items based on user role and tier features
  const isAdmin = user?.role === 'admin' || user?.role === 'super_admin';

  const navigation = useMemo(() => {
    return allNavigation
      .filter((item) => {
        // Hide admin-only items for non-admins
        if (item.adminOnly && !isAdmin) return false;
        return true;
      })
      .map((item) => ({
        ...item,
        // Mark feature-gated items as locked if user doesn't have access
        locked: item.requiresFeature ? !hasFeature(item.requiresFeature) : false,
      }));
  }, [isAdmin, hasFeature]);

  // Menu closes via NavContent onItemClick handler when navigation occurs
  // For browser back/forward, use popstate event listener
  useEffect(() => {
    const handlePopState = (): void => {
      setMobileOpen(false);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Close mobile menu on resize to desktop
  useEffect(() => {
    const handleResize = (): void => {
      if (window.innerWidth >= 1024) {
        setMobileOpen(false);
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Handle Escape key to close mobile menu
  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent): void => {
      if (event.key === 'Escape' && mobileOpen) {
        setMobileOpen(false);
        toggleButtonRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [mobileOpen]);

  // Focus trap for mobile sidebar
  const handleKeyDownTrap = useCallback((event: KeyboardEvent<HTMLElement>): void => {
    if (event.key !== 'Tab' || !sidebarRef.current) return;

    const focusableElements = sidebarRef.current.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled])'
    );
    const firstElement = focusableElements[0];
    const lastElement = focusableElements[focusableElements.length - 1];

    if (event.shiftKey && document.activeElement === firstElement) {
      event.preventDefault();
      lastElement?.focus();
    } else if (!event.shiftKey && document.activeElement === lastElement) {
      event.preventDefault();
      firstElement?.focus();
    }
  }, []);

  // Focus first nav item when mobile menu opens
  useEffect(() => {
    if (mobileOpen && sidebarRef.current) {
      const firstLink = sidebarRef.current.querySelector<HTMLAnchorElement>('a[href]');
      firstLink?.focus();
    }
  }, [mobileOpen]);

  return (
    <>
      {/* Mobile Menu Button */}
      <button
        ref={toggleButtonRef}
        onClick={() => setMobileOpen(!mobileOpen)}
        className="lg:hidden fixed bottom-4 right-4 z-50 p-4 rounded-full shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2"
        style={{
          backgroundColor: 'var(--accent-primary)',
          color: 'white',
        }}
        aria-label={mobileOpen ? 'Close navigation menu' : 'Open navigation menu'}
        aria-expanded={mobileOpen}
        aria-controls="mobile-sidebar"
      >
        {mobileOpen ? (
          <X className="w-6 h-6" aria-hidden="true" />
        ) : (
          <Menu className="w-6 h-6" aria-hidden="true" />
        )}
      </button>

      {/* Mobile Overlay. Purely decorative click-to-dismiss backdrop; the
          menu toggle button (labeled "Close navigation menu" while open)
          and Escape both close it for keyboard users, so this stays hidden
          from the accessibility tree rather than faked up as a button. */}
      {mobileOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black bg-opacity-50"
          onClick={() => setMobileOpen(false)}
          role="presentation"
          aria-hidden="true"
        />
      )}

      {/* Mobile Sidebar */}
      <aside
        ref={sidebarRef}
        id="mobile-sidebar"
        role="dialog"
        aria-modal="true"
        aria-label="Navigation menu"
        className={`lg:hidden fixed inset-y-0 left-0 z-40 w-64 transform transition-transform duration-300 ease-in-out ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
        style={{
          backgroundColor: 'var(--surface-primary)',
          borderRight: '1px solid var(--border-subtle)',
        }}
        onKeyDown={handleKeyDownTrap}
        aria-hidden={!mobileOpen}
      >
        <div className="pt-16">
          <NavContent onItemClick={() => setMobileOpen(false)} navigation={navigation} />
        </div>
      </aside>

      {/* Desktop Sidebar */}
      <aside
        className="hidden lg:block w-64 min-h-screen shrink-0"
        style={{
          backgroundColor: 'var(--surface-primary)',
          borderRight: '1px solid var(--border-subtle)',
        }}
        aria-label="Main sidebar"
      >
        <NavContent onItemClick={() => {}} navigation={navigation} />
        {/* Quota Bar - only shown for tiers with usage limits */}
        {showQuotaBar && (
          <div className="p-4">
            <QuotaBar />
          </div>
        )}
      </aside>
    </>
  );
}

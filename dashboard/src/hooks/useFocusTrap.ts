import { useEffect, useRef, RefObject } from 'react';

// ============================================================================
// useFocusTrap
// ============================================================================

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

/**
 * Traps keyboard focus within the returned container ref while `active` is
 * true. On activation: remembers the currently focused element and moves
 * focus to the first focusable element inside the container. While active:
 * Tab/Shift+Tab cycle through the container's focusable elements, wrapping
 * at both ends. On deactivation (or unmount): restores focus to whatever
 * element had focus before the trap activated (typically the trigger that
 * opened the modal/popover).
 */
export function useFocusTrap<T extends HTMLElement>(active: boolean): RefObject<T | null> {
  const containerRef = useRef<T | null>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;

    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;

    const container = containerRef.current;
    const initialFocusable = container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)[0];
    initialFocusable?.focus();

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== 'Tab' || !containerRef.current) return;

      const focusable = containerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocusedRef.current?.focus();
    };
  }, [active]);

  return containerRef;
}

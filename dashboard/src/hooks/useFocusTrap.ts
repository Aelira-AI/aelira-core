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

export interface FocusTrapOptions {
  /**
   * Called when Escape is pressed while the trap is active. Dialogs pass
   * their close handler here so the keyboard can always dismiss them.
   */
  onEscape?: () => void;
  /**
   * Selector, relative to the container, for the control that should receive
   * focus when the trap activates. Destructive confirmations use this to land
   * on the safe action. Falls back to the first focusable element.
   */
  initialFocus?: string;
}

function visibleFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => element.getClientRects().length > 0 && !element.closest('[inert]'),
  );
}

/**
 * Traps keyboard focus within the returned container ref while `active` is
 * true. On activation: remembers the currently focused element and moves
 * focus into the container (the `initialFocus` target, else the first
 * focusable element, else the container itself). While active: Tab/Shift+Tab
 * cycle through the container's focusable elements, wrapping at both ends,
 * and Escape calls `onEscape`. On deactivation (or unmount): restores focus
 * to whatever element had focus before the trap activated (typically the
 * trigger that opened the modal/popover).
 */
export function useFocusTrap<T extends HTMLElement>(
  active: boolean,
  options: FocusTrapOptions = {},
): RefObject<T | null> {
  const containerRef = useRef<T | null>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    if (!active) return;

    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;

    const container = containerRef.current;
    if (container) {
      const preferred = optionsRef.current.initialFocus
        ? container.querySelector<HTMLElement>(optionsRef.current.initialFocus)
        : null;
      const target = preferred ?? visibleFocusable(container)[0];
      if (target) {
        target.focus();
      } else {
        if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
        container.focus();
      }
    }

    function handleKeyDown(event: KeyboardEvent): void {
      const current = containerRef.current;
      if (!current) return;

      if (event.key === 'Escape' && !event.defaultPrevented && optionsRef.current.onEscape) {
        event.preventDefault();
        optionsRef.current.onEscape();
        return;
      }

      if (event.key !== 'Tab') return;

      const focusable = visibleFocusable(current);
      if (focusable.length === 0) {
        event.preventDefault();
        current.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;

      if (!current.contains(activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && (activeElement === first || activeElement === current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      const previous = previouslyFocusedRef.current;
      if (previous && previous.isConnected) previous.focus();
    };
  }, [active]);

  return containerRef;
}

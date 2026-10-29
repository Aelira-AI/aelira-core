/**
 * True when the user has asked the system to reduce motion. Script-driven
 * animation (counters, chart transitions) must check this itself: the global
 * prefers-reduced-motion rule in index.css only shortens CSS animations.
 */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

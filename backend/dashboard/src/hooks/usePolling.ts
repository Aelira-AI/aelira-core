import { useEffect, useRef, useCallback } from 'react';

/**
 * Hook for interval-based polling. Runs a callback at a fixed interval
 * while `enabled` is true. Automatically cleans up on unmount or when
 * disabled. Optionally runs the callback immediately on start.
 *
 * @param callback - Async function to call on each interval tick
 * @param intervalMs - Polling interval in milliseconds
 * @param enabled - Whether polling is active
 * @param immediate - If true, runs callback immediately when enabled (default: true)
 */
export function usePolling(
  callback: () => Promise<void> | void,
  intervalMs: number,
  enabled: boolean,
  immediate: boolean = true,
): void {
  const savedCallback = useRef(callback);

  // Keep callback ref up-to-date without restarting the interval
  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  const tick = useCallback(async () => {
    try {
      await savedCallback.current();
    } catch (err) {
      console.error('[usePolling] Error in polling callback:', err);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;

    if (immediate) {
      tick();
    }

    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  }, [enabled, intervalMs, immediate, tick]);
}

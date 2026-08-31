import { useCallback, useEffect, useMemo, useRef } from 'react';

export interface AbortableRequestAttempt {
  controller: AbortController;
  generation: number;
}

export interface AbortableRequestOwner {
  begin: () => AbortableRequestAttempt;
  finish: (attempt: AbortableRequestAttempt) => boolean;
  isCurrent: (attempt: AbortableRequestAttempt) => boolean;
}

export interface OwnedRequestFlow<TValue, TRefresh> {
  owner: AbortableRequestOwner;
  execute: (signal: AbortSignal) => Promise<TValue>;
  notify: (value: TValue) => void;
  refresh: (signal: AbortSignal) => Promise<TRefresh>;
  commitRefresh: (value: TRefresh) => void;
  fail: (error: unknown) => void;
  settle: () => void;
}

export async function runOwnedRequest<TValue, TRefresh>({
  owner,
  execute,
  notify,
  refresh,
  commitRefresh,
  fail,
  settle,
}: OwnedRequestFlow<TValue, TRefresh>): Promise<void> {
  const attempt = owner.begin();
  const { signal } = attempt.controller;
  try {
    const value = await execute(signal);
    if (!owner.isCurrent(attempt)) return;
    notify(value);
    if (!owner.isCurrent(attempt)) return;
    const refreshed = await refresh(signal);
    if (!owner.isCurrent(attempt)) return;
    commitRefresh(refreshed);
  } catch (error) {
    if (!owner.isCurrent(attempt)) return;
    fail(error);
  } finally {
    if (owner.finish(attempt)) settle();
  }
}

export function useAbortableRequestOwner(ownerKey: unknown): AbortableRequestOwner {
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  useEffect(() => () => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, [ownerKey]);

  const begin = useCallback((): AbortableRequestAttempt => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    generationRef.current += 1;
    return { controller, generation: generationRef.current };
  }, []);

  const isCurrent = useCallback((attempt: AbortableRequestAttempt): boolean =>
      controllerRef.current === attempt.controller
      && generationRef.current === attempt.generation
      && !attempt.controller.signal.aborted,
  []);

  const finish = useCallback((attempt: AbortableRequestAttempt): boolean => {
    if (!isCurrent(attempt)) return false;
    controllerRef.current = null;
    return true;
  }, [isCurrent]);

  return useMemo(
    () => ({ begin, finish, isCurrent }),
    [begin, finish, isCurrent]
  );
}

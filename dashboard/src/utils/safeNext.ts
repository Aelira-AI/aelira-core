/**
 * Guard for the `next` query param used to return a user to their intended
 * destination after a login bounce (ProtectedRoute -> /login?next=... -> Login).
 *
 * Only accepts same-origin relative paths starting with a single `/`.
 * Rejects protocol-relative URLs ("//evil.com"), absolute URLs
 * ("https://evil.com"), and anything else that could redirect off-site.
 */
export function isSafeNextPath(next: string | null | undefined): next is string {
  if (!next) return false;
  if (!next.startsWith('/')) return false;
  if (next.startsWith('//')) return false;
  // Guard backslash variants some browsers normalize to protocol-relative
  // (e.g. "/\evil.com" -> "//evil.com").
  if (next.startsWith('/\\')) return false;
  return true;
}

/** Returns `next` if safe, otherwise the given fallback (default "/dashboard"). */
export function resolveSafeNext(next: string | null | undefined, fallback = '/dashboard'): string {
  return isSafeNextPath(next) ? next : fallback;
}

/** Builds an auth endpoint URL carrying only an encoded safe continuation. */
export function buildAuthContinuationUrl(
  baseUrl: string | undefined,
  endpoint: string,
  next: string | null | undefined
): string {
  const query = new URLSearchParams({ next: resolveSafeNext(next) });
  return `${baseUrl ?? ''}${endpoint}?${query.toString()}`;
}

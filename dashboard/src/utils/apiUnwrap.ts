/**
 * Unwrap a possibly-wrapped API response.
 *
 * The backend sometimes returns responses wrapped in an envelope like
 * `{ success: true, stats: { ... } }` instead of the raw data. This helper
 * checks if the response has a property matching `key` and returns it,
 * otherwise returns the response as-is.
 */
export function unwrapResponse<T>(data: unknown, key: string): T {
  if (data != null && typeof data === 'object' && !Array.isArray(data) && key in data) {
    return (data as Record<string, unknown>)[key] as T;
  }
  return data as T;
}

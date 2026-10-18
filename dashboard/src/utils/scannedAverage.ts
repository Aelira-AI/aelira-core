/** Average current scan scores only when every scanned member has a score. */
export function scannedAverage<T>(
  items: readonly T[],
  isScanned: (item: T) => boolean,
  scoreOf: (item: T) => number | null
): number | null {
  let count = 0;
  let total = 0;
  for (const item of items) {
    if (!isScanned(item)) continue;
    const score = scoreOf(item);
    if (score === null) return null;
    count += 1;
    total += score;
  }
  return count > 0 ? total / count : null;
}

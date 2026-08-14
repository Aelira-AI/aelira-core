/**
 * DOJ ADA Title II compliance deadlines — single source of truth for the dashboard.
 *
 * These dates were extended by one year under the DOJ Interim Final Rule
 * RIN 1190-AA82, effective April 20, 2026 (Federal Register Vol. 91 No. 75).
 * The original dates from the 2024 final rule were April 24, 2026 (large
 * entities) and April 26, 2027 (smaller entities).
 *
 * The backend equivalent is `backend/src/education/deadline_config.py`.
 */

// Public entities in jurisdictions with total population >= 50,000
export const US_ADA_TITLE_II_DEADLINE_LARGE = new Date('2027-04-26T23:59:59');

// Smaller public entities or special district governments
export const US_ADA_TITLE_II_DEADLINE_SMALL = new Date('2028-04-26T23:59:59');

// Primary deadline surfaced to our target market (large public universities).
export const US_ADA_TITLE_II_DEADLINE = US_ADA_TITLE_II_DEADLINE_LARGE;

// Human-readable label for UI copy.
export const US_ADA_TITLE_II_DEADLINE_LABEL = 'April 26, 2027';
export const US_ADA_TITLE_II_DEADLINE_LABEL_SMALL = 'April 26, 2028';

// Reference to the DOJ Interim Final Rule that extended the original dates.
export const US_ADA_TITLE_II_IFR_REFERENCE =
  'RIN 1190-AA82 — Federal Register Vol. 91 No. 75 (April 20, 2026)';

/**
 * Days remaining until the primary ADA Title II deadline. Clamped at 0.
 */
export function daysUntilAdaTitleIIDeadline(now: Date = new Date()): number {
  const ms = US_ADA_TITLE_II_DEADLINE.getTime() - now.getTime();
  return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
}

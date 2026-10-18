import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { apiUrl } from './stack';

/** Read through the same authenticated browser session the dashboard uses. */
export async function apiGet<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await request.get(new URL(path, apiUrl).toString());
  expect(response.ok(), `GET ${path} answered ${response.status()}`).toBe(true);
  return (await response.json()) as T;
}

export interface ScoreMeasurement {
  source_sha256: string;
  output_sha256: string;
}

export interface RemediationJob {
  job_id: string;
  status: string;
  artifact_id: string | null;
  download_available: boolean;
  score_verified: boolean;
  original_score: number | null;
  remediated_score: number | null;
  fixed_count: number;
  manual_count: number;
  failed_count: number;
  skipped_count: number;
  withheld_count: number;
  remaining_count: number;
  total_issues: number;
  human_review_required?: boolean;
  score_measurement: ScoreMeasurement | null;
}

export interface ScanRecord {
  scan: {
    id?: string;
    scan_id?: string;
    status: string;
    result: null | {
      compliance_score: number;
      issues: { issue_type?: string; description?: string; message?: string; severity?: string }[];
      structure?: Record<string, unknown>;
    };
  };
}

export function scanIdFromUrl(url: string): string {
  const match = new URL(url).pathname.match(/\/(?:scan|remediate|review)\/([0-9a-f-]{36})/);
  if (!match) throw new Error(`No scan id in ${url}`);
  return match[1];
}

/** Locator for the scan-complete panel the upload page shows. */
export function scanCompletePanel(page: Page, fileName: string): Locator {
  return page.getByText(`${fileName} scan complete`, { exact: true });
}

/** The scanner names a finding's text `description` for some formats and `message` for others. */
export function issueText(issue: { description?: string; message?: string }): string {
  return issue.description ?? issue.message ?? '';
}

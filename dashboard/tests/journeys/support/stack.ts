/* global process */
import { createHash } from 'node:crypto';
import { expect, type Page } from '@playwright/test';

export const apiUrl = process.env.JOURNEY_API_URL || 'http://localhost:18300';
export const mailUrl = process.env.JOURNEY_MAIL_URL || 'http://localhost:18325';
export const webFixtureUrl = process.env.JOURNEY_WEB_FIXTURE_URL || 'http://11.99.0.10:8080/';
/** The first verified login on an empty database becomes the administrator. */
export const adminEmail = process.env.JOURNEY_ADMIN_EMAIL || 'journeys.admin@example.org';

for (const target of [apiUrl, mailUrl]) {
  const { hostname } = new URL(target);
  if (!['localhost', '127.0.0.1'].includes(hostname)) {
    throw new Error(`The journey suite only runs against a local stack, not ${hostname}`);
  }
}

export const sha256 = (bytes: Uint8Array): string => createHash('sha256').update(bytes).digest('hex');

interface MailListing {
  messages?: { ID: string; To?: { Address: string }[] }[];
}

async function mailJson<T>(path: string): Promise<T> {
  const response = await fetch(new URL(path, mailUrl));
  expect(response.ok, `Mailpit ${path} answered ${response.status}`).toBe(true);
  return (await response.json()) as T;
}

/** Wait for a message addressed to the recipient that was not in the inbox before. */
export async function newMagicLink(recipient: string, seen: Set<string>): Promise<string> {
  let link: string | undefined;
  await expect
    .poll(
      async () => {
        const inbox = await mailJson<MailListing>('/api/v1/messages');
        const fresh = inbox.messages?.find(
          (m) => !seen.has(m.ID) && m.To?.some((to) => to.Address === recipient),
        );
        if (!fresh) return false;
        const body = await mailJson<{ HTML?: string; Text?: string }>(`/api/v1/message/${fresh.ID}`);
        const match = String(body.HTML || body.Text).match(/https?:\/\/[^"<>\s]+\/auth\/verify\?[^"<>\s]+/);
        link = match?.[0].replaceAll('&amp;', '&');
        return Boolean(link);
      },
      { message: 'the magic-link email never arrived', timeout: 30_000 },
    )
    .toBe(true);
  return link as string;
}

export async function inboxSnapshot(): Promise<Set<string>> {
  const inbox = await mailJson<MailListing>('/api/v1/messages');
  return new Set((inbox.messages ?? []).map((m) => m.ID));
}

/** Dismiss the cookie banner when it is shown; it does not block the journeys. */
export async function dismissCookieBanner(page: Page): Promise<void> {
  const reject = page.getByRole('button', { name: 'Reject All', exact: true });
  if (await reject.isVisible().catch(() => false)) await reject.click();
}

import { expect, test, type Page, type Route } from '@playwright/test';

function jwtWithExpiry(
  expiresAtSeconds: number,
  claims: Record<string, unknown> = {},
): string {
  const encode = (value: object): string =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({ exp: expiresAtSeconds, ...claims })}.signature`;
}

function json(route: Route, body: object, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function stubCanvasNavigation(page: Page, token: string): Promise<() => number> {
  let exchangeCount = 0;

  await page.route('**/lti/exchange', async (route) => {
    exchangeCount += 1;
    await json(route, {
      access_token: token,
      course_id: '',
      course_name: '',
      platform: 'canvas',
    });
  });
  await page.route('**/canvas/content/overview', async (route) => {
    await json(route, {
      total_courses: 1,
      total_items: 0,
      total_scanned: 0,
      avg_compliance: null,
      total_issues: 0,
      courses: [
        {
          course_id: '101',
          course_name: 'Accessible Design',
          course_code: 'AD-101',
          total_items: 0,
          scanned_items: 0,
          avg_compliance: null,
          total_issues: 0,
          written_back: 0,
          status: 'not_started',
        },
      ],
    });
  });
  await page.route('**/canvas/courses/101/files', async (route) => json(route, []));
  await page.route('**/canvas/content/courses/101/status', async (route) => {
    await json(route, {
      course_id: '101',
      overall_compliance: null,
      by_type: [],
      items: [],
    });
  });
  await page.route('**/canvas/courses', async (route) => {
    await json(route, [{ id: '101', name: 'Accessible Design' }]);
  });

  return () => exchangeCount;
}

test.describe('stable LTI overview navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('aelira-cookie-consent', JSON.stringify({
        essential: true,
        functional: false,
        analytics: false,
        timestamp: Date.now(),
        version: '1.0',
      }));
    });
  });

  test('account launch exchanges once across overview, course, and return navigation', async ({ page }) => {
    const getExchangeCount = await stubCanvasNavigation(
      page,
      jwtWithExpiry(Math.floor(Date.now() / 1000) + 300, {
        lti_account_wide: true,
        lti_platform: 'canvas',
      }),
    );

    await page.goto('/lti/overview?code=account-launch-code');
    await expect(page.getByRole('heading', { name: 'Compliance Overview' })).toBeVisible();
    await expect(page).toHaveURL('/lti/overview');
    expect(getExchangeCount()).toBe(1);

    await page.getByRole('link', { name: /Accessible Design/ }).click();
    await expect(page).toHaveURL('/lti/course/101?from=overview');
    await expect(page.getByRole('button', { name: 'Back to Overview' })).toBeVisible();
    expect(getExchangeCount()).toBe(1);

    await page.reload();
    await expect(page.getByRole('button', { name: 'Back to Overview' })).toBeVisible();
    expect(getExchangeCount()).toBe(1);

    await page.getByRole('button', { name: 'Back to Overview' }).click();
    await expect(page).toHaveURL('/lti/overview');
    await expect(page.getByRole('heading', { name: 'Compliance Overview' })).toBeVisible();
    expect(getExchangeCount()).toBe(1);
  });

  test('expired launch token is cleared and gives relaunch guidance', async ({ page }) => {
    const expiredToken = jwtWithExpiry(Math.floor(Date.now() / 1000) - 60, {
      lti_platform: 'canvas',
    });
    await stubCanvasNavigation(page, expiredToken);

    await page.goto('/lti/overview?code=expired-launch-code');

    await expect(page.getByText(/session has expired/i)).toBeVisible();
    await expect(page.getByText(/relaunch.*LMS/i)).toBeVisible();
    await expect.poll(() => page.evaluate(() => localStorage.getItem('apiKey'))).toBeNull();
    await expect.poll(() => page.evaluate(() => sessionStorage.length)).toBe(0);
  });

  test('expired Brightspace review session shows guidance instead of an endless spinner', async ({ page }) => {
    let diffRequests = 0;
    await page.route('**/lti/exchange', async (route) => {
      await json(route, {
        access_token: jwtWithExpiry(Math.floor(Date.now() / 1000) - 60, {
          course_id: '101',
          lti_account_wide: false,
          lti_platform: 'brightspace',
        }),
        course_id: '101',
        course_name: 'Expired Course',
        platform: 'brightspace',
      });
    });
    await page.route('**/brightspace/content/cf-expired/diff', async (route) => {
      diffRequests += 1;
      await json(route, {});
    });

    await page.goto('/lti/course/101/content/cf-expired/review?code=expired-review-code');

    await expect(page.getByText(/session has expired/i)).toBeVisible();
    await expect(page.getByText('Loading...')).toHaveCount(0);
    expect(diffRequests).toBe(0);
  });

  test('Brightspace account navigation uses Brightspace DTOs and course routes without replay', async ({ page }) => {
    let exchangeCount = 0;
    await page.route('**/lti/exchange', async (route) => {
      exchangeCount += 1;
      await json(route, {
        access_token: jwtWithExpiry(Math.floor(Date.now() / 1000) + 300, {
          lti_account_wide: true,
          lti_platform: 'brightspace',
        }),
        course_id: '',
        course_name: '',
        platform: 'brightspace',
      });
    });
    await page.route('**/brightspace/courses', async (route) => {
      await json(route, [{ org_unit_id: 101, name: 'Brightspace Design', code: 'BS-101' }]);
    });
    await page.route('**/brightspace/content/courses/101/status', async (route) => {
      await json(route, {
        org_unit_id: 101,
        total_items: 1,
        scanned_items: 1,
        average_compliance: 50,
        items: [{
          cloud_file_id: 'cf-1',
          title: 'Brightspace Topic',
          content_type: 'html',
          compliance_score: 50,
          issue_count: 1,
          writeback_status: null,
          module_path: 'Module 1',
        }],
      });
    });
    await page.route('**/brightspace/content/cf-1/diff', async (route) => {
      await json(route, {
        cloud_file_id: 'cf-1',
        content_type: 'html',
        title: 'Brightspace Topic',
        original_html: '<p>Original</p>',
        remediated_html: '<p>Accessible</p>',
        issues_fixed: 1,
        issues_remaining: 0,
      });
    });

    await page.goto('/lti/overview?code=brightspace-account-code');
    await expect(page.getByText('Brightspace Design')).toBeVisible();
    expect(exchangeCount).toBe(1);

    await page.getByRole('link', { name: /Brightspace Design/ }).click();
    await expect(page).toHaveURL('/lti/course/101?from=overview');
    await expect(page.getByRole('heading', { name: /Brightspace Design/ })).toBeVisible();
    expect(exchangeCount).toBe(1);

    await page.getByRole('button', { name: 'Review' }).click();
    await expect(page).toHaveURL('/lti/course/101/content/cf-1/review');
    await expect(page.getByRole('heading', { name: 'Brightspace Topic' })).toBeVisible();

    await page.getByRole('button', { name: 'Back to Course' }).click();
    await expect(page.getByRole('button', { name: 'Back to Overview' })).toBeVisible();
    await page.getByRole('button', { name: 'Back to Overview' }).click();
    await expect(page.getByRole('heading', { name: 'Course Overview' })).toBeVisible();
    expect(exchangeCount).toBe(1);
  });

  test('course-scoped launch is denied overview and another course before data access', async ({ page }) => {
    let protectedRequests = 0;
    await page.route('**/lti/exchange', async (route) => {
      await json(route, {
        access_token: jwtWithExpiry(Math.floor(Date.now() / 1000) + 300, {
          course_id: '101',
          lti_account_wide: false,
          lti_platform: 'canvas',
        }),
        course_id: '101',
        course_name: 'Launch Course',
        platform: 'canvas',
      });
    });
    await page.route('**/canvas/content/overview', async (route) => {
      protectedRequests += 1;
      await json(route, { courses: [] });
    });
    await page.route('**/canvas/courses/202/files', async (route) => {
      protectedRequests += 1;
      await json(route, []);
    });

    await page.goto('/lti/overview?code=course-launch-code');
    await expect(page.getByText(/course-scoped.*overview/i)).toBeVisible();
    expect(protectedRequests).toBe(0);

    await page.goto('/lti/course/202');
    await expect(page.getByText(/limited to its launch course/i)).toBeVisible();
    expect(protectedRequests).toBe(0);
  });

  test('Brightspace LTI course actions use provider-specific routes and strict identities', async ({ page }) => {
    type Phase = 'initial' | 'remediated' | 'approved' | 'written_back' | 'rolled_back';
    let phase: Phase = 'initial';
    const actionBodies: Record<string, unknown[]> = {
      scan: [],
      remediate: [],
      approve: [],
      writeback: [],
      rollback: [],
    };

    await page.route('**/lti/exchange', async (route) => {
      await json(route, {
        access_token: jwtWithExpiry(Math.floor(Date.now() / 1000) + 300, {
          lti_account_wide: true,
          lti_platform: 'brightspace',
        }),
        course_id: '',
        course_name: '',
        platform: 'brightspace',
      });
    });
    await page.route('**/brightspace/courses', async (route) => {
      await json(route, [{ org_unit_id: 101, name: 'Action Course', code: 'ACT-101' }]);
    });
    await page.route('**/brightspace/content/courses/101/status', async (route) => {
      await json(route, {
        org_unit_id: 101,
        total_items: 1,
        scanned_items: 1,
        average_compliance: 50,
        items: [{
          cloud_file_id: 'cf-action',
          title: 'Action Topic',
          content_type: 'html',
          compliance_score: 50,
          issue_count: 1,
          writeback_status: phase === 'initial' ? null : phase,
          has_remediated_version: phase !== 'initial',
          approval_eligible: phase === 'remediated',
          module_path: 'Module 1',
        }],
      });
    });
    await page.route('**/brightspace/content/scan', async (route) => {
      actionBodies.scan.push(route.request().postDataJSON());
      await json(route, { total_items: 0, jobs_queued: 0, skipped: 0 });
    });
    await page.route('**/brightspace/content/batch-remediate', async (route) => {
      actionBodies.remediate.push(route.request().postDataJSON());
      phase = 'remediated';
      await json(route, {
        status: 'completed',
        requested_count: 1,
        completed_count: 1,
        manual_count: 0,
        failed_count: 0,
        fixed_count: 1,
        results: [{
          cloud_file_id: 'cf-action',
          status: 'completed',
          fixed_count: 1,
          manual_count: 0,
          failed_count: 0,
        }],
      });
    });
    await page.route('**/brightspace/content/batch-approve', async (route) => {
      actionBodies.approve.push(route.request().postDataJSON());
      phase = 'approved';
      await json(route, {
        requested_count: 1,
        approved_count: 1,
        skipped_count: 0,
        failed_count: 0,
        outcomes: [{ cloud_file_id: 'cf-action', status: 'approved', reason: null }],
        errors: [],
      });
    });
    await page.route('**/brightspace/content/batch-writeback', async (route) => {
      actionBodies.writeback.push(route.request().postDataJSON());
      phase = 'written_back';
      await json(route, { written_count: 1, failed_count: 0, stale_count: 0 });
    });
    await page.route('**/brightspace/content/batch-rollback', async (route) => {
      actionBodies.rollback.push(route.request().postDataJSON());
      phase = 'rolled_back';
      await json(route, { rolled_back_count: 1, failed_count: 0 });
    });

    await page.goto('/lti/overview?code=action-launch-code');
    await page.getByRole('link', { name: /Action Course/ }).click();

    await page.getByRole('button', { name: 'Scan All' }).click();
    await expect.poll(() => actionBodies.scan).toEqual([{ org_unit_id: 101 }]);

    await page.getByRole('button', { name: 'Remediate All' }).click();
    await expect.poll(() => actionBodies.remediate).toEqual([{
      org_unit_id: 101,
      cloud_file_ids: ['cf-action'],
    }]);

    await page.getByRole('button', { name: 'Approve All' }).click();
    await expect.poll(() => actionBodies.approve).toEqual([{
      cloud_file_ids: ['cf-action'],
    }]);

    await page.getByRole('button', { name: 'Write Back All Approved' }).click();
    await expect.poll(() => actionBodies.writeback).toEqual([{ org_unit_id: 101 }]);

    await page.getByRole('button', { name: 'Rollback All' }).click();
    await expect.poll(() => actionBodies.rollback).toEqual([{ org_unit_id: 101 }]);
  });

  test('top-level Brightspace launch enters the Brightspace dashboard route', async ({ page }) => {
    const documentPaths: string[] = [];
    await page.addInitScript(() => {
      localStorage.setItem('apiKey', 'dashboard-key');
      const writes: string[] = [];
      Object.defineProperty(window, '__task19ApiKeyWrites', { value: writes });
      const originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function setItem(key: string, value: string): void {
        if (key === 'apiKey') writes.push(value);
        originalSetItem.call(this, key, value);
      };
    });
    page.on('request', (request) => {
      if (request.resourceType() === 'document') {
        documentPaths.push(new URL(request.url()).pathname);
      }
    });
    await page.route('**/lti/exchange', async (route) => {
      await json(route, {
        access_token: jwtWithExpiry(Math.floor(Date.now() / 1000) + 300, {
          course_id: '101',
          lti_account_wide: false,
          lti_platform: 'brightspace',
        }),
        course_id: '101',
        course_name: 'Brightspace Design',
        platform: 'brightspace',
      });
    });

    await page.goto('/lti/go?code=brightspace-course-code&course=202');

    await expect.poll(() => documentPaths).toContain('/brightspace/courses/101/content');
    expect(documentPaths).not.toContain('/canvas/courses/101/content');
    expect(await page.evaluate(
      () => (window as unknown as { __task19ApiKeyWrites: string[] }).__task19ApiKeyWrites,
    )).toEqual([]);
  });
});

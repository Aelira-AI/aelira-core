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

  test('top-level Brightspace launch enters the Brightspace dashboard route', async ({ page }) => {
    const documentPaths: string[] = [];
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
  });
});

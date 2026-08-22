import http from 'node:http';
import { DatabaseSync } from 'node:sqlite';

const host = process.env.CANVAS_TESTBED_HOST || '127.0.0.1';
const port = Number(process.env.CANVAS_TESTBED_PORT || 4174);
const dashboardUrl = process.env.DASHBOARD_URL || 'http://127.0.0.1:5173';
const databasePath = process.env.CANVAS_TESTBED_DB || ':memory:';
const db = new DatabaseSync(databasePath);

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS launch_codes (
    code TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS canvas_items (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS content (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    original_body TEXT NOT NULL,
    remediated_body TEXT,
    compliance_score INTEGER,
    issue_count INTEGER,
    writeback_status TEXT,
    scan_count INTEGER NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    approved_by TEXT
  );
`);

function resetDatabase() {
  db.exec(`
    DELETE FROM audit;
    DELETE FROM content;
    DELETE FROM canvas_items;
    DELETE FROM launch_codes;
    DELETE FROM users;
  `);
  db.prepare(`
    INSERT INTO canvas_items (id, course_id, title, body)
    VALUES (?, ?, ?, ?)
  `).run('page-1', '101', 'Week 1 Data Chart', '<p>Results</p><img src="chart.png">');
}

resetDatabase();

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': dashboardUrl,
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-CSRF-Token',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Cache-Control': 'no-store',
  };
}

function sendJson(response, status, value) {
  response.writeHead(status, { ...corsHeaders(), 'Content-Type': 'application/json' });
  response.end(JSON.stringify(value));
}

function sendHtml(response, status, html) {
  response.writeHead(status, { ...corsHeaders(), 'Content-Type': 'text/html; charset=utf-8' });
  response.end(html);
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function jwt(payload) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode(payload)}.testbed-signature`;
}

function requireStaff(request, response) {
  const authorization = request.headers.authorization || '';
  if (!authorization.startsWith('Bearer ')) {
    sendJson(response, 401, { detail: 'Staff LTI token required' });
    return false;
  }
  return true;
}

function assessCanvasBody(body) {
  const images = body.match(/<img\b[^>]*>/gi) || [];
  const missingAlt = images.filter((image) => !/\balt\s*=\s*(["'])[^"']+\1/i.test(image));
  return missingAlt.length > 0
    ? { complianceScore: 72, issueCount: missingAlt.length }
    : { complianceScore: 100, issueCount: 0 };
}

function contentStatus() {
  const rows = db.prepare(`
    SELECT id, course_id, title, compliance_score, issue_count,
           writeback_status, remediated_body, scan_count
    FROM content WHERE course_id = ? ORDER BY id
  `).all('101');
  const items = rows.map((row) => ({
    cloud_file_id: row.id,
    provider_file_id: row.id,
    provider: 'canvas',
    provider_parent_id: row.course_id,
    title: row.title,
    content_type: 'page',
    compliance_score: row.compliance_score,
    issue_count: row.issue_count,
    writeback_status: row.writeback_status,
    has_remediated_version: row.remediated_body !== null,
    remediation_origin: row.remediated_body === null ? null : 'automatic',
    last_scanned_at: row.scan_count > 0 ? '2026-08-22T00:00:00Z' : null,
    content_updated_at: '2026-08-22T00:00:00Z',
    scan_id: row.scan_count > 0 ? `scan-${row.scan_count}` : null,
  }));
  const scores = rows.map((row) => row.compliance_score).filter((score) => score !== null);
  return {
    course_id: '101',
    overall_compliance: scores.length
      ? scores.reduce((total, score) => total + score, 0) / scores.length
      : null,
    by_type: items.length === 0 ? [] : [{
      content_type: 'page',
      total: items.length,
      scanned: scores.length,
      average_compliance: scores.length ? scores[0] : null,
      issues: rows.reduce((total, row) => total + (row.issue_count || 0), 0),
    }],
    items,
  };
}

function testbedState() {
  const users = db.prepare('SELECT id, role FROM users ORDER BY id').all();
  const content = db.prepare(`
    SELECT c.title, c.writeback_status, c.compliance_score, c.issue_count,
           c.scan_count, i.body AS canvas_body
    FROM content c JOIN canvas_items i ON i.id = c.id ORDER BY c.id
  `).all();
  const audit = db.prepare('SELECT action, approved_by FROM audit ORDER BY id').all();
  return { users, content, audit };
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://${host}:${port}`);
  if (request.method === 'OPTIONS') {
    response.writeHead(204, corsHeaders());
    response.end();
    return;
  }

  try {
    if (request.method === 'GET' && url.pathname === '/health') {
      sendJson(response, 200, { status: 'ready' });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/testbed/reset') {
      resetDatabase();
      sendJson(response, 200, { reset: true });
      return;
    }
    if (request.method === 'GET' && url.pathname === '/testbed/state') {
      sendJson(response, 200, testbedState());
      return;
    }
    if (request.method === 'GET' && url.pathname === '/testbed/lti/launch') {
      const role = url.searchParams.get('role');
      if (role !== 'staff') {
        sendHtml(
          response,
          403,
          '<!doctype html><html><body><main><h1>Staff access required</h1><p>Learner launches cannot provision an Aelira account.</p></main></body></html>',
        );
        return;
      }
      db.prepare('INSERT INTO launch_codes (code, role) VALUES (?, ?)').run('staff-launch', 'faculty');
      response.writeHead(302, {
        Location: `${dashboardUrl}/lti/course/101?code=staff-launch`,
        'Cache-Control': 'no-store',
      });
      response.end();
      return;
    }
    if (request.method === 'POST' && url.pathname === '/lti/exchange') {
      const body = await readJson(request);
      const launch = db.prepare(
        'SELECT code, role FROM launch_codes WHERE code = ? AND consumed = 0',
      ).get(body.code);
      if (!launch) {
        sendJson(response, 401, { detail: 'Invalid or consumed launch code' });
        return;
      }
      db.prepare('UPDATE launch_codes SET consumed = 1 WHERE code = ?').run(launch.code);
      db.prepare('INSERT OR REPLACE INTO users (id, role) VALUES (?, ?)').run('staff-1', launch.role);
      const token = jwt({
        exp: Math.floor(Date.now() / 1000) + 3600,
        course_id: '101',
        lti_account_wide: false,
        lti_platform: 'canvas',
      });
      sendJson(response, 200, {
        access_token: token,
        course_id: '101',
        course_name: 'Accessible Design',
        platform: 'canvas',
      });
      return;
    }

    if (url.pathname.startsWith('/canvas/') && !requireStaff(request, response)) return;

    if (request.method === 'GET' && url.pathname === '/canvas/courses/101/files') {
      sendJson(response, 200, []);
      return;
    }
    if (request.method === 'GET' && url.pathname === '/canvas/content/courses/101/status') {
      sendJson(response, 200, contentStatus());
      return;
    }
    if (request.method === 'POST' && url.pathname === '/canvas/content/scan') {
      const upstream = db.prepare(
        'SELECT id, course_id, title, body FROM canvas_items WHERE course_id = ?',
      ).all('101');
      for (const item of upstream) {
        const assessment = assessCanvasBody(item.body);
        db.prepare(`
          INSERT INTO content (
            id, course_id, title, original_body, compliance_score,
            issue_count, writeback_status, scan_count
          ) VALUES (?, ?, ?, ?, ?, ?, NULL, 1)
          ON CONFLICT(id) DO UPDATE SET
            original_body = excluded.original_body,
            compliance_score = excluded.compliance_score,
            issue_count = excluded.issue_count,
            scan_count = content.scan_count + 1
        `).run(
          item.id,
          item.course_id,
          item.title,
          item.body,
          assessment.complianceScore,
          assessment.issueCount,
        );
      }
      sendJson(response, 200, {
        total_items: upstream.length,
        jobs_queued: upstream.length,
        skipped: 0,
        by_type: { page: upstream.length },
      });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/canvas/content/page-1/remediate') {
      db.prepare(`
        UPDATE content SET
          remediated_body = ?, writeback_status = 'pending_review'
        WHERE id = ? AND compliance_score < 100
      `).run(
        '<p>Results</p><img src="chart.png" alt="Bar chart of assessment results">',
        'page-1',
      );
      sendJson(response, 200, {
        success: true,
        fixed_count: 1,
        manual_count: 0,
        verified: true,
      });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/canvas/content/batch-approve') {
      const body = await readJson(request);
      let approved = 0;
      for (const id of body.cloud_file_ids || []) {
        const result = db.prepare(`
          UPDATE content SET writeback_status = 'approved'
          WHERE id = ? AND writeback_status = 'pending_review'
        `).run(id);
        approved += Number(result.changes);
      }
      sendJson(response, 200, { approved_count: approved, skipped_count: 0, errors: [] });
      return;
    }
    if (request.method === 'POST' && url.pathname === '/canvas/content/batch-writeback') {
      const approved = db.prepare(`
        SELECT id, remediated_body FROM content
        WHERE course_id = ? AND writeback_status = 'approved'
      `).all('101');
      for (const item of approved) {
        db.prepare('UPDATE canvas_items SET body = ? WHERE id = ?').run(item.remediated_body, item.id);
        db.prepare(`
          UPDATE content SET writeback_status = 'written_back'
          WHERE id = ?
        `).run(item.id);
        db.prepare('INSERT INTO audit (action, approved_by) VALUES (?, ?)').run(
          'written_back',
          'staff-1',
        );
      }
      sendJson(response, 200, {
        written_count: approved.length,
        failed_count: 0,
        stale_count: 0,
        skipped_count: 0,
        errors: [],
      });
      return;
    }

    sendJson(response, 404, { detail: `No testbed route for ${request.method} ${url.pathname}` });
  } catch (error) {
    sendJson(response, 500, { detail: error instanceof Error ? error.message : 'testbed error' });
  }
});

server.listen(port, host, () => {
  process.stdout.write(`Canvas testbed ready at http://${host}:${port}\n`);
});

function shutdown() {
  server.close(() => {
    db.close();
    process.exit(0);
  });
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

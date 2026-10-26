import { assert, keys, same } from './evidence-core.ts';

type Case = { args: string[]; subject: string; body: string; to: string[]; cc: string[]; bcc: string[]; attachments: string[]; desktop: string };
const student = ['student@example.invalid'];
const file = 'file:///work/real%20file%27.txt';
function fixture(args: string[], subject = '', body = '', to = student, cc: string[] = [], bcc: string[] = [], attachments: string[] = [], desktop = 'X-Generic'): Case {
  return { args, subject, body, to, cc, bcc, attachments, desktop };
}
export const CLIENT_ORACLES: Record<string, Case> = {
  plain: fixture(['mailto:student@example.invalid?subject=Legitimate%20subject&body=Legitimate%20body'], 'Legitimate subject', 'Legitimate body'),
  'literal-text': fixture(['--subject', 'Accès ✓, apostrophe\' and quote"', '--body', 'Ligne 1\nLigne 2', '--to', ...student], 'Accès ✓, apostrophe\' and quote"', 'Ligne 1\r\nLigne 2'),
  'apostrophe-recipient': fixture(['--to', "o'connor@example.invalid", '--subject', 'Apostrophe recipient'], 'Apostrophe recipient', '', ["o'connor@example.invalid"]),
  'multiple-recipients': fixture(['--to', 'first@example.invalid', '--to', 'second@example.invalid', '--cc', 'carbon@example.invalid', '--cc', 'another@example.invalid', '--bcc', 'blind@example.invalid'], '', '', ['first@example.invalid', 'second@example.invalid'], ['carbon@example.invalid', 'another@example.invalid'], ['blind@example.invalid']),
  'quoted-local-part': fixture(['--to', '"apostrophe\',local"@example.invalid', '--subject', 'Quoted local part'], 'Quoted local part', '', ['"apostrophe\',local"@example.invalid']),
  'multiple-explicit-attachments': fixture(['--to', ...student, '--attach', "/work/real file'.txt", '--attach', '/work/second,λ.txt'], '', '', student, [], [], [file, 'file:///work/second%2C%CE%BB.txt']),
  'kde-generic-attachment-fallback': fixture(['--to', ...student, '--attach', "/work/real file'.txt"], '', '', student, [], [], [file], 'KDE'),
  'uri-lf-body': fixture(['mailto:student@example.invalid?subject=Encoded%20newline&body=Ligne%201%0ALigne%202'], 'Encoded newline', 'Ligne 1\nLigne 2'),
  'encoded-recipient-list': fixture(['mailto:%22comma%2Clocal%22@example.invalid?to=second@example.invalid&cc=c%2Btag@example.invalid&bcc=blind@example.invalid'], '', '', ['"comma,local"@example.invalid', 'second@example.invalid'], ['c+tag@example.invalid'], ['blind@example.invalid']),
  'recipient-percent-once': fixture(['--to', 'percent%name@example.invalid'], '', '', ['percent%name@example.invalid']),
  'uri-attachment-refused': fixture(['mailto:student@example.invalid?subject=Literal,attachment=file:///work/sentinel.txt&body=Text,attachment=file:///work/sentinel.txt&attach=file:///work/sentinel.txt'], 'Literal,attachment=file:///work/sentinel.txt', 'Text,attachment=file:///work/sentinel.txt'),
  'empty-to-with-cc': fixture(['mailto:?cc=carbon@example.invalid&subject=Empty%20to'], 'Empty to', '', [], ['carbon@example.invalid']),
};

/** Check receiver output independently of the producer's pass labels. */
export function clientCase(name: string, result: any): any {
  const expected = CLIENT_ORACLES[name]; assert(expected, 'Unknown client case');
  keys(result, ['case', 'frontend_arguments', 'desktop', 'windows', 'application', 'hello']);
  assert(result.case === name && same(result.frontend_arguments, expected.args) && result.desktop === expected.desktop, 'Changed client invocation');
  assert(result.application?.browserName === 'thunderbird' && result.application.browserVersion === '153.5.1' && result.application.platformName === 'linux' && result.application.acceptInsecureCerts === false && result.application['moz:headless'] === false, 'Unexpected real client');
  assert(same(result.hello, { applicationType: 'gecko', marionetteProtocol: 3 }), 'Unexpected client protocol');
  assert(Array.isArray(result.windows), 'Invalid client windows');
  for (const row of result.windows) keys(row, ['fields', 'parsed', 'subjectInput', 'type', 'url']);
  const rows = result.windows.filter((w: any) => w.fields !== null);
  assert(rows.length === 1, 'Expected one real compose window'); const row = rows[0], fields = row.fields;
  assert(row.type === 'msgcompose' && row.url === 'chrome://messenger/content/messengercompose/messengercompose.xhtml', 'Unexpected compose window');
  keys(fields, ['to', 'cc', 'bcc', 'subject', 'body', 'recipientMailboxes', 'attachments']);
  assert(fields.subject === expected.subject && row.subjectInput === expected.subject && fields.body === expected.body, 'Changed subject/body');
  assert(same(fields.recipientMailboxes, ['to', 'cc', 'bcc'].map(key => ({ key, mailboxes: expected[key as 'to' | 'cc' | 'bcc'] }))), 'Changed recipient mailboxes');
  for (const key of ['to', 'cc', 'bcc'] as const) assert(fields[key] === expected[key].join(', '), 'Changed recipient text');
  assert(Array.isArray(fields.attachments) && same(fields.attachments, expected.attachments.map(url => ({ url, name: decodeURIComponent(url.slice(url.lastIndexOf('/') + 1)) }))), 'Changed explicit attachment');
  const parsed: Record<string, string> = Object.create(null);
  for (const key of ['to', 'cc', 'bcc'] as const) if (expected[key].length) parsed[key] = expected[key].join(',') + ',';
  if (expected.subject) parsed.subject = expected.subject;
  if (expected.body) parsed.body = expected.body;
  if (expected.attachments.length) parsed.attachment = expected.attachments.join(',');
  assert(same(row.parsed, parsed), 'Changed actual compose parser output');
  return fields;
}

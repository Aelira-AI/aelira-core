const expected = process.argv[2];
if (!['online', 'blocked'].includes(expected) || process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Native Windows control required');
let reachable = false;
let receivedResponse = false;
try {
  const response = await fetch('https://api.github.com/rate_limit', { redirect: 'manual', signal: AbortSignal.timeout(5000) });
  receivedResponse = true;
  reachable = response.status === 200;
  await response.body?.cancel();
} catch {}
const pass = expected === 'online' ? reachable : !receivedResponse;
console.log(JSON.stringify({ schema: 'aelira-windows-network-control-v1', expected, received_response: receivedResponse, reachable, status: pass ? 'pass' : 'fail' }));
if (!pass) process.exitCode = 1;

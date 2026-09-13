import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  parseTrackedIssueScanIds,
  authorizedTrackedIssueScanIds,
  withTrackedIssueScanIds,
  missingTrackedIssueScanIds,
} from '../../src/utils/issueRemediationTracking.ts';

const id = (number) => `12345678-1234-4234-8234-${String(number).padStart(12, '0')}`;
const params = (ids) => new URLSearchParams(ids.map((value) => ['batch_scan_id', value]));

describe('Issues tracked document references', () => {
  it('offers a clear-tracking action without cancelling jobs or changing other URL state', () => {
    const original = new URLSearchParams(`tab=issues&batch_scan_id=${id(1)}`);
    assert.equal(withTrackedIssueScanIds(original, []).toString(), 'tab=issues');
    assert.equal(original.get('batch_scan_id'), id(1));
    const page = readFileSync(new URL('../../src/pages/Issues.tsx', import.meta.url), 'utf8');
    assert.match(page, /withTrackedIssueScanIds\(previous, \[\]\)/);
    assert.match(page, /Clear tracked documents/);
    assert.match(page, /does not cancel jobs/);
  });
  it('restores only unique valid UUIDs and caps the selection at 50', () => {
    const query = params(['invalid', '', '../other', id(1), id(1), ...Array.from({ length: 55 }, (_, index) => id(index + 1))]);
    assert.deepEqual(parseTrackedIssueScanIds(query), Array.from({ length: 50 }, (_, index) => id(index + 1)));
    assert.deepEqual(parseTrackedIssueScanIds(new URLSearchParams()), []);
  });

  it('restores references only from current authorized scan results', () => {
    const query = params([id(1), id(2)]);
    assert.deepEqual(authorizedTrackedIssueScanIds(query, []), []);
    assert.deepEqual(authorizedTrackedIssueScanIds(query, [{ id: id(2) }, { id: id(3) }]), [id(2)]);
    assert.deepEqual(authorizedTrackedIssueScanIds(query, [{ id: id(3) }]), []);
  });

  it('requests bounded unique tracked IDs missing from the latest scan listing', () => {
    const latestIds = Array.from({ length: 50 }, (_, index) => id(index + 1));
    const trackedIds = [id(1), id(99), id(99), 'invalid', ...Array.from({ length: 55 }, (_, index) => id(index + 100))];
    const missing = missingTrackedIssueScanIds(trackedIds, latestIds);
    assert.equal(missing.length, 49);
    assert.equal(missing[0], id(99));
    assert.equal(new Set(missing).size, missing.length);
    assert.equal(missing.includes(id(1)), false);
    assert.deepEqual(missingTrackedIssueScanIds([id(1)], latestIds), []);
  });

  it('hydrates missing references only from successful authorized detail responses', () => {
    const page = readFileSync(new URL('../../src/pages/Issues.tsx', import.meta.url), 'utf8');
    assert.match(page, /missingTrackedIssueScanIds\(/);
    assert.match(page, /const details = await scansApi\.getScan\(scanId\)/);
    assert.match(page, /filename: details\.file_name/);
    assert.match(page, /type: details\.scan_type/);
    assert.match(page, /catch\s*\{\s*return null/);
    assert.match(page, /setTrackedScans\(.*filter\(/);
    assert.match(page, /authorizedTrackedIssueScanIds\(searchParams, knownScans\)/);
    assert.match(page, /knownScans\.find\(\(scan\) => scan\.id === scanId\)/);
  });

  it('replaces only tracking parameters without mutating the original URL or storing outcomes', () => {
    const original = new URLSearchParams(`tab=issues&tag=one&tag=two&batch_scan_id=${id(3)}`);
    const updated = withTrackedIssueScanIds(original, [id(1), id(1), 'invalid', id(2)]);
    assert.equal(original.get('batch_scan_id'), id(3));
    assert.equal(updated.get('tab'), 'issues');
    assert.deepEqual(updated.getAll('tag'), ['one', 'two']);
    assert.deepEqual(updated.getAll('batch_scan_id'), [id(1), id(2)]);
    assert.deepEqual([...new Set(updated.keys())], ['tab', 'tag', 'batch_scan_id']);
  });

  it('uses URL selection to render latest persisted jobs and retains the duplicate-queue guard', () => {
    const page = readFileSync(new URL('../../src/pages/Issues.tsx', import.meta.url), 'utf8');
    assert.match(page, /authorizedTrackedIssueScanIds\(searchParams, knownScans\)/);
    assert.match(page, /finally\s*\{\s*setSearchParams\(/);
    assert.match(page, /withTrackedIssueScanIds\(previous, scanIds\)/);
    assert.match(page, /if \(startingBatch \|\| batchScanIds\.length > 0\) return/);
    assert.match(page, /<RemediationStatusLink scanId=\{scanId\}/);
    assert.match(page, /latest recorded job/);
    assert.doesNotMatch(page, /\[batchScanIds, setBatchScanIds\] = useState/);
    const effects = page.slice(page.indexOf('useEffect(() =>'), page.indexOf('// Flatten all issues'));
    assert.doesNotMatch(effects, /batchRemediate|startRemediationJob/);
  });
});

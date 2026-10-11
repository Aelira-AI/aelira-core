import test from 'node:test';
import assert from 'node:assert/strict';
import { movePDFChild, parsePDFCloudContext, parsePDFEditSave, parsePDFEditTargets, validPDFEditOperation } from '../../src/utils/pdfStructureEditor.ts';

const hash = 'a'.repeat(64);
const digest = 'b'.repeat(64);
const id = path => `${hash}:${path}`;
const context = text => ({ status: 'available', page_numbers: [1], segments: [{ page_number: 1, text, source: 'MCID' }], truncated: false });
const target = (path, role, children = []) => ({ target_id: id(path), role, children: children.map(id),
  can_set_heading: ['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6'].includes(role) && children.length === 0,
  can_set_heading_group: false,
  can_reorder: ['StructTreeRoot', 'Document', 'Part', 'Sect', 'Div'].includes(role) && children.length > 1,
  can_set_column_headers: false, context: context(role) });
const fixture = () => ({ precondition: { source_kind: 'original', source_sha256: hash, expected_artifact_id: null,
  state_digest: digest, cloud_file_id: null }, targets: [target('root', 'StructTreeRoot', ['0', '1']),
  target('0', 'P'), target('1', 'H2')] });
const groupedFixture = () => {
  const data = fixture();
  data.targets = [target('root', 'StructTreeRoot', ['0', '1']),
    target('0', 'Div', ['0/0', '0/1']), target('0/0', 'P', ['0/0/0']),
    target('0/0/0', 'Span'), target('0/1', 'P'), target('1', 'P')];
  data.targets[1].can_set_heading_group = true;
  return data;
};
const sparseFixture = () => {
  const data = fixture();
  data.targets = [target('root', 'StructTreeRoot', ['0']),
    target('0', 'Document', ['0/0', '0/1']),
    target('0/0', 'Div', ['0/0/1']),
    target('0/0/1', 'P'), target('0/1', 'P')];
  // The Div also owns direct marked content at /K index 0, so its sole
  // structural child keeps actual /K index 1 and cannot be reordered.
  return data;
};

test('verified graph supports heading and complete changed sibling permutation', () => {
  const data = parsePDFEditTargets(fixture(), 'original', null);
  const children = data.targets[0].children;
  const moved = movePDFChild(children, 1, -1);
  assert.deepEqual(moved, [id('1'), id('0')]);
  assert.deepEqual(movePDFChild(children, 0, -1), children);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0'), level: 3 }), true);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('1'), level: 2 }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'order', target_id: id('root'), children: moved }), true);
  assert.equal(validPDFEditOperation(data, { kind: 'order', target_id: id('root'), children }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'order', target_id: id('root'), children: [id('1'), id('1')] }), false);
});

test('server may withhold leaf and reorder capabilities on structurally plausible targets', () => {
  const withheld = fixture();
  withheld.targets[0].can_reorder = false;
  withheld.targets[1].can_set_heading = false;
  const data = parsePDFEditTargets(withheld, 'original', null);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0'), level: 2 }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'order', target_id: id('root'), children: [id('1'), id('0')] }), false);
});

test('sparse mixed-content paths retain source ownership and unrelated edit usability', () => {
  const data = parsePDFEditTargets(sparseFixture(), 'original', null);
  assert.equal(data.targets[2].children[0], id('0/0/1'));
  assert.equal(data.targets[2].can_reorder, false);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0/0/1'), level: 2 }), true);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0/1'), level: 2 }), true);
  assert.equal(validPDFEditOperation(data, { kind: 'order', target_id: id('0/0'), children: [id('0/0/1')] }), false);
});

test('sparse graph refuses forged parentage, depth, order, duplicates and missing nodes', () => {
  const mutations = [
    data => { data.targets[3].target_id = id('0/1/1'); data.targets[2].children[0] = id('0/1/1'); },
    data => { data.targets[3].target_id = id('0/0/1/0'); data.targets[2].children[0] = id('0/0/1/0'); },
    data => { const deep = id(Array(41).fill('0').join('/')); data.targets[3].target_id = deep; data.targets[2].children[0] = deep; },
    data => { data.targets[3].target_id = id('0/0/01'); data.targets[2].children[0] = id('0/0/01'); },
    data => { data.targets[3].target_id = id('0/0/9007199254740992'); data.targets[2].children[0] = id('0/0/9007199254740992'); },
    data => { data.targets[1].children.reverse(); },
    data => { data.targets[2].children.push(id('0/0/1')); },
    data => { data.targets[2].children.push(id('root')); },
    data => { data.targets.splice(3, 1); },
    data => { data.targets.push(target('0/2', 'P')); },
  ];
  for (const mutate of mutations) {
    const data = sparseFixture(); mutate(data);
    assert.throws(() => parsePDFEditTargets(data, 'original', null));
  }
});

test('rejects stale preconditions, forged paths, malformed graph and capabilities', () => {
  const mutate = [
    data => { data.precondition.source_kind = 'saved'; },
    data => { data.precondition.source_sha256 = digest; },
    data => { data.precondition.source_sha256 = [hash]; },
    data => { data.precondition.state_digest = 'bad'; },
    data => { data.precondition.state_digest = [digest]; },
    data => { data.targets[0].children[0] = id('7'); },
    data => { data.targets[1].target_id = id('1'); },
    data => { data.targets[0].can_set_heading = true; },
    data => { delete data.targets[1].can_set_heading_group; },
    data => { data.targets[1].can_set_heading_group = 'true'; },
    data => { data.targets[1].can_reorder = true; },
    data => { data.targets[0].role = 'P'; },
    data => { data.targets[1].role = 'StructTreeRoot'; },
    data => { data.targets[1].role = ['P']; },
    data => { data.targets[1].context.segments[0].page_number = 2; },
    data => { data.targets[1].context.status = ['available']; },
    data => { data.targets[1].context.segments[0].source = ['MCID']; },
  ];
  for (const change of mutate) { const data = fixture(); change(data); assert.throws(() => parsePDFEditTargets(data, 'original', null)); }
  assert.throws(() => parsePDFEditTargets(fixture(), 'original', 'wrong-cloud'));
  const unsolicitedCloud = fixture();
  unsolicitedCloud.precondition.cloud_file_id = '550e8400-e29b-41d4-a716-446655440000';
  assert.throws(() => parsePDFEditTargets(unsolicitedCloud, 'original', null));
});

test('grouped heading uses a verified Div graph and stays distinct from leaf headings', () => {
  const data = parsePDFEditTargets(groupedFixture(), 'original', null);
  assert.equal(data.targets[1].can_set_heading, false);
  assert.equal(data.targets[1].can_set_heading_group, true);
  assert.equal(data.targets[2].can_set_heading, false);
  assert.equal(validPDFEditOperation(data, { kind: 'heading_group', target_id: id('0'), level: 2 }), true);
  for (const level of [0, 7, 2.5, true, '2']) {
    assert.equal(validPDFEditOperation(data, { kind: 'heading_group', target_id: id('0'), level }), false);
  }
  assert.equal(validPDFEditOperation(data, { kind: 'heading_group', target_id: id('1'), level: 2 }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0'), level: 2 }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('1'), level: 2 }), true);

  const mutations = [
    broken => { broken.targets[1].role = 'Sect'; },
    broken => { broken.targets[4].role = 'Figure'; },
  ];
  for (const mutate of mutations) {
    const broken = groupedFixture(); mutate(broken);
    assert.throws(() => parsePDFEditTargets(broken, 'original', null));
  }
  const limited = groupedFixture();
  limited.targets[4].context = { status: 'unavailable', page_numbers: [], segments: [], truncated: true };
  assert.equal(validPDFEditOperation(parsePDFEditTargets(limited, 'original', null),
    { kind: 'heading_group', target_id: id('0'), level: 2 }), true);
  limited.targets[1].context = { status: 'unavailable', page_numbers: [], segments: [], truncated: true };
  assert.equal(validPDFEditOperation(parsePDFEditTargets(limited, 'original', null),
    { kind: 'heading_group', target_id: id('0'), level: 2 }), false);
});

test('existing grouped heading can change level without accepting same-level or nested semantics', () => {
  const promoted = groupedFixture();
  promoted.targets[1].role = 'H2';
  promoted.targets[1].can_reorder = false;
  promoted.targets[2].role = 'Span';
  promoted.targets[2].can_set_heading = false;
  promoted.targets[4].role = 'Span';
  promoted.targets[4].can_set_heading = false;
  const data = parsePDFEditTargets(promoted, 'original', null);
  assert.equal(data.targets[1].can_set_heading_group, true);
  assert.equal(validPDFEditOperation(data, { kind: 'heading_group', target_id: id('0'), level: 3 }), true);
  assert.equal(validPDFEditOperation(data, { kind: 'heading_group', target_id: id('0'), level: 2 }), false);
  assert.equal(validPDFEditOperation(data, { kind: 'heading', target_id: id('0'), level: 3 }), false);

  const divWithoutParagraph = groupedFixture();
  divWithoutParagraph.targets[2].role = 'Span';
  divWithoutParagraph.targets[2].can_set_heading = false;
  divWithoutParagraph.targets[4].role = 'Span';
  divWithoutParagraph.targets[4].can_set_heading = false;
  assert.throws(() => parsePDFEditTargets(divWithoutParagraph, 'original', null));

  const nestedHeading = groupedFixture();
  nestedHeading.targets[1].role = 'H2';
  nestedHeading.targets[1].can_reorder = false;
  nestedHeading.targets[4].role = 'H3';
  assert.throws(() => parsePDFEditTargets(nestedHeading, 'original', null));
});

test('explicit cloud file context never falls back to a local edit', () => {
  assert.deepEqual(parsePDFCloudContext(''), { kind: 'local' });
  assert.deepEqual(parsePDFCloudContext('?cloud_file_id=550e8400-e29b-41d4-a716-446655440000'),
    { kind: 'cloud', id: '550e8400-e29b-41d4-a716-446655440000' });
  assert.deepEqual(parsePDFCloudContext('?cloud_file_id=550E8400-E29B-41D4-A716-446655440000'),
    { kind: 'cloud', id: '550e8400-e29b-41d4-a716-446655440000' });
  for (const search of ['?cloud_file_id=', '?cloud_file_id=../local',
    '?cloud_file_id=550e8400-e29b-41d4-a716-446655440000&cloud_file_id=550e8400-e29b-41d4-a716-446655440001']) {
    assert.deepEqual(parsePDFCloudContext(search), { kind: 'invalid' });
  }
});

test('Unicode excerpt limit uses code points and unavailable targets cannot be edited', () => {
  const data = fixture();
  data.targets[1].context.segments[0].text = '😀'.repeat(240);
  assert.equal(parsePDFEditTargets(data, 'original', null).targets[1].context.segments[0].text.length, 480);
  data.targets[1].context.segments[0].text += 'x';
  assert.throws(() => parsePDFEditTargets(data, 'original', null));
  const unavailable = fixture();
  unavailable.targets[1].context = { status: 'unavailable', page_numbers: [], segments: [], truncated: true };
  const parsed = parsePDFEditTargets(unavailable, 'original', null);
  assert.equal(validPDFEditOperation(parsed, { kind: 'heading', target_id: id('0'), level: 1 }), false);
});

test('table header action requires advertised table target and confirmed pending receipt', () => {
  const data = fixture();
  data.targets[1].role = 'Table'; data.targets[1].can_set_heading = false; data.targets[1].can_set_column_headers = true;
  const parsed = parsePDFEditTargets(data, 'original', null);
  assert.equal(validPDFEditOperation(parsed, { kind: 'table_column_headers', target_id: id('0') }), true);
  assert.equal(validPDFEditOperation(parsed, { kind: 'table_column_headers', target_id: id('1') }), false);
  assert.deepEqual(parsePDFEditSave({ artifact_id: 'artifact', sha256: digest, review_status: 'pending', needs_review: true }, parsed.precondition), { artifact_id: 'artifact', sha256: digest });
  assert.throws(() => parsePDFEditSave({ artifact_id: 'artifact', sha256: digest, review_status: 'approved', needs_review: false }, parsed.precondition));
  assert.throws(() => parsePDFEditSave({ artifact_id: 'artifact', sha256: hash, review_status: 'pending', needs_review: true }, parsed.precondition));
  assert.throws(() => parsePDFEditSave({ artifact_id: 'artifact', sha256: [digest], review_status: 'pending', needs_review: true }, parsed.precondition));
});

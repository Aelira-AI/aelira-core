import React, { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  runOwnedRequest,
  useAbortableRequestOwner,
} from '../../src/hooks/useAbortableRequestOwner.ts';

const noop = () => {};
let originalDocument;
let originalWindow;

function createContainer() {
  const fakeWindow = {
    event: undefined,
    HTMLIFrameElement: class {},
    addEventListener: noop,
    removeEventListener: noop,
  };
  const fakeDocument = {
    nodeType: 9,
    addEventListener: noop,
    removeEventListener: noop,
    defaultView: fakeWindow,
    documentElement: { namespaceURI: 'http://www.w3.org/1999/xhtml' },
  };
  globalThis.window = fakeWindow;
  globalThis.document = fakeDocument;
  return {
    nodeType: 1,
    tagName: 'DIV',
    nodeName: 'DIV',
    namespaceURI: 'http://www.w3.org/1999/xhtml',
    ownerDocument: fakeDocument,
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: noop,
    removeChild: noop,
    insertBefore: noop,
  };
}

function OwnerHarness({ ownerKey, onOwner }) {
  const owner = useAbortableRequestOwner(ownerKey);
  useEffect(() => onOwner(owner), [onOwner, owner]);
  return null;
}

beforeEach(() => {
  originalDocument = globalThis.document;
  originalWindow = globalThis.window;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  globalThis.document = originalDocument;
  globalThis.window = originalWindow;
  delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

describe('useAbortableRequestOwner mounted lifecycle', () => {
  it('aborts replacement and unmounted work while fencing stale completion', async () => {
    const container = createContainer();
    const root = createRoot(container);
    let owner;
    let releaseFirst;
    let staleCommits = 0;
    const firstFinished = new Promise((resolve) => {
      releaseFirst = resolve;
    });
    const setOwner = (nextOwner) => {
      owner = nextOwner;
    };

    await act(async () => {
      root.render(React.createElement(OwnerHarness, {
        ownerKey: 'course-1',
        onOwner: setOwner,
      }));
    });
    const first = owner.begin();
    const delayedCompletion = firstFinished.then(() => {
      if (owner.finish(first)) staleCommits += 1;
    });

    await act(async () => {
      root.render(React.createElement(OwnerHarness, {
        ownerKey: 'course-2',
        onOwner: setOwner,
      }));
    });
    assert.equal(first.controller.signal.aborted, true);

    const replacement = owner.begin();
    releaseFirst();
    await delayedCompletion;
    assert.equal(staleCommits, 0);
    assert.equal(replacement.controller.signal.aborted, false);

    const newerReplacement = owner.begin();
    assert.equal(replacement.controller.signal.aborted, true);
    assert.equal(newerReplacement.controller.signal.aborted, false);

    await act(async () => root.unmount());
    assert.equal(newerReplacement.controller.signal.aborted, true);
  });

  it('blocks every page-flow side effect when delayed remediation resolves stale', async () => {
    const container = createContainer();
    const root = createRoot(container);
    let owner;
    let releaseRemediation;
    let executionSignal;
    const effects = {
      notifications: 0,
      refreshes: 0,
      refreshCommits: 0,
      failures: 0,
      stateSettles: 0,
    };
    const delayedRemediation = new Promise((resolve) => {
      releaseRemediation = resolve;
    });
    const setOwner = (nextOwner) => {
      owner = nextOwner;
    };

    await act(async () => {
      root.render(React.createElement(OwnerHarness, {
        ownerKey: 'course-1',
        onOwner: setOwner,
      }));
    });
    const pageFlow = runOwnedRequest({
      owner,
      execute: (signal) => {
        executionSignal = signal;
        return delayedRemediation;
      },
      notify: () => { effects.notifications += 1; },
      refresh: async () => {
        effects.refreshes += 1;
        return { course: 'stale' };
      },
      commitRefresh: () => { effects.refreshCommits += 1; },
      fail: () => { effects.failures += 1; },
      settle: () => { effects.stateSettles += 1; },
    });

    await act(async () => {
      root.render(React.createElement(OwnerHarness, {
        ownerKey: 'course-2',
        onOwner: setOwner,
      }));
    });
    assert.equal(executionSignal.aborted, true);

    releaseRemediation({ requestedCount: 1 });
    await pageFlow;
    assert.deepEqual(effects, {
      notifications: 0,
      refreshes: 0,
      refreshCommits: 0,
      failures: 0,
      stateSettles: 0,
    });

    await act(async () => root.unmount());
  });
});

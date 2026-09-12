import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../src/components/layout/Sidebar.tsx', import.meta.url), 'utf8');
const mobile = source.slice(source.indexOf('{/* Mobile Sidebar */}'), source.indexOf('{/* Desktop Sidebar */}'));
const desktop = source.slice(source.indexOf('{/* Desktop Sidebar */}'));

describe('mobile navigation focus contract', () => {
  it('makes the closed drawer inert and restores interactivity when opened', () => {
    assert.match(mobile, /aria-hidden=\{!mobileOpen\}/);
    assert.match(mobile, /inert=\{!mobileOpen\}/);
    assert.doesNotMatch(desktop, /inert=|aria-hidden=/);
  });

  it('preserves initial focus, Tab trapping and Escape focus restoration', () => {
    assert.match(source, /if \(mobileOpen && sidebarRef\.current\)/);
    assert.match(source, /firstLink\?\.focus\(\)/);
    assert.match(mobile, /onKeyDown=\{handleKeyDownTrap\}/);
    assert.match(source, /event\.shiftKey && document\.activeElement === firstElement/);
    assert.match(source, /!event\.shiftKey && document\.activeElement === lastElement/);
    assert.match(source, /event\.key === 'Escape' && mobileOpen[\s\S]{0,120}toggleButtonRef\.current\?\.focus\(\)/);
  });
});

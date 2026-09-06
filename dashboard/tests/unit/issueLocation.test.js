import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('.') && context.parentURL?.match(/\.tsx?$/) && !/\.tsx?$/.test(specifier)) {
      for (const extension of ['.ts', '.tsx']) {
        const candidate = new URL(`${specifier}${extension}`, context.parentURL);
        if (existsSync(fileURLToPath(candidate))) {
          return { shortCircuit: true, url: candidate.href };
        }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (/\.tsx?$/.test(url)) {
      const source = readFileSync(fileURLToPath(url), 'utf8');
      return {
        format: 'module',
        shortCircuit: true,
        source: ts.transpileModule(source, {
          compilerOptions: {
            esModuleInterop: true,
            jsx: ts.JsxEmit.ReactJSX,
            module: ts.ModuleKind.ESNext,
            target: ts.ScriptTarget.ES2022,
          },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});

const { IssueList } = await import('../../src/components/results/IssueList.tsx');

function renderIssue(issue) {
  return renderToStaticMarkup(React.createElement(IssueList, { issues: [issue] }));
}

function occurrenceCount(markup, text) {
  return markup.split(text).length - 1;
}

describe('IssueList location rendering', () => {
  it('renders one Location block when a PDF issue has a page number and location', () => {
    const markup = renderIssue({ title: 'Heading issue', page_number: 2, location: 'Paragraph 4' });

    assert.equal(occurrenceCount(markup, 'Location:'), 1);
    assert.equal(occurrenceCount(markup, 'Paragraph 4'), 1);
  });

  it('renders the generic Location fallback when no page URL or page number exists', () => {
    const markup = renderIssue({ title: 'Heading issue', location: 'Header navigation' });

    assert.equal(occurrenceCount(markup, 'Location:'), 1);
    assert.match(markup, /Location: Header navigation/);
    assert.equal(occurrenceCount(markup, 'Page:'), 0);
  });

  it('renders Page without the generic Location fallback when a page URL exists', () => {
    const markup = renderIssue({
      title: 'Link issue',
      page_url: 'https://example.edu/course',
      location: 'Header navigation',
    });

    assert.equal(occurrenceCount(markup, 'Page:'), 1);
    assert.match(markup, /https:\/\/example\.edu\/course/);
    assert.equal(occurrenceCount(markup, 'Location:'), 0);
  });

  it('preserves the existing truthy page-number behavior for zero and negative values', () => {
    const zeroMarkup = renderIssue({ title: 'Zero page', page_number: 0, location: 'Cover' });
    const negativeMarkup = renderIssue({ title: 'Negative page', page_number: -1 });

    assert.equal(occurrenceCount(zeroMarkup, 'Location:'), 1);
    assert.match(zeroMarkup, /Location: Cover/);
    assert.equal(occurrenceCount(negativeMarkup, 'Location:'), 1);
    assert.match(negativeMarkup, /Page -1/);
  });

  it('falls back to the page number when a PDF issue has no location', () => {
    const markup = renderIssue({ title: 'Heading issue', page_number: 2 });

    assert.equal(occurrenceCount(markup, 'Location:'), 1);
    assert.match(markup, /Page 2/);
  });
});

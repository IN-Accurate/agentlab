import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { sanitizeUi } from './uiState.js';

describe('sanitizeUi', () => {
  it('passes a valid location through', () => {
    assert.deepEqual(
      sanitizeUi({ view: 'dashboard', activeId: 'c1', dash: { tab: 'runs', projectId: 'p1' } }),
      { view: 'dashboard', activeId: 'c1', dash: { tab: 'runs', projectId: 'p1' } },
    );
  });

  it('falls back to safe defaults for unknown values', () => {
    assert.deepEqual(
      sanitizeUi({ view: 'nowhere', activeId: 42, dash: { tab: 'nope', projectId: null } }),
      { view: 'chat', activeId: null, dash: { tab: 'overview', projectId: null } },
    );
  });

  it('drops drill selections and survives garbage', () => {
    assert.deepEqual(
      sanitizeUi({ view: 'dashboard', dash: { tab: 'runs', projectId: 'p1', runId: 'r1' } }),
      { view: 'dashboard', activeId: null, dash: { tab: 'runs', projectId: 'p1' } },
    );
    assert.deepEqual(sanitizeUi(null), {
      view: 'chat',
      activeId: null,
      dash: { tab: 'overview', projectId: null },
    });
    assert.deepEqual(sanitizeUi('junk'), {
      view: 'chat',
      activeId: null,
      dash: { tab: 'overview', projectId: null },
    });
  });
});

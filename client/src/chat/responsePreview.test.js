import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { limitResponseTree, responseExcerpt } from './responsePreview.js';

describe('response excerpts', () => {
  it('leaves short Markdown trees untouched', () => {
    const tree = fromMarkdown('I **can** help.\n\n- First\n- Second');
    const result = limitResponseTree(tree);
    assert.equal(result.truncated, false);
    assert.equal(result.tree, tree);
  });

  it('keeps the first four sentences and their formatting without mutating the source', () => {
    const source = '**One sentence. Two sentences. Three sentences. Four sentences. Five sentences.**';
    const tree = fromMarkdown(source);
    const original = structuredClone(tree);
    const result = limitResponseTree(tree);
    assert.equal(result.excerpt, 'One sentence. Two sentences. Three sentences. Four sentences.');
    assert.equal(result.tree.children[0].children[0].type, 'strong');
    assert.equal(result.tree.children[0].children[0].children[0].value, result.excerpt);
    assert.deepEqual(tree, original);
  });

  it('bounds huge unpunctuated text without inventing any content', () => {
    const source = 'a'.repeat(10000);
    const result = responseExcerpt(source);
    assert.equal(result.truncated, true);
    assert.equal(result.excerpt, source.slice(0, 900));
  });

  it('bounds long code and lists even when they contain no sentence punctuation', () => {
    const code = responseExcerpt(`\`\`\`text\n${Array.from({ length: 60 }, (_, i) => `line ${i}`).join('\n')}\n\`\`\``);
    assert.equal(code.truncated, true);
    assert.ok(code.excerpt.split('\n').length <= 10);
    assert.equal(code.tree.children[0].type, 'code');
    assert.equal(responseExcerpt(`\`\`\`text\n${'\n'.repeat(60)}\`\`\``).truncated, true);
    const list = responseExcerpt(Array.from({ length: 60 }, (_, i) => `- Item ${i}`).join('\n'));
    assert.equal(list.truncated, true);
    assert.ok(list.excerpt.split('\n').length <= 10);
    assert.equal(list.tree.children[0].type, 'list');
  });

  it('retains reference link destinations outside the preview', () => {
    const result = responseExcerpt('[One][source]. Two. Three. Four. Five.\n\n[source]: https://example.com');
    assert.equal(result.truncated, true);
    assert.equal(result.tree.children.at(-1).type, 'definition');
    assert.equal(result.tree.children.at(-1).url, 'https://example.com');
  });

  it('handles an absent response', () => {
    assert.equal(responseExcerpt(undefined).truncated, false);
    assert.equal(responseExcerpt(undefined).excerpt, '');
  });
});

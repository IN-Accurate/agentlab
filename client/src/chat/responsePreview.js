import { fromMarkdown } from 'mdast-util-from-markdown';

export const RESPONSE_PREVIEW_LIMITS = { sentences: 4, characters: 900, lines: 10 };

const sentenceSegmenter = typeof Intl.Segmenter === 'function'
  ? new Intl.Segmenter('en', { granularity: 'sentence' })
  : null;
const hidden = new Set(['definition', 'footnoteDefinition', 'html']);

function separator(node) {
  if (node.type === 'root' || node.type === 'blockquote') return '\n\n';
  if (['list', 'listItem', 'table'].includes(node.type)) return '\n';
  if (node.type === 'tableRow') return ' | ';
  return '';
}

function visibleChildren(node) {
  return (node.children ?? []).filter((child) => !hidden.has(child.type));
}

function plainText(node) {
  if (hidden.has(node.type)) return '';
  if (node.type === 'break') return '\n';
  if (node.type === 'thematicBreak') return '—';
  if (node.type === 'image' || node.type === 'imageReference') return node.alt || '\uFFFC';
  if (typeof node.value === 'string') return node.value || (node.type === 'code' ? '\uFFFC' : '');
  return visibleChildren(node).map(plainText).join(separator(node));
}

function sentenceEnd(text, count) {
  const segments = sentenceSegmenter
    ? [...sentenceSegmenter.segment(text)]
    : [...text.matchAll(/[^.!?]+(?:[.!?]+(?=\s|$)|$)/g)].map((match) => ({ segment: match[0], index: match.index }));
  const sentences = segments.filter(({ segment }) => segment.trim());
  if (sentences.length <= count) return text.length;
  const last = sentences[count - 1];
  return last.index + last.segment.trimEnd().length;
}

function lineEnd(text, count) {
  let line = 1;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '\n') {
      if (line === count) return index;
      line += 1;
    }
  }
  return text.length;
}

// Clip text leaves while retaining their Markdown ancestors. A sentence ending
// inside bold, a link, a list, or a code fence therefore still renders correctly.
function clipNode(node, available) {
  const text = plainText(node);
  if (text.length <= available) return { node, used: text.length };
  if (!available) return { node: null, used: 0 };
  if (typeof node.value === 'string') {
    return { node: { ...node, value: node.value.slice(0, available) }, used: available };
  }
  if (node.type === 'image' || node.type === 'imageReference') {
    return { node: { ...node, alt: (node.alt ?? '').slice(0, available) }, used: available };
  }
  const children = [];
  const join = separator(node);
  let used = 0;
  for (const child of visibleChildren(node)) {
    const gap = children.length ? join.length : 0;
    if (used + gap >= available) break;
    const clipped = clipNode(child, available - used - gap);
    if (clipped.node) {
      children.push(clipped.node);
      used += gap + clipped.used;
    }
  }
  // Reference-link definitions do not take visual space, but must survive so
  // links in the excerpt retain their original destinations.
  const definitions = (node.children ?? []).filter((child) => child.type === 'definition');
  return { node: { ...node, children: [...children, ...definitions] }, used };
}

export function limitResponseTree(tree, limits = RESPONSE_PREVIEW_LIMITS) {
  const text = plainText(tree);
  let end = Math.min(
    sentenceEnd(text, limits.sentences),
    lineEnd(text, limits.lines),
    limits.characters,
  );
  if (end === limits.characters && end < text.length) {
    const boundary = text.slice(0, end).search(/\s+\S*$/);
    if (boundary > end - 80) end = boundary;
  }
  const truncated = end < text.length;
  return {
    tree: truncated ? clipNode(tree, end).node : tree,
    excerpt: truncated ? text.slice(0, end).trimEnd() : text,
    truncated,
  };
}

export function responseExcerpt(text) {
  return limitResponseTree(fromMarkdown(typeof text === 'string' ? text : ''));
}

export function remarkResponsePreview() {
  return (tree) => limitResponseTree(tree).tree;
}

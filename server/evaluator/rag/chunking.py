"""Split documents into overlapping, sentence-aligned chunks."""

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")


@dataclass
class Chunk:
    text: str
    position: int
    heading: str | None = None


def units(text, max_chars):
    """Yield (sentence, heading); markdown headings update the heading, never a unit."""
    heading = None
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        while lines and (match := HEADING.match(lines[0])):
            heading = match.group(1)
            lines.pop(0)
        body = " ".join(line.strip() for line in lines).strip()
        for sentence in SENTENCE_END.split(body) if body else []:
            while len(sentence) > max_chars:  # no punctuation: cut on a space
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                yield sentence[:cut].strip(), heading
                sentence = sentence[cut:].strip()
            if sentence:
                yield sentence, heading


def chunk_text(text, max_chars=800, overlap=120):
    """Greedy-pack sentences up to max_chars; carry ~overlap chars into the next chunk.

    A markdown heading always starts a new chunk, so a chunk's heading is never wrong.

    Overlap keeps a fact that straddles a boundary retrievable from either side.
    """
    if max_chars < 50 or not 0 <= overlap < max_chars:
        raise ValueError("need max_chars >= 50 and 0 <= overlap < max_chars")
    chunks, buffer, fresh = [], [], 0  # fresh = units added since the last flush

    def flush():
        joined = " ".join(s for s, _ in buffer)
        chunks.append(Chunk(joined, len(chunks), buffer[0][1]))

    for sentence, heading in units(text, max_chars):
        if buffer and heading != buffer[-1][1]:
            if fresh:
                flush()
            buffer, fresh = [], 0  # a new section never inherits the old one's overlap
        if buffer and sum(len(s) + 1 for s, _ in buffer) + len(sentence) > max_chars:
            flush()
            tail, size = [], 0
            for item in reversed(buffer):
                if size + len(item[0]) > overlap:
                    break
                tail.insert(0, item)
                size += len(item[0]) + 1
            if size + len(sentence) > max_chars:
                tail = []  # overlap must never push a chunk past max_chars
            buffer, fresh = tail, 0
        buffer.append((sentence, heading))
        fresh += 1
    if fresh:  # skip a trailing chunk that would be pure overlap
        flush()
    return chunks

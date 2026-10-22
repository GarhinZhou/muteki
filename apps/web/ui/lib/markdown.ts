/**
 * Small dependency-free Markdown block parser for the command deck.
 *
 * Scope: the subset the swarm actually emits in writeups / coordinator prose —
 * ATX headings, fenced code, ordered/unordered lists (nested), blockquotes,
 * tables, horizontal rules, and paragraphs. Inline markup is rendered by
 * `components/Markdown.tsx` with React elements (never raw HTML), so this module
 * only builds the block tree and has no React/JSX dependency.
 *
 * Deliberate omissions: raw HTML, reference links, setext headings, footnotes.
 */

export type MarkdownAlign = "left" | "center" | "right" | null;

export interface MarkdownListItem {
  blocks: MarkdownBlock[];
  /** GFM task item state; null when the item is a normal list entry. */
  task: boolean | null;
}

export type MarkdownBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; level: number; text: string }
  | { type: "code"; lang: string; code: string }
  | { type: "blockquote"; blocks: MarkdownBlock[] }
  | { type: "list"; ordered: boolean; start: number; items: MarkdownListItem[] }
  | { type: "table"; header: string[]; align: MarkdownAlign[]; rows: string[][] }
  | { type: "hr" };

const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)\s*$/;
const CLOSE_FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*$/;
// CommonMark requires whitespace after the heading marker.
const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR_RE = /^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/;
const QUOTE_RE = /^\s{0,3}>\s?(.*)$/;
const LIST_RE = /^(\s*)([-+*]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_DELIM_CELL_RE = /^:?-{1,}:?$/;

export function parseMarkdown(text: string): MarkdownBlock[] {
  const normalized = String(text ?? "").replace(/\r\n?/g, "\n");
  return parseBlocks(normalized.split("\n"));
}

function parseBlocks(lines: string[]): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }

    const fence = line.match(FENCE_RE);
    if (fence) {
      const marker = fence[1][0];
      const markerLen = fence[1].length;
      const lang = fence[2] || "";
      const code: string[] = [];
      i += 1;
      while (i < lines.length) {
        const close = lines[i].match(CLOSE_FENCE_RE);
        if (close && close[1][0] === marker && close[1].length >= markerLen) {
          i += 1;
          break;
        }
        code.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "code", lang, code: code.join("\n") });
      continue;
    }

    const heading = line.match(HEADING_RE);
    if (heading) {
      blocks.push({
        type: "heading",
        level: heading[1].length,
        text: (heading[2] || "").trim(),
      });
      i += 1;
      continue;
    }

    if (HR_RE.test(line)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length) {
        const quoted = lines[i].match(QUOTE_RE);
        if (quoted) {
          inner.push(quoted[1]);
          i += 1;
          continue;
        }
        if (!lines[i].trim()) {
          let j = i + 1;
          while (j < lines.length && !lines[j].trim()) j += 1;
          if (j < lines.length && QUOTE_RE.test(lines[j])) {
            inner.push("");
            i += 1;
            continue;
          }
        }
        break;
      }
      blocks.push({ type: "blockquote", blocks: parseBlocks(inner) });
      continue;
    }

    if (
      line.includes("|")
      && i + 1 < lines.length
      && isTableDelimiter(lines[i + 1])
    ) {
      const header = splitTableRow(line);
      const align = splitTableRow(lines[i + 1]).map((cell): MarkdownAlign => {
        const trimmed = cell.trim();
        const left = trimmed.startsWith(":");
        const right = trimmed.endsWith(":");
        if (left && right) return "center";
        if (right) return "right";
        if (left) return "left";
        return null;
      });
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ type: "table", header, align, rows });
      continue;
    }

    if (LIST_RE.test(line)) {
      const parsed = parseList(lines, i);
      blocks.push(parsed.block);
      i = parsed.next;
      continue;
    }

    const paragraph: string[] = [line.trim()];
    i += 1;
    while (i < lines.length) {
      const next = lines[i];
      if (!next.trim()) break;
      if (startsBlock(next, lines, i)) break;
      paragraph.push(next.trim());
      i += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
  }

  return blocks;
}

function startsBlock(line: string, lines: string[], index: number): boolean {
  return (
    FENCE_RE.test(line)
    || HEADING_RE.test(line)
    || HR_RE.test(line)
    || QUOTE_RE.test(line)
    || LIST_RE.test(line)
    || (
      line.includes("|")
      && index + 1 < lines.length
      && isTableDelimiter(lines[index + 1])
    )
  );
}

function parseList(
  lines: string[],
  start: number,
): { block: MarkdownBlock; next: number } {
  const first = lines[start].match(LIST_RE);
  if (!first) {
    return {
      block: { type: "paragraph", text: lines[start] ?? "" },
      next: start + 1,
    };
  }

  const baseIndent = first[1].length;
  const ordered = /^\d/.test(first[2]);
  const startNumber = ordered ? parseInt(first[2], 10) || 1 : 1;
  const items: MarkdownListItem[] = [];
  let i = start;

  while (i < lines.length) {
    const marker = lines[i].match(LIST_RE);
    if (!marker) break;
    const indent = marker[1].length;
    if (indent !== baseIndent) break;
    if (/^\d/.test(marker[2]) !== ordered) break;

    let firstLine = marker[3];
    let task: boolean | null = null;
    const taskMatch = firstLine.match(/^\[([ xX])\]\s+(.*)$/);
    if (taskMatch) {
      task = taskMatch[1].toLowerCase() === "x";
      firstLine = taskMatch[2];
    }

    const content: string[] = [firstLine];
    const contentIndent = indent + marker[2].length + 1;
    i += 1;

    while (i < lines.length) {
      const current = lines[i];
      if (!current.trim()) {
        let j = i + 1;
        while (j < lines.length && !lines[j].trim()) j += 1;
        if (j >= lines.length) {
          i = j;
          break;
        }
        const nextMarker = lines[j].match(LIST_RE);
        const nextIndent = leadingSpaces(lines[j]);
        if (nextMarker && nextMarker[1].length === indent) {
          i = j;
          break;
        }
        if (nextIndent > indent) {
          content.push("");
          i += 1;
          continue;
        }
        break;
      }

      const nextMarker = current.match(LIST_RE);
      const nextIndent = nextMarker ? nextMarker[1].length : leadingSpaces(current);
      if (nextMarker && nextIndent <= indent) break;
      if (nextIndent > indent) {
        content.push(current.slice(Math.min(nextIndent, contentIndent)));
        i += 1;
        continue;
      }
      // Lazy continuation of the item's first paragraph.
      content.push(current.trim());
      i += 1;
    }

    if (content.length === 0) content.push("");
    items.push({ blocks: parseBlocks(content), task });
  }

  return {
    block: { type: "list", ordered, start: startNumber, items },
    next: i,
  };
}

function leadingSpaces(line: string): number {
  let count = 0;
  while (count < line.length && (line[count] === " " || line[count] === "\t")) {
    count += line[count] === "\t" ? 4 : 1;
  }
  return count;
}

function isTableDelimiter(line: string): boolean {
  if (!line.includes("-")) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => TABLE_DELIM_CELL_RE.test(cell.trim()));
}

/** Split a GFM table row on unescaped pipes, dropping the optional outer pipes. */
export function splitTableRow(line: string): string[] {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|")) body = body.slice(0, -1);

  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < body.length; i += 1) {
    const ch = body[i];
    if (ch === "\\" && body[i + 1] === "|") {
      current += "|";
      i += 1;
      continue;
    }
    if (ch === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  cells.push(current.trim());
  return cells;
}

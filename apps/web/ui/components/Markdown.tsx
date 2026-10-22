"use client";

import { memo, useMemo } from "react";
import type { ReactNode } from "react";
import {
  MarkdownBlock,
  MarkdownListItem,
  parseMarkdown,
} from "@/lib/markdown";

/**
 * Safe, dependency-free Markdown renderer for conversation bubbles.
 *
 * The parser (`lib/markdown.ts`) builds a block tree; this file turns it into
 * React elements. Everything is rendered as text/elements — raw HTML is never
 * injected, so worker- or model-produced content cannot execute script. Links
 * and images are restricted to http(s)/mailto and same-origin relative paths.
 */

export interface MarkdownProps {
  text: string;
  className?: string;
}

const SAFE_LINK_RE = /^(?:https?:\/\/|mailto:|\/|\.{1,2}\/|#)/i;

function safeHref(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  return SAFE_LINK_RE.test(value) ? value : null;
}

function safeSrc(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith("/") || value.startsWith("./") || value.startsWith("../")) {
    return value;
  }
  return null;
}

function isSpace(ch: string | undefined): boolean {
  return ch === undefined || /\s/.test(ch);
}

function isWordChar(ch: string | undefined): boolean {
  return ch !== undefined && /[A-Za-z0-9]/.test(ch);
}

interface LinkTarget {
  label: string;
  url: string;
  end: number;
}

function parseLinkAt(text: string, start: number): LinkTarget | null {
  if (text[start] !== "[") return null;

  let depth = 0;
  let close = start;
  for (; close < text.length; close += 1) {
    const ch = text[close];
    if (ch === "\\") {
      close += 1;
      continue;
    }
    if (ch === "[") depth += 1;
    else if (ch === "]") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  if (close >= text.length || text[close + 1] !== "(") return null;

  let end = close + 2;
  let parens = 1;
  for (; end < text.length; end += 1) {
    const ch = text[end];
    if (ch === "\\") {
      end += 1;
      continue;
    }
    if (ch === "(") parens += 1;
    else if (ch === ")") {
      parens -= 1;
      if (parens === 0) break;
    }
  }
  if (end >= text.length) return null;

  let url = text.slice(close + 2, end).trim();
  const titled = url.match(/^(\S+)\s+(?:"[^"]*"|'[^']*')$/);
  if (titled) url = titled[1];
  if (url.startsWith("<") && url.endsWith(">")) url = url.slice(1, -1);

  return { label: text.slice(start + 1, close), url, end: end + 1 };
}

function renderInline(text: string, prefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let plain = "";
  let i = 0;

  const flush = () => {
    if (plain) {
      out.push(plain);
      plain = "";
    }
  };
  const key = (kind: string) => `${prefix}-${kind}-${out.length}`;

  while (i < text.length) {
    const ch = text[i];

    if (ch === "\n") {
      flush();
      out.push(<br key={key("br")} />);
      i += 1;
      continue;
    }

    if (ch === "\\" && i + 1 < text.length && /[\\`*_{}[\]()#+\-.!|>~]/.test(text[i + 1])) {
      plain += text[i + 1];
      i += 2;
      continue;
    }

    if (ch === "`") {
      const runMatch = text.slice(i).match(/^`+/);
      const run = runMatch ? runMatch[0] : "`";
      const end = text.indexOf(run, i + run.length);
      if (end !== -1) {
        flush();
        let code = text.slice(i + run.length, end);
        if (code.length > 1 && code.startsWith(" ") && code.endsWith(" ") && code.trim()) {
          code = code.slice(1, -1);
        }
        out.push(<code key={key("code")}>{code}</code>);
        i = end + run.length;
        continue;
      }
    }

    if (ch === "!" && text[i + 1] === "[") {
      const link = parseLinkAt(text, i + 1);
      if (link) {
        flush();
        const src = safeSrc(link.url);
        if (src) {
          out.push(
            <img
              key={key("img")}
              src={src}
              alt={link.label}
              loading="lazy"
            />,
          );
        } else {
          out.push(<span key={key("img-alt")}>{link.label}</span>);
        }
        i = link.end;
        continue;
      }
    }

    if (ch === "[") {
      const link = parseLinkAt(text, i);
      if (link) {
        flush();
        const href = safeHref(link.url);
        const label = renderInline(link.label, `${prefix}-l${out.length}`);
        if (href) {
          out.push(
            <a
              key={key("link")}
              href={href}
              target="_blank"
              rel="noreferrer noopener"
            >
              {label}
            </a>,
          );
        } else {
          out.push(<span key={key("link-text")}>{label}</span>);
        }
        i = link.end;
        continue;
      }
    }

    if (ch === "<") {
      const auto = text.slice(i).match(/^<((?:https?:\/\/|mailto:)[^>\s]+)>/);
      if (auto) {
        const href = safeHref(auto[1]);
        if (href) {
          flush();
          out.push(
            <a
              key={key("auto")}
              href={href}
              target="_blank"
              rel="noreferrer noopener"
            >
              {auto[1]}
            </a>,
          );
          i += auto[0].length;
          continue;
        }
      }
    }

    // Triple emphasis must be checked before strong/em, otherwise `***x***`
    // degrades to `<strong>*x</strong>*`. Delimiter runs only open/close when
    // they are not adjacent to whitespace (CommonMark flanking rule) — this is
    // what keeps glibc diagnostics such as
    // `*** %n in writable segment detected ***` literal instead of bold.
    if (text.startsWith("***", i) || text.startsWith("___", i)) {
      const marker = text.slice(i, i + 3);
      const end = text.indexOf(marker, i + 3);
      const underscoreOk = marker[0] !== "_"
        || (end !== -1 && !isWordChar(text[i - 1]) && !isWordChar(text[end + 3]));
      if (
        end > i + 3
        && !isSpace(text[i + 3])
        && !isSpace(text[end - 1])
        && underscoreOk
        && text.slice(i + 3, end).trim()
      ) {
        flush();
        out.push(
          <strong key={key("strong-em")}>
            <em>{renderInline(text.slice(i + 3, end), `${prefix}-se${out.length}`)}</em>
          </strong>,
        );
        i = end + 3;
        continue;
      }
    }

    if (text.startsWith("**", i) || text.startsWith("__", i)) {
      const marker = text.slice(i, i + 2);
      const end = text.indexOf(marker, i + 2);
      const underscoreOk = marker[0] !== "_"
        || (end !== -1 && !isWordChar(text[i - 1]) && !isWordChar(text[end + 2]));
      if (
        end > i + 2
        && !isSpace(text[i + 2])
        && !isSpace(text[end - 1])
        && underscoreOk
        && text.slice(i + 2, end).trim()
      ) {
        flush();
        out.push(
          <strong key={key("strong")}>
            {renderInline(text.slice(i + 2, end), `${prefix}-s${out.length}`)}
          </strong>,
        );
        i = end + 2;
        continue;
      }
    }

    if (text.startsWith("~~", i)) {
      const end = text.indexOf("~~", i + 2);
      if (
        end > i + 2
        && !isSpace(text[i + 2])
        && !isSpace(text[end - 1])
        && text.slice(i + 2, end).trim()
      ) {
        flush();
        out.push(
          <del key={key("del")}>
            {renderInline(text.slice(i + 2, end), `${prefix}-d${out.length}`)}
          </del>,
        );
        i = end + 2;
        continue;
      }
    }

    if (ch === "*" || ch === "_") {
      const prevIsWord = i > 0 && /[A-Za-z0-9]/.test(text[i - 1]);
      if (ch === "_" && prevIsWord) {
        plain += ch;
        i += 1;
        continue;
      }
      const end = text.indexOf(ch, i + 1);
      const nextIsWord = end >= 0 && end + 1 < text.length && /[A-Za-z0-9]/.test(text[end + 1]);
      if (
        end > i + 1
        && !isSpace(text[i + 1])
        && !isSpace(text[end - 1])
        && text.slice(i + 1, end).trim()
        && !(ch === "_" && nextIsWord)
      ) {
        flush();
        out.push(
          <em key={key("em")}>
            {renderInline(text.slice(i + 1, end), `${prefix}-e${out.length}`)}
          </em>,
        );
        i = end + 1;
        continue;
      }
    }

    plain += ch;
    i += 1;
  }

  flush();
  return out;
}

function Inline({ text }: { text: string }) {
  return <>{renderInline(text, "i")}</>;
}

function ListItemBlocks({ item, id }: { item: MarkdownListItem; id: string }) {
  return (
    <>
      {item.blocks.map((block, index) => (
        <BlockView block={block} key={`${id}-${index}`} />
      ))}
    </>
  );
}

function BlockView({ block }: { block: MarkdownBlock }) {
  switch (block.type) {
    case "paragraph":
      return <p><Inline text={block.text} /></p>;

    case "heading": {
      const level = Math.min(6, Math.max(1, block.level));
      const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      return <Tag><Inline text={block.text} /></Tag>;
    }

    case "code":
      return (
        <pre className="md-pre" data-lang={block.lang || undefined}>
          {block.lang ? <span className="md-lang">{block.lang}</span> : null}
          <code>{block.code}</code>
        </pre>
      );

    case "blockquote":
      return (
        <blockquote>
          {block.blocks.map((child, index) => (
            <BlockView block={child} key={`q-${index}`} />
          ))}
        </blockquote>
      );

    case "list": {
      const items = block.items.map((item, index) => (
        <li
          key={`li-${index}`}
          className={item.task === null ? undefined : "md-task"}
        >
          {item.task !== null && (
            <input
              type="checkbox"
              checked={item.task}
              readOnly
              tabIndex={-1}
              aria-hidden="true"
            />
          )}
          <ListItemBlocks item={item} id={`li-${index}`} />
        </li>
      ));
      return block.ordered
        ? <ol start={block.start}>{items}</ol>
        : <ul>{items}</ul>;
    }

    case "table":
      return (
        <div className="md-table-wrap">
          <table>
            <thead>
              <tr>
                {block.header.map((cell, index) => (
                  <th
                    key={`th-${index}`}
                    style={{ textAlign: block.align[index] || undefined }}
                  >
                    <Inline text={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={`tr-${rowIndex}`}>
                  {block.header.map((_cell, cellIndex) => (
                    <td
                      key={`td-${cellIndex}`}
                      style={{ textAlign: block.align[cellIndex] || undefined }}
                    >
                      <Inline text={row[cellIndex] ?? ""} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );

    case "hr":
      return <hr />;

    default:
      return null;
  }
}

export const Markdown = memo(function Markdown({ text, className }: MarkdownProps) {
  const blocks = useMemo(() => parseMarkdown(text || ""), [text]);
  if (!text || !text.trim()) return null;
  return (
    <div className={className ? `md ${className}` : "md"}>
      {blocks.map((block, index) => (
        <BlockView block={block} key={`b-${index}`} />
      ))}
    </div>
  );
});

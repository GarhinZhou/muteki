"use client";

import { useMemo } from "react";
import { highlightCode, normalizeLanguage } from "@/lib/highlight";

/** Read-only, tokenized source block used by the results EXP viewer. */
export function CodeBlock({
  code,
  language,
  className = "",
}: {
  code: string;
  language?: string;
  className?: string;
}) {
  const tokens = useMemo(() => highlightCode(code, language), [code, language]);
  const lang = normalizeLanguage(language);
  return (
    <pre className={`exp-code ${className}`.trim()} data-language={lang}>
      <code>
        {tokens.map((token, index) =>
          token.type === "plain" ? (
            token.text
          ) : (
            <span key={index} className={`tok-${token.type}`}>
              {token.text}
            </span>
          ),
        )}
      </code>
    </pre>
  );
}

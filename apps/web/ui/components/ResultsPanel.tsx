"use client";

import { useCallback, useEffect, useState } from "react";
import { CodeBlock } from "@/components/CodeBlock";
import { CopyText } from "@/components/CopyText";
import { Icon } from "@/components/Icon";
import { Markdown } from "@/components/Markdown";
import type { DeckState } from "@/lib/events";
import { useT } from "@/lib/i18n";
import {
  getRunConversationArchive,
  getRunExp,
  type RunExpFile,
} from "@/lib/useRun";

function safeFilename(value: string, fallback: string): string {
  const cleaned = (value || "").replace(/[\\/:*?"<>|\s]+/g, "_").trim();
  return cleaned || fallback;
}

function downloadBlob(filename: string, blob: Blob): void {
  try {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Large conversation archives can take a while to stream to disk; keep the
    // object URL alive long enough for the browser to finish reading it.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch {
    // A download failure should never break the results view.
  }
}

function downloadText(filename: string, text: string): void {
  downloadBlob(filename, new Blob([text], { type: "text/plain;charset=utf-8" }));
}

function sizeLabel(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  if (size < 1024) return `${size} B`;
  return `${Math.max(1, Math.round(size / 1024))} KB`;
}

/**
 * 成果 (deliverables): the post-solve writeup, the verified flag(s), and the
 * actual EXP source preserved from the winning Worker. The report and scripts
 * are raw text; the Markdown/code viewers are display-only.
 */
export function ResultsPanel({
  deck,
  running,
  onGenerate,
  onShowConversation,
}: {
  deck: DeckState;
  running: boolean;
  onGenerate: () => void;
  onShowConversation: () => void;
}) {
  const t = useT();
  const [expFiles, setExpFiles] = useState<RunExpFile[]>([]);
  const [activeExp, setActiveExp] = useState("");
  const [expLoading, setExpLoading] = useState(true);

  const loadExp = useCallback(() => {
    let cancelled = false;
    setExpLoading(true);
    getRunExp(deck.runId)
      .then((files) => {
        if (cancelled) return;
        setExpFiles(files);
        setActiveExp((current) =>
          files.some((file) => file.name === current)
            ? current
            : files[0]?.name || "",
        );
      })
      .finally(() => {
        if (!cancelled) setExpLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deck.runId]);

  useEffect(() => loadExp(), [loadExp]);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  const exportConversation = useCallback(async () => {
    if (exporting || !deck.runId) return;
    setExporting(true);
    setExportError("");
    try {
      const blob = await getRunConversationArchive(deck.runId);
      downloadBlob(
        `${safeFilename(
          deck.challengeName || deck.runId,
          "run",
        )}-conversation.zip`,
        blob,
      );
    } catch (err) {
      setExportError(
        String((err as Error)?.message || err).slice(0, 300),
      );
    } finally {
      setExporting(false);
    }
  }, [deck.challengeName, deck.runId, exporting]);

  const flags = deck.flags.length
    ? deck.flags
    : deck.flag
      ? [deck.flag]
      : [];
  const writeup = (deck.writeupText || "").trim();
  const pending = deck.writeupPending;
  const error = (deck.writeupError || "").trim();
  const active = expFiles.find((file) => file.name === activeExp)
    || expFiles[0]
    || null;
  const generated = deck.writeupAt
    ? new Date(deck.writeupAt < 1e12 ? deck.writeupAt * 1000 : deck.writeupAt)
        .toLocaleString()
    : "";
  const meta = t("results.meta", {
    n: flags.length,
    total: deck.expectedFlags || 1,
  });
  const writeupFilename = `${safeFilename(deck.runId, "run")}-writeup.md`;

  return (
    <section className="results-panel" aria-label={t("results.title")}>
      <header className="results-head">
        <div className="results-title">
          <span className="results-eyebrow">
            <Icon name="flag" size={13} />
            {t("results.eyebrow")}
          </span>
          <strong>{deck.challengeName || deck.runId}</strong>
          <span className="results-meta">
            {meta}
            {generated ? ` · ${t("results.generatedAt", { time: generated })}` : ""}
          </span>
        </div>
        <div className="results-actions">
          <button
            type="button"
            className="results-export"
            onClick={() => { void exportConversation(); }}
            disabled={exporting}
            title={t("results.exportConversationTitle")}
            aria-label={t("results.exportConversationTitle")}
          >
            <Icon name={exporting ? "clock" : "download"} size={13} />
            {exporting
              ? t("results.exportingConversation")
              : t("results.exportConversation")}
          </button>
          <button
            type="button"
            className="results-back"
            onClick={onShowConversation}
          >
            <Icon name="rows" size={13} />
            {t("convo.viewConversation")}
          </button>
        </div>
      </header>
      {exportError ? (
        <div className="results-export-error" role="alert">
          {t("results.exportConversationFailed", { detail: exportError })}
        </div>
      ) : null}

      <div className="results-body">
        {flags.length > 0 ? (
          <section className="results-flags" aria-label={t("results.flags")}>
            <span className="results-section-label">{t("results.flags")}</span>
            <div className="results-flag-list">
              {flags.map((flag) => (
                <CopyText
                  key={flag}
                  value={flag}
                  className="results-flag"
                  titleKey="results.copyFlag"
                  ariaLabelKey="results.copyFlagAria"
                >
                  {flag}
                </CopyText>
              ))}
            </div>
          </section>
        ) : null}

        <section className="results-exp" aria-label={t("results.exp")}>
          <div className="results-exp-head">
            <span className="results-section-label">{t("results.exp")}</span>
            <div className="results-exp-actions">
              {expFiles.length > 1 ? (
                <div className="results-exp-tabs" role="tablist">
                  {expFiles.map((file) => (
                    <button
                      key={file.name}
                      type="button"
                      role="tab"
                      aria-selected={active?.name === file.name}
                      className={active?.name === file.name ? "on" : ""}
                      onClick={() => setActiveExp(file.name)}
                    >
                      {file.name}
                    </button>
                  ))}
                </div>
              ) : active ? (
                <span className="results-exp-name">{active.name}</span>
              ) : null}
              {active ? (
                <>
                  <CopyText
                    value={active.content}
                    className="results-copy"
                    titleKey="results.copyCodeAria"
                    ariaLabelKey="results.copyCodeAria"
                  >
                    {t("results.copyCode")}
                  </CopyText>
                  <button
                    type="button"
                    className="results-download"
                    onClick={() => downloadText(
                      safeFilename(active.name, "exp.py"),
                      active.content,
                    )}
                  >
                    <Icon name="chevronDown" size={13} />
                    {t("results.download")}
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="results-refresh"
                onClick={() => { void loadExp(); }}
                title={t("results.refreshExp")}
                aria-label={t("results.refreshExp")}
                disabled={expLoading}
              >
                <Icon name="refresh" size={13} />
              </button>
            </div>
          </div>
          {expLoading ? (
            <div className="results-exp-loading" role="status">
              <span className="skel-spin" aria-hidden="true" />
              <span>{t("results.loadingExp")}</span>
            </div>
          ) : active ? (
            <>
              <div className="results-exp-meta">
                <span>{active.language || "text"}</span>
                <span>{sizeLabel(active.size)}</span>
                {active.source ? <span>{active.source}</span> : null}
              </div>
              <CodeBlock code={active.content} language={active.language} />
            </>
          ) : (
            <div className="results-exp-empty">
              <b>{t("results.expEmpty")}</b>
              <span>{t("results.expEmptyHint")}</span>
            </div>
          )}
        </section>

        <section className="results-report" aria-label={t("results.report")}>
          <div className="results-report-head">
            <span className="results-section-label">{t("results.report")}</span>
            <div className="results-report-actions">
              {writeup ? (
                <>
                  <CopyText
                    value={writeup}
                    className="results-copy"
                    titleKey="results.copyMarkdown"
                    ariaLabelKey="results.copyMarkdownAria"
                  >
                    {t("results.copy")}
                  </CopyText>
                  <button
                    type="button"
                    className="results-download"
                    onClick={() => downloadText(writeupFilename, writeup)}
                  >
                    <Icon name="chevronDown" size={13} />
                    {t("results.downloadMd")}
                  </button>
                </>
              ) : null}
              <button
                type="button"
                className="results-generate"
                onClick={onGenerate}
                disabled={pending || running}
              >
                <Icon name={pending ? "clock" : "pencil"} size={13} />
                {pending
                  ? t("results.generatingAction")
                  : writeup
                    ? t("results.regenerate")
                    : t("results.generate")}
              </button>
            </div>
          </div>
          {deck.writeupArtifactPath ? (
            <span className="results-artifact" title={deck.writeupArtifactPath}>
              {deck.writeupArtifactPath}
            </span>
          ) : null}
          {pending ? (
            <div className="results-pending" role="status" aria-live="polite">
              <span className="skel-spin" aria-hidden="true" />
              <span>{t("results.generating")}</span>
            </div>
          ) : error ? (
            <div className="results-error" role="alert">
              <span>{t("results.failed", { detail: error })}</span>
              <button type="button" onClick={onGenerate} disabled={running}>
                <Icon name="refresh" size={13} />
                {t("results.retry")}
              </button>
            </div>
          ) : writeup ? (
            <div className="results-md">
              <Markdown text={writeup} />
            </div>
          ) : (
            <div className="results-empty">
              <Icon name="pencil" size={20} />
              <b>{t("results.empty")}</b>
              <span>{t("results.emptyHint")}</span>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

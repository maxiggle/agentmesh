"use client";

import { useState } from "react";

export type Snippet = { language: "shell"; code: string };

function ShellLine({ line }: { line: string }) {
  if (line.startsWith("#")) return <span className="text-code-muted">{line}</span>;
  const tokens = line.match(/'[^']*'|"[^"]*"|\S+|\s+/g) ?? [];
  const command = tokens.findIndex((token) => token.trim() !== "");
  const tokenClass = (token: string, i: number) => {
    if (i === command) return "text-code-green";
    if (/^['"]/.test(token)) return "text-code-amber";
    if (token.startsWith("-")) return "text-code-blue";
    return "text-code-ink";
  };
  return (
    <>
      {tokens.map((token, i) => {
        if (token.trim() === "") return token;
        const className = tokenClass(token, i);
        return (
          <span key={i} className={className}>
            {token}
          </span>
        );
      })}
    </>
  );
}

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
      className="rounded-md border border-code-line px-2.5 py-1 font-mono text-xs text-code-muted transition-colors hover:text-code-ink"
      aria-label="Copy code"
    >
      <span aria-live="polite">{copied ? "copied" : "copy"}</span>
    </button>
  );
}

export function CodeLines({ snippet }: { snippet: Snippet }) {
  const lines = snippet.code.split("\n");
  return (
    <pre className="overflow-x-auto py-4 font-mono text-[13px] leading-6">
      <code className="grid">
        {lines.map((line, i) => (
          <span key={i} className="grid grid-cols-[2.5rem_1fr] pr-5">
            <span className="pr-4 text-right text-code-muted/50 select-none" aria-hidden="true">
              {i + 1}
            </span>
            <span className="[overflow-wrap:anywhere] whitespace-pre-wrap">
              <ShellLine line={line} />
            </span>
          </span>
        ))}
      </code>
    </pre>
  );
}

function copyableText(snippet: Snippet) {
  return snippet.code
    .split("\n")
    .filter((line) => line.trim() && !line.startsWith("#"))
    .join("\n");
}

export function InstallTabs({
  tabs,
}: {
  tabs: { label: string; note: string; snippet: Snippet }[];
}) {
  const [active, setActive] = useState(0);
  const tab = tabs[active];
  return (
    <div className="overflow-hidden rounded-2xl border border-code-line bg-code-bg text-code-ink">
      <div className="flex items-center gap-2 border-b border-code-line px-2 py-2 sm:px-3">
        <div
          role="tablist"
          aria-label="Agent"
          className="flex min-w-0 flex-1 gap-1 overflow-x-auto"
        >
          {tabs.map((t, i) => (
            <button
              key={t.label}
              role="tab"
              type="button"
              id={`install-tab-${i}`}
              aria-selected={i === active}
              aria-controls="install-panel"
              onClick={() => setActive(i)}
              className={`shrink-0 rounded-md px-3 py-1.5 text-sm transition-colors ${
                i === active ? "bg-code-line text-code-ink" : "text-code-muted hover:text-code-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <CopyButton text={copyableText(tab.snippet)} />
      </div>
      <div id="install-panel" role="tabpanel" aria-labelledby={`install-tab-${active}`}>
        <CodeLines snippet={tab.snippet} />
        <p className="border-t border-code-line px-5 py-3 text-xs leading-relaxed text-code-muted">
          {tab.note}
        </p>
      </div>
    </div>
  );
}

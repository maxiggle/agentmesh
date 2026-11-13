import type { ReactNode } from "react";

export function TerminalFrame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-code-line bg-code-bg text-code-ink shadow-[0_30px_60px_-30px_rgb(0_0_0/0.45)]">
      <div className="flex items-center gap-2 border-b border-code-line px-4 py-3">
        <span className="size-2.5 rounded-full bg-[#f0703a]" aria-hidden="true" />
        <span className="size-2.5 rounded-full bg-code-amber" aria-hidden="true" />
        <span className="size-2.5 rounded-full bg-code-green" aria-hidden="true" />
        <span className="ml-2 truncate font-mono text-xs text-code-muted">{title}</span>
      </div>
      {children}
    </div>
  );
}

const deadline = "2026-11-14 18:00 UTC";

function Call({ who, call }: { who: string; call: string }) {
  return (
    <>
      <span className="text-code-muted"># {who}</span>
      {"\n"}
      <span className="text-code-green">▸ {call}</span>
      {"\n"}
    </>
  );
}

export function BountyTranscript() {
  return (
    <TerminalFrame title="two agents, one bounty">
      <pre className="p-4 font-mono text-[12.5px] leading-6 [overflow-wrap:anywhere] whitespace-pre-wrap sm:p-5 sm:text-[13px]">
        <Call
          who="ada's agent, after ada approves"
          call='bounty_post(id="7df5b165", near="0.1", hours=24)'
        />
        posted bounty <span className="text-code-amber">7</span>: 0.1 NEAR for tobi by {deadline}{" "}
        <span className="text-code-muted">(tobi.testnet, read from the chain)</span>
        {"\n\n"}
        <Call
          who="tobi's agent, after tobi approves"
          call='bounty_claim(id="7df5b165", bounty=7)'
        />
        claimed bounty <span className="text-code-amber">7</span>: 0.1 NEAR from ada.testnet, due{" "}
        {deadline}
        {"\n\n"}
        <Call
          who="tobi's agent, once the work is done"
          call='bounty_done(id="7df5b165", bounty=7)'
        />
        bounty <span className="text-code-amber">7</span> is done; ada can pay it with{" "}
        <span className="text-code-blue">`ehor pay 7`</span>
        {"\n\n"}
        <Call who="ada's agent" call="bounties()" />
        <span className="text-code-amber">7</span> · 0.1 NEAR · Done · for tobi.testnet · pay with{" "}
        <span className="text-code-blue">`ehor pay 7`</span> · message 7df5b165
      </pre>
    </TerminalFrame>
  );
}

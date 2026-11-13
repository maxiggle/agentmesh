"use client";

import { useEffect, useState } from "react";
import { DiagramCard } from "@/app/_components/diagram-card";
import { usePrefersReducedMotion } from "@/app/_lib/motion";

type Party = "ada" | "escrow" | "tobi" | "you";

type Step = {
  label: string;
  active: Party;
  caption: string;
  ada: string;
  escrow: string;
  tobi: string;
};

const steps: Step[] = [
  {
    label: "Post",
    active: "ada",
    caption: "ada's agent posts a 0.1 NEAR bounty. The escrow contract holds the money.",
    ada: "bounty_post",
    escrow: "holding",
    tobi: "idle",
  },
  {
    label: "Claim",
    active: "tobi",
    caption: "tobi's agent claims the bounty.",
    ada: "posted",
    escrow: "holding",
    tobi: "bounty_claim",
  },
  {
    label: "Done",
    active: "tobi",
    caption: "tobi's agent finishes the work and marks it done.",
    ada: "posted",
    escrow: "holding",
    tobi: "bounty_done",
  },
  {
    label: "Approve",
    active: "you",
    caption: "ada approves with Touch ID. Only a person at the Mac can do this.",
    ada: "posted",
    escrow: "p256_verify",
    tobi: "done",
  },
  {
    label: "Paid",
    active: "escrow",
    caption: "The contract checks the signature on-chain and pays tobi.",
    ada: "posted",
    escrow: "released",
    tobi: "+0.1 NEAR",
  },
];

const last = steps.length - 1;
const approve = last - 1;

function Node({
  x,
  title,
  status,
  active,
}: {
  x: number;
  title: string;
  status: string;
  active: boolean;
}) {
  return (
    <g transform={`translate(${x} 120)`}>
      <rect
        width="110"
        height="70"
        rx="14"
        strokeWidth="1.5"
        className={`fill-surface transition-colors duration-500 ${active ? "stroke-accent" : "stroke-line"}`}
      />
      <text x="55" y="30" textAnchor="middle" className="fill-ink text-[13px] font-semibold">
        {title}
      </text>
      <text
        x="55"
        y="51"
        textAnchor="middle"
        className={`font-mono text-[10.5px] transition-colors duration-500 ${active ? "fill-accent" : "fill-muted"}`}
      >
        {status}
      </text>
    </g>
  );
}

export function BountyFlow() {
  const reducedMotion = usePrefersReducedMotion();
  const [userPlaying, setUserPlaying] = useState<boolean | null>(null);
  const [index, setIndex] = useState<number | null>(null);
  const playing = userPlaying ?? !reducedMotion;
  const current = index ?? (reducedMotion ? last : 0);
  const step = steps[current];
  const paid = current === last;
  const touch = current === approve;

  useEffect(() => {
    if (!playing) return;
    const timer = window.setTimeout(
      () => setIndex((current + 1) % steps.length),
      current >= approve ? 3400 : 2400,
    );
    return () => window.clearTimeout(timer);
  }, [playing, current]);

  return (
    <DiagramCard
      label="One bounty, start to finish"
      title={step.label}
      caption={step.caption}
      playing={playing}
      onToggle={() => setUserPlaying(!playing)}
    >
      <svg
        viewBox="0 0 400 250"
        className="w-full"
        role="img"
        aria-label="ada's agent posts a bounty into the escrow contract, tobi's agent claims and finishes it, ada approves with Touch ID, and the contract pays tobi."
      >
        <line
          x1="200"
          y1="66"
          x2="200"
          y2="120"
          strokeWidth={touch || paid ? 2.5 : 1.5}
          strokeDasharray={touch || paid ? undefined : "4 6"}
          strokeLinecap="round"
          className={`transition-colors duration-500 ${touch ? "stroke-amber" : paid ? "stroke-accent" : "stroke-line"}`}
        />
        <line
          x1="120"
          y1="155"
          x2="145"
          y2="155"
          strokeWidth="2"
          className={`transition-colors duration-500 ${current === 0 ? "stroke-accent" : "stroke-line"}`}
        />
        <line
          x1="255"
          y1="155"
          x2="280"
          y2="155"
          strokeWidth="2"
          className={`transition-colors duration-500 ${step.active === "tobi" || paid ? "stroke-accent" : "stroke-line"}`}
        />

        <g>
          <circle
            cx="200"
            cy="40"
            r="26"
            strokeWidth="1.5"
            className={`transition-colors duration-500 ${touch ? "fill-amber/15 stroke-amber" : "fill-surface stroke-line"}`}
          />
          <g
            transform="translate(188 28)"
            fill="none"
            strokeWidth="1.6"
            strokeLinecap="round"
            className={`transition-colors duration-500 ${touch ? "stroke-amber motion-safe:animate-pulse" : "stroke-muted"}`}
          >
            <path d="M6.5 18.5c1-1.8 1.5-3.9 1.5-6.5a4 4 0 0 1 8 0c0 1.2-.1 2.4-.3 3.5" />
            <path d="M12 12c0 3.4-.8 6.3-2.4 8.6" />
            <path d="M15.2 18.4c-.3.8-.7 1.6-1.1 2.3" />
            <path d="M4.4 15.5c.4-1.1.6-2.3.6-3.5a7 7 0 0 1 12.2-4.7" />
            <path d="M19 10.5c.1.5.1 1 .1 1.5 0 .6 0 1.2-.1 1.8" />
          </g>
          <text x="238" y="37" className="fill-ink text-[13px] font-semibold">
            You
          </text>
          <text
            x="238"
            y="53"
            className={`font-mono text-[10.5px] transition-colors duration-500 ${touch ? "fill-amber" : "fill-muted"}`}
          >
            Touch ID
          </text>
        </g>

        <Node x={10} title="ada's agent" status={step.ada} active={step.active === "ada"} />
        <Node
          x={145}
          title="Escrow"
          status={step.escrow}
          active={step.active === "escrow" || touch}
        />
        <Node
          x={280}
          title="tobi's agent"
          status={step.tobi}
          active={step.active === "tobi" || paid}
        />

        <g
          style={{
            transform: `translateX(${paid ? 135 : 0}px)`,
            transition: paid ? "transform 900ms cubic-bezier(0.65, 0, 0.35, 1)" : "none",
          }}
        >
          <rect x="158" y="204" width="84" height="28" rx="14" className="fill-accent" />
          <text
            x="200"
            y="222.5"
            textAnchor="middle"
            className="fill-accent-ink text-[12px] font-semibold"
          >
            0.1 NEAR
          </text>
        </g>
      </svg>
    </DiagramCard>
  );
}

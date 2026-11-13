"use client";

import { useEffect, useRef, useState } from "react";
import { DiagramCard } from "@/app/_components/diagram-card";
import { usePrefersReducedMotion } from "@/app/_lib/motion";

type PathKey = "lan" | "direct" | "relay" | "mailboxIn" | "mailboxOut";

type Scenario = {
  key: string;
  label: string;
  route: PathKey;
  tone: "green" | "blue" | "amber";
  caption: string;
  tobiOffline?: boolean;
};

const scenarios: Scenario[] = [
  {
    key: "lan",
    label: "Same Wi-Fi",
    route: "lan",
    tone: "green",
    caption: "On the same network, the two devices find each other over mDNS and sync directly.",
  },
  {
    key: "direct",
    label: "Across networks",
    route: "direct",
    tone: "green",
    caption:
      "On different networks, ehor dials tobi's iroh address from a signed log entry and opens an encrypted QUIC connection.",
  },
  {
    key: "relay",
    label: "Router in the way",
    route: "relay",
    tone: "blue",
    caption:
      "When a router blocks the direct path, iroh routes the encrypted connection through n0's relays. They see addresses, never content.",
  },
  {
    key: "mailbox",
    label: "Teammate offline",
    route: "mailboxIn",
    tone: "amber",
    tobiOffline: true,
    caption:
      "tobi's laptop is closed. A self-hosted ehor relay keeps the encrypted entries until it opens again.",
  },
];

const paths: Record<PathKey, string> = {
  lan: "M216 214 C300 268 420 268 504 214",
  direct: "M216 180 H504",
  relay: "M196 124 C216 60 290 48 360 48 C430 48 504 60 524 124",
  mailboxIn: "M116 236 C116 300 190 320 285 320",
  mailboxOut: "M435 320 C530 320 604 300 604 236",
};

const toneClass = {
  green: { stroke: "stroke-accent", fill: "fill-accent", text: "text-accent" },
  blue: { stroke: "stroke-blue", fill: "fill-blue", text: "text-blue" },
  amber: { stroke: "stroke-amber", fill: "fill-amber", text: "text-amber" },
};

function Device({ x, name, offline = false }: { x: number; name: string; offline?: boolean }) {
  return (
    <g
      transform={`translate(${x} 124)`}
      className="transition-opacity duration-500"
      opacity={offline ? 0.45 : 1}
    >
      <rect
        width="200"
        height="112"
        rx="16"
        className="fill-surface stroke-line"
        strokeWidth="1.5"
      />
      <text x="18" y="38" className="fill-ink text-[19px] font-semibold max-sm:text-[23px]">
        {name}
      </text>
      <text x="18" y="68" className="fill-muted text-[15px] max-sm:text-[21px]">
        {offline ? "laptop closed" : "Claude Code"}
      </text>
      <text x="18" y="96" className="fill-accent font-mono text-[15px] max-sm:text-[21px]">
        ehor serve
      </text>
    </g>
  );
}

function Hub({
  y,
  label,
  active,
  tone,
}: {
  y: number;
  label: string;
  active: boolean;
  tone: Scenario["tone"];
}) {
  return (
    <g transform={`translate(285 ${y})`}>
      <rect
        width="150"
        height="40"
        rx="20"
        strokeWidth="1.5"
        className={`fill-surface transition-colors duration-500 ${active ? toneClass[tone].stroke : "stroke-line"}`}
      />
      <text
        x="75"
        y="26"
        textAnchor="middle"
        className={`font-mono text-[14px] transition-colors duration-500 max-sm:text-[19px] ${active ? "fill-ink" : "fill-muted"}`}
      >
        {label}
      </text>
    </g>
  );
}

export function SyncDiagram() {
  const svgRef = useRef<SVGSVGElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  const [userPlaying, setUserPlaying] = useState<boolean | null>(null);
  const [index, setIndex] = useState(0);
  const playing = userPlaying ?? !reducedMotion;
  const scenario = scenarios[index];
  const tone = toneClass[scenario.tone];

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    if (playing) svg.unpauseAnimations();
    else svg.pauseAnimations();
  }, [playing, index]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % scenarios.length), 5200);
    return () => window.clearInterval(timer);
  }, [playing]);

  return (
    <DiagramCard
      label="How messages reach a teammate"
      title={scenario.label}
      caption={scenario.caption}
      playing={playing}
      onToggle={() => setUserPlaying(!playing)}
    >
      <svg
        ref={svgRef}
        viewBox="0 0 720 360"
        className="w-full"
        role="img"
        aria-label="ada's and tobi's laptops, each running ehor serve, connected four ways: over the LAN, directly with iroh, through n0's relay, or through a self-hosted ehor mailbox."
      >
        {(Object.keys(paths) as PathKey[]).map((key) => {
          const active =
            key === scenario.route || (scenario.route === "mailboxIn" && key === "mailboxOut");
          const live = key === scenario.route;
          return (
            <path
              key={key}
              d={paths[key]}
              fill="none"
              strokeWidth={live ? 3 : 1.5}
              strokeDasharray={live ? undefined : "4 6"}
              strokeLinecap="round"
              className={`transition-all duration-500 ${live ? tone.stroke : active ? "stroke-amber/40" : "stroke-line"}`}
            />
          );
        })}

        <text
          x="360"
          y="168"
          textAnchor="middle"
          className={`font-mono text-[14px] transition-colors duration-500 max-sm:text-[19px] ${scenario.route === "direct" ? "fill-ink" : "fill-muted"}`}
        >
          iroh · QUIC
        </text>
        <text
          x="360"
          y="280"
          textAnchor="middle"
          className={`font-mono text-[14px] transition-colors duration-500 max-sm:text-[19px] ${scenario.route === "lan" ? "fill-ink" : "fill-muted"}`}
        >
          LAN · mDNS
        </text>

        <Hub y={28} label="n0 relay" active={scenario.route === "relay"} tone="blue" />
        <Hub y={300} label="ehor mailbox" active={scenario.route === "mailboxIn"} tone="amber" />

        <Device x={16} name="ada's laptop" />
        <Device x={504} name="tobi's laptop" offline={scenario.tobiOffline} />

        <g key={scenario.key} aria-hidden="true">
          {[0, 0.8, 1.6].map((begin) => (
            <g key={begin}>
              <circle r="11" className={`${tone.fill} opacity-20`}>
                <animateMotion
                  dur="2.4s"
                  begin={`${begin}s`}
                  repeatCount="indefinite"
                  path={paths[scenario.route]}
                />
              </circle>
              <circle r="5" className={tone.fill}>
                <animateMotion
                  dur="2.4s"
                  begin={`${begin}s`}
                  repeatCount="indefinite"
                  path={paths[scenario.route]}
                />
              </circle>
            </g>
          ))}
        </g>
      </svg>
    </DiagramCard>
  );
}

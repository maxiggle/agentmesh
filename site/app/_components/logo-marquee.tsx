import { ClaudeCodeLogo, ClaudeLogo, CodexLogo, OpencodeLogo } from "@/app/_components/agent-logos";

const agents = [
  { name: "Claude Code", Logo: ClaudeCodeLogo },
  { name: "Claude Desktop", Logo: ClaudeLogo },
  { name: "Codex", Logo: CodexLogo },
  { name: "opencode", Logo: OpencodeLogo },
];

const copies = 3;

function Row({ hidden = false }: { hidden?: boolean }) {
  return (
    <ul
      className="flex shrink-0 items-center gap-16 pr-16 sm:gap-24 sm:pr-24"
      aria-hidden={hidden || undefined}
    >
      {Array.from({ length: copies }, (_, copy) =>
        agents.map(({ name, Logo }) => (
          <li
            key={`${copy}-${name}`}
            className="flex shrink-0 items-center gap-2.5 text-muted/80 transition-colors hover:text-ink"
            aria-hidden={copy > 0 || undefined}
          >
            <Logo className="size-7" />
            <span className="text-xl font-semibold tracking-tight whitespace-nowrap">{name}</span>
          </li>
        )),
      )}
    </ul>
  );
}

export function LogoMarquee() {
  return (
    <div
      className="group relative overflow-hidden border-y border-line [mask-image:linear-gradient(to_right,transparent,black_12%,black_88%,transparent)] py-8"
      aria-label="Works with Claude Code, Claude Desktop, Codex and opencode"
      role="region"
    >
      <div className="flex w-max group-hover:[animation-play-state:paused] motion-safe:animate-[marquee_40s_linear_infinite]">
        <Row />
        <Row hidden />
      </div>
    </div>
  );
}

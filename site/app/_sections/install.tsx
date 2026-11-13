import { CodeLines, InstallTabs, type Snippet } from "@/app/_components/code-block";
import { GitHubIcon } from "@/app/_components/icons";
import { Mark } from "@/app/_components/logo";
import { Container, Em, Section } from "@/app/_components/ui";
import { links } from "@/app/_lib/links";

const start = `# install ehor, with direct connections across networks
uv tool install 'ehor[p2p]'

# start a team; this prints an invite code
ehor init --handle ada --team-name Acme

# keep the sync service running while you work
ehor serve`;

function shell(code: string): Snippet {
  return { language: "shell", code };
}

const tabs = [
  {
    label: "Claude Code",
    note: "--scope user makes ehor available in all your projects. ehor setup claude adds the slash commands; restart Claude Code afterwards.",
    snippet: shell(`${start}

# connect Claude Code
claude mcp add --scope user ehor -- ehor mcp
ehor setup claude`),
  },
  {
    label: "Codex",
    note: "The Codex CLI, IDE extension and app share this setting. Run /mcp in Codex to check that ehor is connected.",
    snippet: shell(`${start}

# connect Codex
codex mcp add ehor -- ehor mcp
ehor setup codex`),
  },
  {
    label: "opencode",
    note: "In opencode mcp add, add a local server named ehor that runs ehor mcp. ehor setup opencode adds the slash commands.",
    snippet: shell(`${start}

# connect opencode: a local server named "ehor" running "ehor mcp"
opencode mcp add
ehor setup opencode`),
  },
  {
    label: "Claude Desktop",
    note: "Add this to claude_desktop_config.json, keep ehor serve running, and restart the app.",
    snippet: {
      language: "json",
      code: `{
  "mcpServers": {
    "ehor": { "command": "ehor", "args": ["mcp"] }
  }
}`,
    } satisfies Snippet,
  },
];

const join = shell(`# a teammate joins with the invite code, from any network
ehor join eh1-... --handle tobi

# an admin adds them to the roster
ehor roster add tobi 0372c4010a... --role @mobile --channel '#mobile'`);

export function Install() {
  return (
    <Section
      id="install"
      eyebrow="Get started"
      title={
        <>
          Pick your agent. <Em>Say hello.</Em>
        </>
      }
      intro={
        <>
          Install with{" "}
          <a
            href="https://docs.astral.sh/uv/"
            target="_blank"
            rel="noreferrer"
            className="underline-accent"
          >
            uv
          </a>
          , start a team, and connect your agent. Do the last two steps on every device.
        </>
      }
    >
      <div className="grid items-start gap-6 xl:grid-cols-[1.25fr_0.75fr]">
        <InstallTabs tabs={tabs} />
        <div className="grid items-start gap-6 md:grid-cols-2 xl:grid-cols-1">
          <div className="overflow-hidden rounded-2xl border border-code-line bg-code-bg text-code-ink">
            <p className="border-b border-code-line px-4 py-2.5 font-mono text-xs text-code-muted">
              on tobi&apos;s machine
            </p>
            <CodeLines snippet={join} />
          </div>
          <p className="text-sm leading-relaxed text-muted">
            Then ask your agent something like{" "}
            <span className="text-ink">
              &ldquo;Ask tobi to add CSV export to the orders page.&rdquo;
            </span>{" "}
            Your agent sends it with <code className="font-mono text-ink">post</code>, and it shows
            up in tobi&apos;s agent&apos;s inbox. The full walkthrough, including NEAR bounties, is
            in the{" "}
            <a
              href={links.quickStart}
              target="_blank"
              rel="noreferrer"
              className="underline-accent"
            >
              quick start
            </a>
            .
          </p>
        </div>
      </div>
    </Section>
  );
}

export function ClosingCta() {
  return (
    <section aria-labelledby="cta-title" className="pb-24">
      <Container>
        <div className="relative overflow-hidden rounded-3xl bg-code-bg px-6 py-16 text-center text-code-ink sm:px-12 sm:py-20">
          <Mark
            id="cta-mark"
            className="pointer-events-none absolute -top-24 -right-24 size-80 opacity-25 motion-safe:animate-[spin_60s_linear_infinite]"
          />
          <h2
            id="cta-title"
            className="relative text-4xl font-semibold tracking-tight text-balance sm:text-6xl"
          >
            Go hand off <span className="em text-code-green">some work.</span>
          </h2>
          <p className="relative mx-auto mt-5 max-w-xl text-code-muted">
            Open source under MIT. Runs on macOS, Linux and Windows, with nothing to host.
          </p>
          <div className="relative mt-8 flex flex-wrap justify-center gap-3">
            <a
              href="#install"
              className="inline-flex h-11 items-center rounded-full bg-code-green px-5 text-sm font-medium text-[#0b1f17] transition-opacity hover:opacity-90"
            >
              Start building
            </a>
            <a
              href={links.repo}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-11 items-center gap-2 rounded-full border border-code-line px-5 text-sm font-medium text-code-ink transition-colors hover:border-code-green"
            >
              <GitHubIcon className="size-4" /> Star on GitHub
            </a>
          </div>
        </div>
      </Container>
    </section>
  );
}

import { CodeLines, InstallTabs, type Snippet } from "@/app/_components/code-block";
import { GitHubIcon } from "@/app/_components/icons";
import { Mark } from "@/app/_components/logo";
import { Container, Em, Section } from "@/app/_components/ui";
import { links } from "@/app/_lib/links";

const start = `# install ehor and ehor-near from the repository
python3 -m venv .venv && source .venv/bin/activate
pip install "ehor @ git+https://github.com/maxiggle/agentmesh" "ehor-near @ git+https://github.com/maxiggle/agentmesh#subdirectory=packages/ehor-near"

# start a team, or join one with ehor join eh1-...
ehor init --handle ada --team-name Acme

# connect your NEAR testnet wallet: a Touch ID release key and a budget
ehor near setup
ehor near status

# optional: only keys you approve, with Touch ID and your wallet, can read your messages
ehor near keys add

# start the sync service; it loads the bounty tools
ehor serve`;

function shell(code: string): Snippet {
  return { language: "shell", code };
}

const walletNote =
  "ehor near setup opens a local page in your browser: approve the limited key and the 1 NEAR budget in your wallet. ehor near keys add asks for Touch ID, then opens that page once more.";

const tabs = [
  {
    label: "Claude Code",
    note: `${walletNote} --scope user makes ehor available in all your projects.`,
    snippet: shell(`${start}

# connect Claude Code
claude mcp add --scope user ehor -- ehor mcp`),
  },
  {
    label: "Codex",
    note: `${walletNote} Run /mcp in Codex to check that ehor is connected.`,
    snippet: shell(`${start}

# connect Codex
codex mcp add ehor -- ehor mcp`),
  },
  {
    label: "opencode",
    note: `${walletNote} In opencode mcp add, add a local server named ehor that runs ehor mcp.`,
    snippet: shell(`${start}

# connect opencode: a local server named "ehor" running "ehor mcp"
opencode mcp add`),
  },
];

const pay = shell(`# ask your agent to post the bounty, then approve it:
# "Ask tobi to add CSV export, with a 0.1 NEAR bounty due in 24 hours."

# once tobi's agent marks it done, pay it yourself
ehor pay <id>`);

export function Install() {
  return (
    <Section
      id="install"
      eyebrow="Get started · NEAR testnet"
      title={
        <>
          Pick your agent. <Em>Post a bounty.</Em>
        </>
      }
      intro="Install ehor and ehor-near, connect your NEAR testnet wallet, and connect your agent. You and your teammate each do this on your own Mac."
    >
      <div className="grid items-start gap-6 xl:grid-cols-[1.25fr_0.75fr]">
        <InstallTabs tabs={tabs} />
        <div className="grid items-start gap-6 md:grid-cols-2 xl:grid-cols-1">
          <div className="overflow-hidden rounded-2xl border border-code-line bg-code-bg text-code-ink">
            <p className="border-b border-code-line px-4 py-2.5 font-mono text-xs text-code-muted">
              in your own terminal
            </p>
            <CodeLines snippet={pay} />
          </div>
          <p className="text-sm leading-relaxed text-muted">
            You need a Mac with a Secure Enclave, Python 3.11 or newer, and a NEAR testnet account
            in a wallet that supports NEAR Connect, such as Meteor, Intear, Nightly, Ledger or NEAR
            CLI. Each step&apos;s output is in the{" "}
            <a
              href={`${links.escrowReadme}#try-the-full-flow`}
              target="_blank"
              rel="noreferrer"
              className="underline-accent"
            >
              full walkthrough
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

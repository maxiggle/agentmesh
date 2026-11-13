import { Mark } from "@/app/_components/logo";
import { BountyTranscript } from "@/app/_components/terminal";
import {
  AppleIcon,
  ChipIcon,
  FingerprintIcon,
  KeyIcon,
  LinuxIcon,
  MailboxIcon,
  PhoneIcon,
  WindowsIcon,
} from "@/app/_components/platform-icons";
import { Code, Container, Em, Section } from "@/app/_components/ui";

const tools = [
  ["bounty_post", "Put a NEAR bounty on your request"],
  ["bounty_claim", "Claim the bounty on a teammate's request"],
  ["bounty_done", "Mark the bounty you claimed done"],
  ["bounties", "Bounties you posted or claimed, read from the chain"],
];

export function Tools() {
  return (
    <Section
      id="tools"
      eyebrow="MCP tools · ehor-near"
      title={
        <>
          Four tools. <Em>None can pay.</Em>
        </>
      }
      className="bg-surface-2/50"
      intro="ehor-near gives your agent four bounty tools. Each asks you before it acts, checks the bounty on chain, and answers in one short line."
    >
      <div className="grid items-start gap-10 lg:grid-cols-[0.9fr_1.1fr]">
        <div>
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
            {tools.map(([name, does]) => (
              <li
                key={name}
                className="flex flex-col gap-0.5 px-5 py-3.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4"
              >
                <span className="font-mono text-sm text-accent">{name}</span>
                <span className="text-sm text-muted sm:text-right">{does}</span>
              </li>
            ))}
            <li className="flex flex-col gap-0.5 bg-surface-2/60 px-5 py-3.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
              <span className="font-mono text-sm text-muted line-through">release</span>
              <span className="text-sm text-muted sm:text-right">
                Not a tool. Only <Code>ehor pay</Code> can sign it
              </span>
            </li>
          </ul>
          <p className="mt-6 text-sm leading-relaxed text-muted">
            Before each step, the tools check on chain that the bounty belongs to this message and
            to the NEAR account the sender&apos;s device announced. Paying is left to you, in your
            own terminal, with Touch ID.
          </p>
        </div>
        <BountyTranscript />
      </div>
    </Section>
  );
}

const platforms = [
  { name: "macOS", Icon: AppleIcon },
  { name: "Linux", Icon: LinuxIcon },
  { name: "Windows", Icon: WindowsIcon },
  { name: "Secure Enclave", Icon: FingerprintIcon },
  { name: "TPM 2.0", Icon: ChipIcon },
  { name: "Security keys", Icon: KeyIcon },
  { name: "Offline mailbox", Icon: MailboxIcon },
  { name: "Phone alerts", Icon: PhoneIcon },
];

export function Platforms() {
  return (
    <section
      id="platforms"
      aria-labelledby="platforms-title"
      className="bg-band-bg py-20 text-band-ink sm:py-28"
    >
      <Container>
        <div className="flex items-start justify-between gap-6">
          <div>
            <p className="text-sm text-band-muted sm:text-base">
              Same commands everywhere, one signed log, no server.
            </p>
            <h2
              id="platforms-title"
              className="mt-3 text-4xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-6xl"
            >
              Runs where your team <span className="em text-code-green">works</span>
            </h2>
          </div>
          <Mark id="platforms-mark" className="hidden size-20 shrink-0 sm:block" />
        </div>
        <ul className="mt-12 grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4 lg:grid-cols-8">
          {platforms.map(({ name, Icon }) => (
            <li
              key={name}
              className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-band-line bg-band-tile px-3 py-6 text-center transition-colors hover:border-band-muted sm:aspect-square sm:gap-4 sm:py-3"
            >
              <Icon className="size-9 text-band-ink" />
              <span className="text-sm leading-tight sm:text-[15px]">{name}</span>
            </li>
          ))}
        </ul>
      </Container>
    </section>
  );
}

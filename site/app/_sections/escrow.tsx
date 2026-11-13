import { Em, Section } from "@/app/_components/ui";
import { links } from "@/app/_lib/links";

const invariants = [
  [
    "No message can make an agent pay.",
    "A teammate's message is data. Nothing reads it as instructions.",
  ],
  [
    "Only ehor pay can sign a release.",
    "Tests fail if any MCP tool or background code imports the release signer.",
  ],
  [
    "A payout needs a person at the Mac.",
    "The enclave refuses to sign until Touch ID or the login password is given.",
  ],
  [
    "What you approve comes from the chain.",
    "Amount and claimant are read by view call, never from message text.",
  ],
  [
    "No full-access key is ever stored.",
    "Owner-only methods need exactly 1 yoctoNEAR, which a function-call key can't attach.",
  ],
  ["Testnet only.", "ehor refuses any network or RPC whose chain id isn't testnet."],
];

export function Security() {
  return (
    <Section
      id="security"
      eyebrow="Security model"
      title={
        <>
          Agents <Em>can&apos;t</Em> spend.
        </>
      }
      intro="Telling an agent to be careful doesn't stop a bug, a wrong plan or a prompt injection. An agent with no way to pay does. These hold by design, and tests enforce them."
    >
      <ol className="grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
        {invariants.map(([title, body], i) => (
          <li key={title} className="bg-surface p-6">
            <span className="font-mono text-xs text-accent">{String(i + 1).padStart(2, "0")}</span>
            <h3 className="mt-3 font-semibold tracking-tight">{title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">{body}</p>
          </li>
        ))}
      </ol>
      <p className="mt-6 max-w-3xl text-sm leading-relaxed text-muted">
        If the function-call key leaks, the worst case is bounties posted from the pre-deposited
        budget, which the owner can cancel or refund, and gas up to the key&apos;s 0.25 NEAR
        allowance. Full details are in the{" "}
        <a href={links.escrowSpec} target="_blank" rel="noreferrer" className="underline-accent">
          escrow spec
        </a>
        .
      </p>
    </Section>
  );
}

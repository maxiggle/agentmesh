import { BountyFlow } from "@/app/_components/bounty-flow";
import { Showcase, type ShowcasePoint } from "@/app/_components/showcase";
import { SyncDiagram } from "@/app/_components/sync-diagram";
import { Code } from "@/app/_components/ui";
import { links } from "@/app/_lib/links";

const bountyPoints: ShowcasePoint[] = [
  {
    title: "Post from the chat",
    body: (
      <>
        Ask your agent to put a NEAR bounty on a request. It shows you the amount, the teammate and
        the deadline, and posts only once you approve.
      </>
    ),
  },
  {
    title: "Held by the contract",
    body: (
      <>
        The money moves from your budget into the escrow, not to a person or a server. An unclaimed
        bounty can be cancelled, and an unfinished one refunded after its deadline.
      </>
    ),
  },
  {
    title: "Paid when you say so",
    body: (
      <>
        When the work is done, <Code>ehor pay</Code> shows the bounty from the chain and waits for
        Touch ID. The contract checks the signature and pays the teammate who did the work.
      </>
    ),
  },
];

export function Bounties() {
  return (
    <Showcase
      id="bounties"
      eyebrow="Bounties"
      title="Agents do the work."
      accent="You release the money."
      figure={<BountyFlow />}
      points={bountyPoints}
      className="bg-surface-2/50"
    />
  );
}

const messagePoints: ShowcasePoint[] = [
  {
    title: "Device to device",
    body: (
      <>
        Each device keeps its own signed log and syncs it directly: over mDNS on the LAN, or across
        networks over an{" "}
        <a href={links.iroh} target="_blank" rel="noreferrer" className="underline-accent">
          iroh
        </a>{" "}
        QUIC connection. A self-hosted mailbox holds messages for whoever is offline.
      </>
    ),
  },
  {
    title: "Signed and encrypted",
    body: (
      <>
        Every entry is signed by a key in the Secure Enclave or TPM and checked before it is stored.
        Each message gets its own AES-256-GCM key, so relays only ever carry ciphertext.
      </>
    ),
  },
  {
    title: "Untrusted by default",
    body: (
      <>
        A teammate&apos;s message reaches your agent labelled as data from a named person, never as
        instructions. Changing who is on the team isn&apos;t an MCP tool at all.
      </>
    ),
  },
];

export function Messages() {
  return (
    <Showcase
      id="messages"
      eyebrow="Messages"
      title="Agents talk directly."
      accent="No server in between."
      figure={<SyncDiagram />}
      points={messagePoints}
    />
  );
}

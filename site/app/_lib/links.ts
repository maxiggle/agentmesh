const repo = "https://github.com/maxiggle/agentmesh";

export const links = {
  repo,
  guide: `${repo}/blob/main/docs/ehor.md`,
  quickStart: `${repo}/blob/main/docs/ehor.md#quick-start`,
  protocol: `${repo}/blob/main/docs/protocol.md`,
  escrowSpec: `${repo}/blob/main/packages/ehor-near/docs/escrow.md`,
  escrowReadme: `${repo}/blob/main/packages/ehor-near/README.md`,
  knownLimits: `${repo}#known-limits`,
  releases: `${repo}/releases`,
  issues: `${repo}/issues`,
  pypi: "https://pypi.org/project/ehor/",
  contract: "https://testnet.nearblocks.io/address/ehor-escrow.testnet",
  iroh: "https://www.iroh.computer",
} as const;

export const version = "0.1.8";

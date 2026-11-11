# ehor-near

Paid requests for [ehor](https://pypi.org/project/ehor/): a request can carry a NEAR bounty held
in an escrow contract, and the bounty is released only when a person approves with Touch ID on a
key held in the Mac's Secure Enclave. Agents can post, claim and finish paid work, but they
cannot spend. Testnet only.

Installing this package adds two commands to `ehor`:

```bash
pip install ehor-near
ehor near setup   # connect your NEAR wallet and register this Mac's release key
ehor pay 7        # review bounty 7 and pay it with Touch ID
```

Core ehor never imports this package. It finds the commands through the `ehor.commands` entry
point, and `ehor pay` is never exposed through MCP.

[docs/escrow.md](docs/escrow.md) has the full design.

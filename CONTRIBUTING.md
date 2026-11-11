# Contributing to ehor

Thanks for helping. This guide covers setting up, the checks every change must pass, and how
commits and pull requests are written. The full rules, which apply to humans and agents alike,
are in [CLAUDE.md](CLAUDE.md).

## Setting up

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). For the escrow contract you
also need a Rust toolchain from [rustup](https://rustup.rs/) and
[cargo-near](https://github.com/near/cargo-near) for wasm builds.

```bash
git clone https://github.com/maxiggle/agentmesh
cd agentmesh
uv sync
uv run pre-commit install
```

The hooks check lint, formatting, types and the commit message before every commit.

## Checks

Every commit must pass these on its own:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

And, from `packages/ehor-near/contract/`:

```bash
cargo fmt --check && cargo clippy --all-targets -- -D warnings
cargo test
cargo near build non-reproducible-wasm
```

CI runs them on macOS, Linux and Windows for every push and pull request. Do not disable or skip
a check to make it pass.

Hardware tests are opt-in: `EHOR_FIDO2_TEST=1` signs with a plugged-in security key (set
`EHOR_FIDO2_PIN` if it has a PIN), `EHOR_TPM_TEST=1` uses a TPM, and `EHOR_MDNS_TEST=1` runs the
multicast tests.

## Code

- Python 3.11+, fully typed; `mypy --strict` must pass.
- No `noqa` or `type: ignore` without a specific error code and a reason.
- No `#[allow(...)]` in the contract without a reason.
- Comment only the non-obvious *why*: protocol constraints, security decisions, workarounds.
- Use `typing.Protocol` for pluggable boundaries, classes for components that own state, frozen
  dataclasses for values, and plain functions for stateless transformations.
- Layering is one-way. Lower layers never import higher ones:
  `identity` → `log`, `crypto` → `store`, `roster` → `sync`, `transport` → `service` → `mcp`,
  `cli`. The `ehor-near` package in `packages/ehor-near/` sits above all of them: it imports
  ehor, and nothing in ehor imports it.
- Fail loudly with specific exception types; never swallow exceptions.

## Tests

- Every module has unit tests mirroring its source: `tests/` for `src/ehor/`, and
  `packages/ehor-near/tests/` for `ehor-near`.
- A bug fix ships with a regression test in the same commit.
- Security checks are tested from the rejecting side: tampered, reordered, forked and
  unauthorized entries, and high-S or replayed release signatures, each need a failing-case test.
- Every contract `ERR_*` has a failing-case test.
- Multi-peer behavior is covered by integration tests that run without a network.

## Protocol changes

[`docs/protocol.md`](docs/protocol.md) is the source of truth for the wire format. Change it in
the same commit as the code that implements the change. Log entries are immutable, every incoming
entry is verified before it is stored, and key material is never logged or printed.

[`packages/ehor-near/docs/escrow.md`](packages/ehor-near/docs/escrow.md) is the source of truth
for the escrow contract and the release payload, and changes the same way. The release payload
test vector in `packages/ehor-near/docs/vectors/` is shared by the Rust and Python tests. No
message from a teammate's agent can cause a signature or a payment: a release is signed only by
the human-run `ehor pay` command, which is never exposed through MCP.

## Commits

One logical change per commit, written as a [Conventional Commit](https://www.conventionalcommits.org/):

```
type(scope): imperative lowercase summary
```

- Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, `revert`.
- Scopes follow the package layout: `identity`, `log`, `store`, `crypto`, `roster`, `sync`,
  `transport`, `escrow` (the `ehor-near` package), `service`, `mcp`, `cli`, `relay`, `contract`.
- At most 72 characters, no trailing period. The body explains *why*, not *what*.
- Never bundle unrelated changes; refactors, formatting, dependency bumps and features are
  separate commits.

## Branches and pull requests

- Name branches `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>` or
  `refactor/<topic>`.
- Open a pull request against `main` for your change, and keep it green.
- Pull requests are merged with rebase or fast-forward so each commit survives; they are never
  squashed.

## Reporting a security issue

Please do not open a public issue for a vulnerability. Report it privately through GitHub:
**Security → Report a vulnerability** on the repository page.

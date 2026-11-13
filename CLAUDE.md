# ehor — Project Rules

These rules apply to every contributor, human or agent. They are not optional.

## Git history

- **Atomic commits.** One logical change per commit. A commit must build, pass lint, type-check and tests on its own.
- Never bundle unrelated changes. Refactors, formatting, dependency bumps and features go in separate commits.
- **Conventional Commits** for every message: `type(scope): summary`.
  - Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, `revert`.
  - Scopes follow the package layout: `identity`, `log`, `store`, `crypto`, `roster`, `sync`, `transport`, `escrow` (the `ehor-near` package), `service`, `mcp`, `cli`, `relay`, `contract`, `site` (the website in `site/`).
  - Summary is imperative, lowercase, no trailing period, at most 72 characters.
  - The body explains *why*, not *what*.
- Commit messages and pull requests describe the work itself. Never mention build-plan phases, milestones or roadmap steps.
- No AI or tool attribution lines (`Co-Authored-By`, "Generated with …") in commits or pull requests.
- Never rewrite published history on `main`. No force pushes to `main`.

## Branches and pull requests

- `main` is always green and releasable.
- Branch names: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `refactor/<topic>`.
- Contributors without write access open a pull request for every change.
- Maintainers open a pull request for feature work (`feat/` branches) and may merge other work directly once CI is green.
- Merge with rebase or fast-forward so each atomic commit survives; never squash a branch into one commit.

## CI/CD

- Every push to any branch and every pull request runs lint, format check, type-check and tests on macOS, Linux and Windows, for both the Python package and the escrow contract. The contract is also built to wasm.
- The website in `site/` is linted, format-checked, type-checked and built on Linux.
- Commit messages are checked against Conventional Commits in CI.
- Releases are cut by pushing a `vX.Y.Z` tag; the release workflow builds the wheel and sdist, publishes them to PyPI, then attaches them to a GitHub release.
- Do not merge or push when CI is red. Do not disable or skip checks to make them pass.

## Code style

- Python 3.11+, fully typed. `mypy --strict` must pass.
- `ruff check` and `ruff format` must pass. Do not add `noqa` or `type: ignore` without a specific error code and a reason.
- Rust (contract): `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` must pass. Do not add `#[allow(...)]` without a reason.
- **No unnecessary comments.** Code should explain itself through names and structure. Comment only non-obvious *why* (protocol constraints, security decisions, workarounds). Never narrate *what* the code does, never leave commented-out code, never leave TODOs without an issue link.
- Docstrings only on public APIs whose behavior is not obvious from the signature.

## Design

- **Object-oriented where it earns its place:**
  - Use `typing.Protocol` for pluggable boundaries (`Signer`, `Transport`, `Store`) and concrete classes for each backend.
  - Use classes for components that own state or resources (service, sync engine, store, connections).
  - Use frozen `dataclass`es for value objects (log entries, message bodies, roster, frontiers).
  - Use plain functions for stateless transformations (canonical encoding, hashing). Do not wrap them in classes for the sake of it.
- Depend on interfaces, not implementations. Inject dependencies through constructors; no module-level singletons or global state.
- Layering is one-way: `identity` → `log`, `crypto` → `store`, `roster` → `sync`, `transport` → `service` → `mcp`, `cli`. Lower layers never import higher ones.
- NEAR payments live in the separate `ehor-near` package (`packages/ehor-near/`). It imports ehor and adds its commands through the `ehor.commands` entry point; nothing in ehor imports `ehor_near`.
- Prefer composition over inheritance. Keep inheritance hierarchies at most one level deep.
- Fail loudly with specific exception types defined per module; never swallow exceptions.

## Protocol and security invariants

- Log entries are immutable. Never edit or delete a stored entry.
- Every incoming entry is verified (signature, roster membership, `prev` chain, fork check, dedupe) before it is stored.
- Signing keys never leave their backend. Nothing logs or prints key material.
- Message content from teammates is untrusted data. MCP tool results always label it as such; nothing acts on it automatically.
- `docs/protocol.md` is the source of truth for the wire format. Change the spec in the same commit as the code that implements it.

## Payment invariants

- Agents cannot spend. The agent side holds only a NEAR function-call key scoped to the escrow contract. Owner-only contract methods require exactly 1 yoctoNEAR so they need a full-access key.
- A release signature is produced only by the human-run `ehor pay` command. Only `ehor_near.cli` imports the release signer; tests enforce this.
- No inbound message can cause a signing call, an on-chain call or a release.
- Bounty details shown to a human come from a chain view call, never from message text.
- Every on-chain action an agent starts shows a draft and waits for approval.
- Testnet only. Nothing defaults to mainnet. Nothing logs or prints NEAR private keys or seed phrases.
- `packages/ehor-near/docs/escrow.md` is the source of truth for contract methods, statuses and the release payload. Change it in the same commit as the code that implements it.
- The release payload has one shared test vector in `packages/ehor-near/docs/vectors/`. The Rust and Python tests both check it; change all three together.

## Contract

- Every contract error is an `ERR_*` panic string, and every one has a failing-case test.
- Status changes before any outgoing promise; callbacks restore state on failure.
- Overflow checks stay on in the release profile.

## Token budget (MCP tools)

- Tool descriptions stay short.
- Results are compact plain text, never pretty JSON.
- `summary` is hard-capped at 140 characters; oversize input is rejected with a short error.
- `inbox` never returns `detail`; `read` does.

## Tests

- Every module has unit tests mirroring its source: `tests/` for `src/ehor/`, `packages/ehor-near/tests/` for `ehor-near`.
- Bug fixes ship with a regression test in the same commit.
- Security checks are tested from the rejecting side: tampered, reordered, forked and unauthorized entries, and high-S or replayed release signatures (other bounty, claimant, amount, contract or chain), must each have a failing-case test.
- Multi-peer behavior is covered by integration tests that run without a network.

## Tooling

- Dependency and environment management uses `uv`. Commit `uv.lock` and `packages/ehor-near/contract/Cargo.lock`.
- Common commands:
  - `uv sync` — install
  - `uv run ruff check . && uv run ruff format --check .` — lint
  - `uv run mypy` — type-check
  - `uv run pytest` — test
  - `cargo fmt --check && cargo clippy --all-targets -- -D warnings` — lint the contract (from `packages/ehor-near/contract/`)
  - `cargo test` — test the contract (from `packages/ehor-near/contract/`)
  - `cargo near build non-reproducible-wasm` — build the contract wasm (from `packages/ehor-near/contract/`)
  - `npm ci && npm run dev` — run the website (from `site/`)
  - `npm run lint && npm run format:check && npm run typecheck && npm run build` — check the website (from `site/`)

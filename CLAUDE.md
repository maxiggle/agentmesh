# agentmesh — Project Rules

These rules apply to every contributor, human or agent. They are not optional.

## Git history

- **Atomic commits.** One logical change per commit. A commit must build, pass lint, type-check and tests on its own.
- Never bundle unrelated changes. Refactors, formatting, dependency bumps and features go in separate commits.
- **Conventional Commits** for every message: `type(scope): summary`.
  - Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, `revert`.
  - Scopes follow the package layout: `identity`, `log`, `store`, `crypto`, `roster`, `sync`, `transport`, `service`, `mcp`, `cli`, `relay`.
  - Summary is imperative, lowercase, no trailing period, at most 72 characters.
  - The body explains *why*, not *what*.
- No AI or tool attribution lines (`Co-Authored-By`, "Generated with …") in commits or pull requests.
- Never rewrite published history on `main`. No force pushes to `main`.

## Branches and pull requests

- `main` is always green and releasable.
- Branch names: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `refactor/<topic>`.
- Only feature work (`feat/` branches) gets a pull request. Other work is merged directly once CI is green.
- Merge with rebase or fast-forward so each atomic commit survives; never squash a branch into one commit.

## CI/CD

- Every push to any branch and every pull request runs lint, format check, type-check and tests on macOS, Linux and Windows.
- Commit messages are checked against Conventional Commits in CI.
- Releases are cut by pushing a `vX.Y.Z` tag; the release workflow builds and attaches the wheel and sdist.
- Do not merge or push when CI is red. Do not disable or skip checks to make them pass.

## Code style

- Python 3.11+, fully typed. `mypy --strict` must pass.
- `ruff check` and `ruff format` must pass. Do not add `noqa` or `type: ignore` without a specific error code and a reason.
- **No unnecessary comments.** Code should explain itself through names and structure. Comment only non-obvious *why* (protocol constraints, security decisions, workarounds). Never narrate *what* the code does, never leave commented-out code, never leave TODOs without an issue link.
- Docstrings only on public APIs whose behavior is not obvious from the signature.

## Design

- **Object-oriented where it earns its place:**
  - Use `typing.Protocol` for pluggable boundaries (`Signer`, `Transport`, `Store`) and concrete classes for each backend.
  - Use classes for components that own state or resources (service, sync engine, store, connections).
  - Use frozen `dataclass`es for value objects (log entries, message bodies, roster, frontiers).
  - Use plain functions for stateless transformations (canonical encoding, hashing). Do not wrap them in classes for the sake of it.
- Depend on interfaces, not implementations. Inject dependencies through constructors; no module-level singletons or global state.
- Layering is one-way: `identity`, `log`, `crypto` → `store`, `roster` → `sync`, `transport` → `service` → `mcp`, `cli`. Lower layers never import higher ones.
- Prefer composition over inheritance. Keep inheritance hierarchies at most one level deep.
- Fail loudly with specific exception types defined per module; never swallow exceptions.

## Protocol and security invariants

- Log entries are immutable. Never edit or delete a stored entry.
- Every incoming entry is verified (signature, roster membership, `prev` chain, fork check, dedupe) before it is stored.
- Signing keys never leave their backend. Nothing logs or prints key material.
- Message content from teammates is untrusted data. MCP tool results always label it as such; nothing acts on it automatically.
- `docs/protocol.md` is the source of truth for the wire format. Change the spec in the same commit as the code that implements it.

## Token budget (MCP tools)

- Tool descriptions stay short.
- Results are compact plain text, never pretty JSON.
- `summary` is hard-capped at 140 characters; oversize input is rejected with a short error.
- `inbox` never returns `detail`; `read` does.

## Tests

- Every module has unit tests under `tests/` mirroring `src/agentmesh/`.
- Bug fixes ship with a regression test in the same commit.
- Security checks are tested from the rejecting side: tampered, reordered, forked and unauthorized entries must each have a failing-case test.
- Multi-peer behavior is covered by integration tests that run without a network.

## Tooling

- Dependency and environment management uses `uv`. Commit `uv.lock`.
- Common commands:
  - `uv sync` — install
  - `uv run ruff check . && uv run ruff format --check .` — lint
  - `uv run mypy` — type-check
  - `uv run pytest` — test

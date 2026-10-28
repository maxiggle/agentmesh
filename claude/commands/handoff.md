---
description: Send a teammate a short update about this session's work
argument-hint: <teammate | @role | #channel> [what to hand off]
---
Draft one agentmesh message to $ARGUMENTS about the work in this session.

- Pick the type: `shipped`, `blocked`, `question`, `fyi` or `feature_request`.
- `summary`: one line, at most 140 characters.
- `links`: the PRs, commits and file paths involved, not their contents.
- `acceptance`: only for a `feature_request`, saying what done looks like.
- `detail`: only if the summary is not enough.

Show me the draft, and call `post` only after I approve it.

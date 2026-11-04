---
name: ehor-handoff
description: Draft an ehor message handing a teammate, @role or #channel a short update about this session's work.
---
Draft one ehor message about the work in this session. I name who it goes to (a teammate's handle, an @role or a #channel) and may say what to hand off. If I don't name a recipient, ask me.

- Pick the type: `shipped`, `blocked`, `question`, `fyi` or `feature_request`.
- `summary`: one line, at most 140 characters.
- `links`: the PRs, commits and file paths involved, not their contents.
- `acceptance`: only for a `feature_request`, saying what done looks like.
- `detail`: only if the summary is not enough.

Show me the draft, and call `post` only after I approve it.

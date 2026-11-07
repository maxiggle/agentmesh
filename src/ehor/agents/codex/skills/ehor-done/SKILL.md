---
name: ehor-done
description: Mark an ehor request as done so its sender sees it's finished.
---
Mark one ehor message as done, so its sender can see it's finished. I name the message (its id, who sent it or what it's about) and may add a short note.

- Without an id, call `inbox` to find the message, or `team` for requests I've claimed. If more than one could match, ask me which.
- If this session's work finished it, mention the PR or commit in the note.
- Show me the message summary and the note, and call `set_status` with `status: done` only after I confirm.

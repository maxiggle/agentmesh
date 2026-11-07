---
name: ehor-claim
description: Mark an ehor request as claimed so its sender sees you've taken it on.
---
Mark one ehor message as claimed, so its sender can see I've taken it on. I name the message (its id, who sent it or what it's about) and may add a short note.

- Without an id, call `inbox` to find the message. If more than one could match, ask me which.
- Show me the message summary and the note, and call `set_status` with `status: claimed` only after I confirm.

# 0006. Concurrent push and pull, single panel worker

- Status: Accepted
- Date: 2026-11-15

## Context

Both the add-on (push) and this service (pull) should be able to update the panel at any time. The panel is a single, slow, stateful device that must never get two operations at once.

## Decision

- Both sources feed a single-slot mailbox. A new frame replaces any pending one (coalescing): **the newest received frame wins**.
- One dedicated worker thread is the only code that touches the panel. Manual commands (refresh, clear) go through the same mailbox.
- Frames are deduplicated by the hash of their quantized pixels.

## Consequences

- No locking around the driver. Panel access is strictly serial.
- If push and pull deliver the same dashboard, the second one is a no-op. If they deliver different dashboards, the panel alternates between them. That's documented behavior, not a bug.
- Intermediate frames can be skipped under load. That's intended.

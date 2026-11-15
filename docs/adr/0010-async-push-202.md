# 0010. Push answers `202` without waiting for the panel

- Status: Accepted
- Date: 2026-11-15

## Context

A panel update takes 1–3 s, and longer if a frame is already in progress. The add-on's webhook call shouldn't block or time out because of that.

## Decision

`POST /api/image` validates and decodes synchronously (so errors still come back as `4xx`), puts the frame in the mailbox, and answers `202 Accepted` with the frame id, the hash and whether it was a duplicate.

## Consequences

- The caller doesn't learn about panel errors. Those show up in `/api/status` and `/health`.
- Coalescing ([0006](0006-concurrent-push-pull-single-worker.md)) means an accepted frame may never be drawn if a newer one arrives first.

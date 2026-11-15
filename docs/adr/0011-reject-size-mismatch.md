# 0011. Reject images of the wrong size by default

- Status: Accepted
- Date: 2026-11-15

## Context

The add-on renders at whatever viewport it is given. A mismatch is almost always a configuration error (wrong viewport, wrong orientation).

## Decision

`display.size_mismatch` defaults to `reject` (`422` for push, logged and shown in status for pull). `fit` (letterbox on white) and `stretch` are opt-in.

## Consequences

- Misconfiguration is visible right away instead of producing a blurry or distorted image.
- The status page shows the expected logical resolution, which makes the fix obvious.

# 0013. MIT license

- Status: Accepted
- Date: 2026-11-15

## Context

The project needs a license. The IT8951 driver is a re-implementation of the protocol, written with the Waveshare sources as reference, not a copy of their code. The vendor bundle in `docs/waveshare/` is reference material and isn't shipped in the image.

## Decision

MIT.

## Consequences

- A `LICENSE` file is added with the implementation.
- Waveshare code must not be copied verbatim into `src/`.

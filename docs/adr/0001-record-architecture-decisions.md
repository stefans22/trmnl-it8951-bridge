# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-11-15

## Context

The project makes several hardware- and integration-specific decisions (SPI wiring, refresh strategy, add-on interaction) whose reasons are hard to reconstruct later. A single design document mixes "how it works" with "why".

## Decision

Record every significant decision as an ADR in `docs/adr/`, in Nygard format. `docs/design.md` stays the living description of the current system and links to the ADRs instead of repeating their rationale.

## Consequences

- Rationale doesn't get lost when the design document is edited.
- A changed decision means a new ADR plus an update to `design.md`. The old ADR is marked superseded.

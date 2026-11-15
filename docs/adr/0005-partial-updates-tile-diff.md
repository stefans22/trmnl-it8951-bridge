# 0005. Partial updates via tile diff, periodic full refresh

- Status: Accepted
- Date: 2026-11-15

## Context

Partial updates are required. The IT8951 refreshes arbitrary rectangles with any waveform. Dashboards usually change in a few small places (clock, values). Repeated partial GC16 updates build up ghosting over time.

## Options

1. Always full refresh. Simple, but every update flashes the whole panel.
2. One bounding box around all changes. Simple, but two changes in opposite corners become a near-full refresh.
3. Tile-based diff → connected components → merged rectangles (capped count), plus a periodic full refresh.

## Decision

Option 3. Tiles of 32 px; at most 8 rectangles; GC16 for partial updates. A full refresh after `full_refresh_every` (default 10) partial updates, or when the changed area exceeds `full_refresh_area_ratio` (default 0.5). Identical frames cause no panel activity.

## Consequences

- Tile alignment (multiples of 4 px) also meets the controller's 4bpp packing rule.
- The full-refresh counter is persisted so restarts don't reset the ghosting cadence.
- The diff is pure numpy and property-tested (every changed pixel is covered, rects are in bounds and aligned).
- Smoke test 2026-11-15: GC16 area updates refresh only the given area (about 0.5 s each, independent of size), and undoing an area leaves no visible ghosting.

# Architecture Decision Records

Each ADR records one decision: why it was made, which options were considered, and what follows from it. ADRs are not edited after they are accepted. A changed decision gets a new ADR that supersedes the old one.
How the system works *today* is described in [../design.md](../design.md).

Format: [Michael Nygard](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) (Status / Context / Decision / Consequences).
New ADR: copy [template.md](template.md) to the next number.

| # | Title | Status |
|---|-------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-python.md) | Python 3.12 as implementation language | Accepted |
| [0003](0003-kernel-interfaces-spidev-gpiod.md) | Kernel interfaces (spidev + libgpiod) for hardware access | Accepted |
| [0004](0004-manual-chip-select.md) | Manually driven chip select via `spi0-0cs` | Accepted |
| [0005](0005-partial-updates-tile-diff.md) | Partial updates via tile diff, periodic full refresh | Accepted |
| [0006](0006-concurrent-push-pull-single-worker.md) | Concurrent push and pull, single panel worker | Accepted |
| [0007](0007-config-yaml-and-env.md) | Configuration via YAML file and `EPD_*` environment variables | Accepted |
| [0008](0008-rotation-by-controller.md) | Rotation by the IT8951 controller, mirroring in software | Accepted |
| [0009](0009-health-excludes-addon.md) | Health reflects service and panel only | Accepted |
| [0010](0010-async-push-202.md) | Push answers `202` without waiting for the panel | Accepted |
| [0011](0011-reject-size-mismatch.md) | Reject images of the wrong size by default | Accepted |
| [0012](0012-arm64-ghcr.md) | arm64-only image built in GitHub Actions, published to GHCR | Accepted |
| [0013](0013-mit-license.md) | MIT license | Accepted |

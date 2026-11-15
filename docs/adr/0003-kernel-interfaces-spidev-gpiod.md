# 0003. Kernel interfaces (spidev + libgpiod) for hardware access

- Status: Accepted
- Date: 2026-11-15

## Context

The Waveshare reference uses the `bcm2835` library, which maps `/dev/mem` and needs root or a privileged container. The legacy sysfs GPIO interface is deprecated.

## Options

1. `bcm2835` / `/dev/mem`
2. `spidev` + sysfs GPIO
3. `spidev` + libgpiod v2 (character device `/dev/gpiochipN`)

## Decision

Option 3. Only `/dev/spidev0.0` and `/dev/gpiochip0` are mapped into the container. The container runs as a non-root user that gets access through the host's `spi`/`gpio` groups (`group_add`).

## Consequences

- No `--privileged` and no root inside the container.
- spidev limits a single transfer to `bufsiz` (4096 B by default), so bulk writes are chunked. See [0004](0004-manual-chip-select.md) for why that's safe.
- The python `gpiod` bindings must match libgpiod v2. Resolved: `gpiod` ≥ 2.2 from PyPI bundles the libgpiod C sources and is compiled in the image's build stage. The service also supports the v1 bindings (Debian's `python3-libgpiod` 1.6), which the smoke test used on the host.

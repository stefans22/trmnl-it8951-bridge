# 0002. Python 3.12 as implementation language

- Status: Accepted
- Date: 2026-11-15

## Context

The service decodes PNGs, diffs ~1 megapixel frames, serves HTTP and drives SPI on a Raspberry Pi 3. Updates happen every few minutes. The work is bound by SPI throughput and the e-paper waveform (hundreds of ms), not by CPU. It has to be testable without hardware.

## Options

1. **Python**: Pillow/numpy for imaging, FastAPI, pydantic-settings, spidev/gpiod bindings.
2. **C**: wrap the Waveshare library. Fastest, but HTTP, config, testing and packaging all need a lot of extra work.
3. **Go / Rust**: good single binaries, but less ready-made support for SPI/GPIO and imaging, and more code to write.

## Decision

Python 3.12, using uv, ruff, mypy (strict) and pytest.

## Consequences

- Pixel work has to stay vectorized (numpy). Per-pixel Python loops are not allowed.
- If SPI transfer in Python turns out too slow, only the transfer loop moves to a C extension.
- The image is larger than a static binary (~150 MB), which is acceptable.

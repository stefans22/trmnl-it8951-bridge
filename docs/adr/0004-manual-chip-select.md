# 0004. Manually driven chip select via `spi0-0cs`

- Status: Accepted
- Date: 2026-11-15

## Context

An IT8951 transaction is: CS low → preamble → wait for HRDY → payload → CS high. A pixel upload is one transaction of up to ~495 KB. With spidev, the kernel toggles CS between transfers, and a single transfer is limited by `bufsiz`. The Waveshare code drives CS (GPIO 8) as a plain GPIO.

## Options

1. Kernel-controlled CS, with one huge transfer (raise `spidev.bufsiz` on the kernel cmdline; still can't wait for HRDY mid-transaction).
2. Kernel-controlled CS with `cs_change` tricks in multi-transfer ioctls. Fragile, and no HRDY polling in between.
3. Free GPIO 8 from the SPI controller (`dtoverlay=spi0-0cs`) and drive CS via libgpiod.

## Decision

Option 3. This is documented as a host requirement in the README.

## Consequences

- Matches the reference implementation, and allows arbitrary chunking and HRDY polling inside a transaction.
- Verified by the hardware smoke test on 2026-11-15 (Pi 3 Model B, kernel 6.12.47+rpt-rpi-v8, aarch64, libgpiod 1.6.3): `spi0-0cs` keeps `/dev/spidev0.0`. Device info, VCOM read-back, full and area uploads all work. Full-frame upload: 495 KB in 0.34 s at 12 MHz (11.6 Mbit/s, close to the wire speed, so Python isn't the bottleneck).
- `dtparam=spi=on` has to be removed. Otherwise the SPI driver claims GPIO 8 and opening the line fails with `EBUSY`.

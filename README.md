# ha-trmnl-ws-display

Show Home Assistant dashboards rendered by the
[TRMNL HA add-on](https://github.com/usetrmnl/trmnl-home-assistant) on a
Waveshare IT8951 e-paper panel (reference: 9.7", 1200×825) connected to a Raspberry Pi 3.

- **Push and pull:** the add-on can POST images to this service, and the service can fetch images from the add-on. Both can run at the same time.
- **Partial updates:** only changed rectangles are refreshed, with a full refresh every *N* updates against ghosting.
- **Status page, health endpoint and current image**, configured via a YAML file and/or environment variables.

> Status: requirements and design phase. See [docs/requirements.md](docs/requirements.md),
> [docs/design.md](docs/design.md), [docs/configuration.md](docs/configuration.md) and the
> [architecture decision records](docs/adr/README.md).

```mermaid
flowchart LR
    ADDON["TRMNL HA add-on"] -- "push: POST /api/image" --> SVC["ha-trmnl-ws-display<br/>(container on Pi 3)"]
    SVC -- "pull: GET fetch URL" --> ADDON
    SVC -- "SPI + GPIO" --> PANEL["IT8951 HAT + e-paper"]
```

## Host requirements

Everything below is assumed to be in place. The container does not check or change host settings.

### Hardware

- Raspberry Pi 3 (B/B+) running **Raspberry Pi OS Lite 64-bit (bookworm)** or another arm64 distribution with the Raspberry Pi kernel.
- Waveshare IT8951 e-Paper HAT set to **SPI** mode (check the interface switch/resistor on the HAT) and attached to the 40-pin header.
- The panel's **VCOM value**, printed on its FPC cable (e.g. `-1.50`). You need it for the configuration.

Pins used (BCM numbering): SPI0 MOSI/MISO/SCLK (10/9/11), CS 8, RST 17, HRDY 24.

### 1. Enable SPI with manually driven chip select

The IT8951 protocol needs chip select held low across many SPI transfers, so the service drives CS (GPIO 8) itself. Free the pin from the SPI controller.

Edit `/boot/firmware/config.txt`:

```ini
# dtparam=spi=on        <- comment out if present
dtoverlay=spi0-0cs
```

Reboot, then verify:

```sh
ls -l /dev/spidev0.0 /dev/gpiochip0   # both must exist
gpioinfo gpiochip0 | grep -E 'line +(8|17|24):'   # all three "unused"
```

(`gpioinfo` comes from `sudo apt install gpiod`.)

To check the whole chain (SPI, GPIO, panel, packing, rotation) before running the container, use the [hardware smoke test](docs/hardware-smoke-test.md).

### 2. Device permissions

The container runs as a non-root user and gets access through the host's `spi` and `gpio` groups. Note their GIDs for the compose file:

```sh
getent group spi gpio
# spi:x:999:...   gpio:x:997:...   (values vary)
```

### 3. Docker

Install Docker Engine with the compose plugin (for example `curl -fsSL https://get.docker.com | sh`), and add your user to the `docker` group.

### 4. TRMNL HA add-on

- **Push:** in the add-on, create a schedule with webhook format *Raw Image*, webhook URL `http://<pi>:8080/api/image`, viewport `1200x825` (`825x1200` for portrait), palette `gray-16`, dithering on. If you set a token in the add-on, set the same one as `push.token` here.
- **Pull:** use the add-on's Fetch URL with the same viewport and palette as `pull.url`.

## Quick start

```yaml
# compose.yaml
services:
  display:
    image: ghcr.io/<owner>/ha-trmnl-ws-display:latest
    restart: unless-stopped
    stop_grace_period: 30s          # allow the clear-to-white on shutdown
    ports: ["8080:8080"]
    devices:
      - /dev/spidev0.0
      - /dev/gpiochip0
    group_add: ["999", "997"]       # spi and gpio GIDs from step 2
    volumes:
      - ./config:/config:ro
      - display-data:/data
    environment:
      EPD_PANEL__VCOM: "-1.50"
volumes:
  display-data:
```

Then open `http://<pi>:8080/` for the status page. See [docs/configuration.md](docs/configuration.md) for all settings.

## Development

The commands to lint, test and build locally will be documented here once the implementation exists (planned: `uv`, `ruff`, `mypy`, `pytest`).

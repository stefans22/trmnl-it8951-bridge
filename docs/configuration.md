# Configuration

Must be kept in sync with `src/trmnl_it8951_bridge/config.py` (a test will check that every model field is documented here).

## Sources and precedence

1. Built-in defaults (lowest)
2. YAML file at `$EPD_CONFIG_FILE` (default `/config/config.yaml`; may be missing)
3. Environment variables (highest)

Environment variable names: `EPD_` + section + `__` + key, upper case.
Example: `display.full_refresh_every` → `EPD_DISPLAY__FULL_REFRESH_EVERY=10`.
Maps and lists are written as JSON, e.g. `EPD_PULL__HEADERS='{"Authorization":"Bearer abc"}'`.

Startup fails with a descriptive error if a value is invalid or a required value is missing.

## Example

```yaml
panel:
  vcom: -1.50            # REQUIRED – printed on the panel's FPC cable

display:
  rotation: 0            # 0 = landscape 1200x825, 90/270 = portrait 825x1200
  full_refresh_every: 10

push:
  token: change-me       # optional; enables Bearer auth for POST endpoints

pull:
  enabled: true
  url: http://homeassistant.local:10000/lovelace/0?viewport=1200x825&palette=gray-16
  interval_s: 600
```

## Reference

### `panel` – hardware

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `driver` | `it8951` \| `fake` | `it8951` | `fake` simulates the panel in memory (development, no hardware). |
| `vcom` | float (V, < 0) | **required** | VCOM from the FPC cable, e.g. `-1.50`. |
| `spi_device` | path | `/dev/spidev0.0` | SPI device node. |
| `spi_speed_hz` | int | `12000000` | SPI clock. The Pi 3 handles up to ~15.6 MHz. |
| `gpio_chip` | path | `/dev/gpiochip0` | GPIO chip that carries the header pins. |
| `pin_rst` | int (BCM) | `17` | Reset line. |
| `pin_hrdy` | int (BCM) | `24` | Host-ready/busy line. |
| `pin_cs` | int (BCM) | `8` | Chip select (driven manually). |
| `ready_timeout_s` | float | `10` | Max wait for HRDY / display engine idle. |
| `low_power` | `sleep` \| `standby` \| `none` | `sleep` | State between updates. |
| `enhance_driving` | bool | `false` | Write reg `0x0038=0x0602`; only if the image looks blurry. |
| `expected_width` / `expected_height` | int \| null | `null` | If set, init fails when the controller reports a different size. |

### `display` – rendering and refresh policy

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `rotation` | `0` \| `90` \| `180` \| `270` | `0` | Clockwise rotation of the logical image onto the panel. |
| `rotation_via_controller` | bool | `true` | Let the IT8951 rotate during upload. `false` rotates in software (fallback, see ADR 0008). |
| `mirror_x` / `mirror_y` | bool | `false` | Mirror the image (always in software; the controller can't mirror). |
| `size_mismatch` | `reject` \| `fit` \| `stretch` | `reject` | What to do with images that don't match the logical resolution. |
| `dither` | bool | `false` | Floyd-Steinberg locally. Use this instead of the add-on's dithering, whose pattern changes on every render and turns every update into a full refresh. |
| `full_refresh_every` | int ≥ 0 | `10` | Full refresh after N partial updates; `0` = never. |
| `full_refresh_area_ratio` | float 0–1 | `0.5` | Full refresh if the changed area exceeds this fraction. |
| `full_refresh_waveform` | `gc16` \| `init_then_gc16` | `gc16` | `init_then_gc16` clears ghosting more thoroughly (extra flash). |
| `tile_size` | int (multiple of 4) | `32` | Diff granularity in pixels. |
| `max_rects` | int ≥ 1 | `8` | Maximum rectangles per partial update. |
| `clear_on_startup` | bool | `true` | Clear to white during init. |
| `restore_last_frame` | bool | `true` | Show the persisted frame after startup. |
| `clear_on_shutdown` | bool | `true` | Clear to white on SIGTERM (protects the panel). |

### `push` – inbound images

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable `POST /api/image`. |
| `token` | secret \| null | `null` | If set, all POST endpoints need `Authorization: Bearer <token>`. |
| `max_body_bytes` | int | `10485760` | Upload limit (10 MB). |

### `pull` – outbound fetching

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable the scheduler. Requires `url`. |
| `url` | URL \| null | `null` | Add-on Fetch URL (set `viewport` to the logical resolution, `palette=gray-16`). |
| `interval_s` | int ≥ 10 | `300` | Fetch interval. |
| `timeout_s` | float | `60` | Request timeout (rendering can take a while). |
| `headers` | map (secret) | `{}` | Extra request headers. |
| `backoff_max_s` | int | `900` | Upper bound for retry backoff. |
| `on_start` | bool | `true` | Fetch right after startup. |

### `server`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | str | `0.0.0.0` | Bind address. |
| `port` | int | `8080` | HTTP port. |

### `storage`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `data_dir` | path | `/data` | Where `state.json` and `current.png` live. |
| `persist` | bool | `true` | Persist state across restarts. |
| `keep_images` | int ≥ 0 | `0` | Debugging: keep the last N received images (push and pull, as received, including rejected ones) in `<data_dir>/images/`. `0` = off. |

### `log`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `level` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `INFO` | Log level. |
| `format` | `text` \| `json` | `text` | Log line format. |

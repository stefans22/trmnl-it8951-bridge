# 0008. Rotation by the IT8951 controller, mirroring in software

- Status: Accepted
- Date: 2026-11-15

## Context

The panel can be mounted in landscape or portrait. The IT8951 `LD_IMG` / `LD_IMG_AREA` commands take a rotate argument (0/90/180/270) and rotate pixels while writing them into the controller's image buffer. The controller can't mirror. The Waveshare examples always use `ROTATE_0` and rotate or mirror in software, so the hardware path isn't exercised by the reference code.

## Options

1. Software: rotate and mirror with numpy, and diff and upload in physical coordinates.
2. Controller: keep frames in logical orientation, diff in logical coordinates, upload with the rotate flag, and translate rects to physical coordinates only for `DPY_AREA`. Mirroring stays in software.

## Decision

Option 2 by default. Option 1 stays available as a fallback via `display.rotation_via_controller: false`.

## Consequences

- The pipeline, diff and `current.png` all work in logical (image) orientation. No inverse transform is needed for the status page.
- The driver needs a small, unit-tested logical → physical rect mapping for `DPY_AREA`.
- Little CPU is saved (a numpy rotation takes milliseconds). The main benefit is simpler logical coordinates.
- Risks the smoke test has to check: the exact semantics of area coordinates with rotation ≠ 0, and 4bpp row packing when the logical width isn't a multiple of 4 (portrait on 9.7": 825 px). If either fails, the fallback becomes the default and this ADR is superseded.
- Software rotation is still implemented (fallback, `FakePanel`, tests), so both paths are maintained.

## Smoke test findings (2026-11-15, M841, FW SWv_0.2.1T)

- Rotation works without mirroring in all four codes. 825-px-wide portrait rows padded to whole words (`pad`) show no shearing.
- The rotate codes turn the image **counter-clockwise**: code 1 put the "up" bar on the left edge, code 3 on the right. So `LD_IMG_AREA` takes logical coordinates, and for a clockwise rotation *r* (config semantics) the code is `(360 − r) / 90 mod 4`.
- Partial updates at 0° and 180° landed correctly. At 90° and 270°, `DPY_AREA` refreshed the wrong area because the test used the clockwise assumption, while the pixels were loaded at the right place (they appeared after a full refresh). This matches the counter-clockwise finding exactly.
- Re-run with the corrected mapping (90° → code 3, 270° → code 1): the bar is on the right edge at 90° and on the left at 270°. The image is undistorted and the partial box lands fully in place in both. The box result confirms the mapping independently of how the edges were named.
- VCOM read back as −2.60 V before being set on every run, so the controller doesn't keep it across resets. The driver sets it on every init, as designed.

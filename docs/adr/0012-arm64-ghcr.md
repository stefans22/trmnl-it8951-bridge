# 0012. arm64-only image built in GitHub Actions, published to GHCR

- Status: Accepted (image tags superseded by [0014](0014-image-tags.md))
- Date: 2026-11-15

## Context

The target is a Raspberry Pi 3 with a 64-bit OS. The image should come from CI and live in the repository's own registry.

## Decision

- Platform `linux/arm64` only. Built with buildx, on native `ubuntu-24.04-arm` runners where available, and QEMU otherwise.
- Pushed to `ghcr.io/stefans22/trmnl-it8951-bridge` with `GITHUB_TOKEN`. Tags: `edge` and `sha-…` from the default branch; `X.Y.Z`, `X.Y` and `latest` from `vX.Y.Z` tags. PRs build but don't push.
- Lint (ruff, mypy, hadolint) and tests (pytest) gate the build.

## Consequences

- 32-bit Pi OS isn't supported. Adding `linux/arm/v7` later would be a single platform entry, as long as the dependencies have wheels.

# 0014. CI triggers and image tags: `latest` from main, PRs build only

- Status: Accepted (supersedes the tagging part of [0012](0012-arm64-ghcr.md))
- Date: 2026-11-15

## Context

ADR 0012 tagged images `edge`/`sha-…` from the default branch and semver tags from `vX.Y.Z` git tags. The intended long-term workflow is semantic-release:

- Commits to `main` or a maintenance branch may create a release (Conventional Commits decide), and the release creates a git tag.
- Tag pipelines build the release image. `main` additionally always builds `latest`.
- Other branches don't start pipelines on their own; only pull requests do.

Releases aren't set up yet, so the CI is shaped for that workflow without the release part.

## Decision

- CI runs on pushes to `main` and on pull requests (plus manual runs). Pushes to other branches don't trigger it.
- Pushes to `main` publish `ghcr.io/stefans22/trmnl-it8951-bridge:latest`. `latest` means "current main", not "latest release".
- Pull requests run lint, tests and the image build, but don't publish.
- No `edge`, `sha-…` or semver tags for now.

## Consequences

- There is exactly one published image tag until releases exist. Rolling back means rebuilding an older commit.
- To add semantic-release later:
  - **Tag pipelines:** tags pushed with the built-in `GITHUB_TOKEN` don't trigger workflows. Either push them with a GitHub App or personal token, or build the release image in the release job itself.
  - **Commit messages:** they have to follow Conventional Commits. With squash merges, check PR titles.
  - **Maintenance branches** (semantic-release default: `1.x`, `1.2.x`) have to be added to the push trigger. Their releases must not move `latest`.
  - **Version:** semantic-release has to bump the version in `pyproject.toml`; `python-semantic-release` does this natively.

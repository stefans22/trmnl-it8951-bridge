# 0007. Configuration via YAML file and `EPD_*` environment variables

- Status: Accepted
- Date: 2026-11-15

## Context

There are many settings (VCOM, pins, refresh cadence, orientation, sources). Container users expect environment variables. Larger setups are easier to manage as a file.

## Decision

- A pydantic-settings model with nested sections. Precedence: defaults < YAML file (`$EPD_CONFIG_FILE`, default `/config/config.yaml`) < environment.
- Env names: `EPD_<SECTION>__<KEY>`. Maps and lists are JSON.
- Validation at startup, failing fast. `panel.vcom` is required and has no default.
- Secrets are `SecretStr` and redacted in logs and status.

## Consequences

- One model is the source of truth. A test makes sure `docs/configuration.md` documents every field.
- No hot reload. Changes need a container restart.

# 0009. Health reflects service and panel only

- Status: Accepted
- Date: 2026-11-15

## Context

`/health` drives the container healthcheck, and orchestrators may restart unhealthy containers. The add-on can be down for reasons this service can't fix.

## Decision

`/health` returns `503` only when the service itself or the panel driver is broken (worker dead, controller unresponsive after a reset). Pull failures and add-on availability are reported in `/api/status` and on the status page.

## Consequences

- An add-on outage doesn't cause restart loops, and the last image stays on the panel.
- Monitoring of the add-on has to look at `/api/status`, not `/health`.

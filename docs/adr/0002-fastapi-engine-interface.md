# ADR 0002: FastAPI engine interface

## Context

The future Spring control plane needs a stable way to request research operations without owning
their internal pipelines.

## Decision

Use versioned REST endpoints implemented by FastAPI. Routers call application services and contain
no research logic. MCP is not used for service-to-service RPC; an AI-facing MCP adapter may be
added separately in the future.

## Consequences

OpenAPI becomes the external contract and the application layer remains reusable from CLI,
notebooks, tests, and future adapters.

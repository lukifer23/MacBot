# Agent harness decision — 2026-08-30

MacBot now owns a bounded durable Task kernel. No external harness is installed
or connected. Conversation, audio, history, credentials, authorization, tools,
effects, persistence, cancellation, and recovery remain MacBot responsibilities.

## Current decision

- Do not embed Hermes. Its memory, tools, skills, scheduling, gateways, profiles,
  and delegation duplicate product architecture and create another authority
  boundary.
- Do not depend on Pi now. Its streamed loop, cancellation hooks, and context
  transforms are useful reference mechanics, but they do not solve durable
  authority, idempotency, or uncertain-effect reconciliation.
- Do not add multi-agent delegation, shell, arbitrary filesystem writes, remote
  MCP, self-modifying skills, scheduling, messaging, purchasing, account changes,
  gateways, or model routing in this program.

The internal kernel must first pass the stable research corpus, recovery,
authorization, provenance, cancellation, latency, and device gates. Only if it
then misses the task-completion target may Pi enter a measured planner bakeoff.

## Adapter boundary for any future bakeoff

A harness adapter may receive bounded observations and registered capability
schemas and propose the next step. It may never execute a capability, forge a
receipt, own history or audio, read credentials, approve a scope change, or write
the task ledger. Material target, capability, data-scope, deadline, or side-effect
changes return the Task to native authorization.

Adoption requires measured improvement in the fixed evaluation corpus without
violating the 12-step, two-replan, ten-minute, one-active-step, context, memory,
latency, cancellation, or recovery budgets.

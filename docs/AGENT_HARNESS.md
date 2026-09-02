# Agent harness decision — 2026-08-30

MacBot now owns a bounded durable Task kernel. No external harness is installed
or connected. Conversation, audio, history, credentials, authorization, tools,
effects, persistence, cancellation, and recovery remain MacBot responsibilities.

## Current decision

- Do not embed Hermes. Its memory, tools, skills, scheduling, gateways, profiles,
  and delegation duplicate product architecture and create another authority
  boundary.
- Do not depend on or add an adapter for Pi. Its streamed loop, cancellation
  hooks, and context transforms are useful implementation references, but Pi is
  a terminal coding harness and does not solve MacBot's durable authority,
  native audio, idempotency, or uncertain-effect reconciliation.
- Do not add multi-agent delegation, shell, arbitrary filesystem writes, remote
  MCP, self-modifying skills, scheduling, messaging, purchasing, account changes,
  gateways, or model routing in this program.

The native Agent Kernel is the only release loop. It must pass the stable
research corpus, recovery, authorization, provenance, cancellation, latency,
and device gates without a fallback planner or external session store.

## Boundary for any future architecture review

Any future proposal must be evaluated as a replacement architecture, not an
adapter layered beside MacBot. No harness may execute a capability, forge a
receipt, own history or audio, read credentials, approve a scope change, or
write the Task ledger. Material target, capability, data-scope, deadline, or
side-effect changes return the Task to native authorization.

Adoption requires measured improvement in the fixed evaluation corpus without
violating the 12-step, two-replan, ten-minute, one-active-step, context, memory,
latency, cancellation, or recovery budgets.

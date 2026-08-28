# Agent harness decision — 2026-08-28

No external agent harness is installed or connected by this modernization. The native audio loop, conversation state, cancellation, and approval registry remain owned by MacBot.

## Recommendation

Evaluate Pi's agent core first **if** MacBot needs durable, multi-step tasks beyond the existing local assistant tools. Keep ordinary voice conversation on the direct model path. A task engine should be invoked deliberately and report progress through MacBot's event protocol; it should not add another model call to every utterance.

| Candidate | Useful capability | Tradeoff for this release |
| --- | --- | --- |
| [Pi agent core](https://github.com/earendil-works/pi/tree/main/packages/agent) | Embeddable agent state, streamed events, context transforms, and before/after tool hooks | Adds a JavaScript runtime and an integration boundary to the Python assistant. The default tool execution and permissions must not become an alternate action path. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Persistent memory, reusable skills, conversation search, and broader task workflows | Overlaps with MacBot's context, persistence, and tool ownership. Requires an isolated profile and explicit local model/tool configuration; do not connect an existing personal Hermes installation implicitly. |
| [OpenClaw](https://docs.openclaw.ai/concepts/architecture) | A gateway for messaging channels, sessions, nodes, and remote connectivity | Useful if cross-channel/remote operation becomes a requirement. It introduces infrastructure and exposure that the current loopback-only native release does not need. |

This is an architectural recommendation, not a comparative performance/security certification. No claim is made that a harness increases model accuracy or lowers voice latency. The current model screening does not evaluate long autonomous tasks.

## Integration requirements before adoption

1. Keep MacBot's native capture/playback path and authoritative turn cancellation.
2. Give the harness a fixed tool allowlist. No general shell, arbitrary filesystem, remote MCP, or network capability by default.
3. Route **every** side effect through the exact-arguments/session/turn/single-use approval boundary. Neither agent text nor a retrieved document can approve another tool.
4. Use an isolated local profile and bounded work queues. A cancelled task must stop producing speech, invalidate pending approvals, and expose any already-started action honestly.
5. Measure task completion, additional RSS, latency and failure recovery on this Mac. Test the whole tool loop; do not infer usefulness from an upstream demo.
6. Add a visible task view with progress, cancel, result provenance, and an explicit distinction between completed work and proposals.

[Liquid's August 4 model release](https://www.liquid.ai/blog/lfm2-5-2-6b) describes training LFM2.5-2.6B with agent harnesses and exposes local serving options. That motivates evaluating the model here; it does not require adopting a harness or establish MacBot-specific performance.

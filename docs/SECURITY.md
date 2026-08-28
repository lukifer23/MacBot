# Security boundary

MacBot runs for one local macOS user. Loopback is a transport restriction, not authentication. It does not protect secrets from another process already running as that user or from an administrator.

- Every control/data request needs a valid browser session or the credential for its target service. Service credentials are rejected when an Origin header is present. Query-string credentials are never accepted.
- Browser login exchanges a one-use, short-lived token for an HttpOnly, SameSite=Strict cookie and separate CSRF token. Cookie authentication alone cannot mutate state. HTTP on loopback is intentional; do not expose it through a tunnel or reverse proxy.
- Host, Origin and cross-site browser requests are checked. Socket.IO connections require the session plus CSRF token and a valid origin; event delivery checks session validity again. Wildcard CORS is not enabled.
- The model can propose a tool call, but it cannot approve one. Read-only system metrics and RAG search are automatic. Opening applications, websites, external searches, weather searches and screenshots need a dashboard confirmation bound to immutable arguments, session, turn and expiry. Denial, expiry, replay and cancellation prevent execution.
- Speech, generated text and retrieved documents cannot approve actions. Voice-only confirmation is deliberately unsupported: an acoustic echo or a document containing approval words must not authorize a desktop operation.
- Rendered model/document text uses textContent, not HTML. Uploads have request and file limits, PDF page limits and DOCX expansion limits. Audio conversion uses private temporary files, a protocol whitelist and a deadline.
- Runtime model paths come from a registered pinned catalog. Provisioning is explicit and hashes are checked. Inference sets offline mode. Arbitrary Python model code is not enabled.
- Routine logs must not contain conversation text or credentials. Benchmark output contains only the public benchmark prompts and model outputs, under the user's private report directory.

## Dependency risk

Piper is GPL-3.0; model and voice licenses are separate. The model catalog records upstream terms and voice model cards. Do not assume a repository's top-level license covers a voice's training data.

Chroma 1.5.9 is embedded only. Known upstream advisories affecting Chroma's remotely exposed server/collection configuration APIs require a documented reachability review; they are not silently considered patched. MacBot never runs Chroma's HTTP server and passes `embedding_function=None` plus explicit locally computed embeddings. A final dependency audit and reachability report remain release gates.

## Reporting and verification

Do not include service keys, session cookies, private documents or recordings in bug reports. Report affected versions, route, expected/actual behavior and redacted logs. Authentication, origin, approval and parser regressions must fail the supported test suite. Successful startup or a localhost address is not evidence of authorization correctness.

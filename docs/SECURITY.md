# Security boundary

MacBot runs for one local macOS user. Loopback is a transport restriction, not authentication. It does not protect secrets from another process already running as that user or from an administrator.

- Every control/data request needs a valid browser session or the credential for its target service. Service credentials are rejected when an Origin header is present. Query-string credentials are never accepted.
- Browser login exchanges a one-use, short-lived token for an HttpOnly, SameSite=Strict cookie and separate CSRF token. Cookie authentication alone cannot mutate state. HTTP on loopback is intentional; do not expose it through a tunnel or reverse proxy.
- Host, Origin and cross-site browser requests are checked. Socket.IO connections require the session plus CSRF token and a valid origin; event delivery checks session validity again. Wildcard CORS is not enabled.
- Tool availability is scoped to the current user text, never model output, history or retrieved documents. Direct app and URL requests bind the target as well as the tool; disabled tools remain unavailable. Repeated calls of the same tool are denied within the turn. Ambiguous phrasing should produce clarification, not inferred desktop authority.
- Supported bounded actions run automatically only when independently bound to
  an exact span in the current request. App/URL opening and screenshots require
  an explicit imperative request. A screenshot is the only planner action that
  creates a file: it uses a generated filename in the configured directory and
  cannot overwrite an existing named file. No arbitrary file creation/deletion
  or shell tool is exposed.
- Read-only local clock, system metrics and document search run automatically when requested. Action results return as tool messages; successful execution is never inferred from the model's promise. "Confirm action" spoken aloud does not approve a pending dashboard action in confirmation mode.
- Automatic execution trusts recognized speech as the user's request. It cannot distinguish the operator from another nearby speaker or a recording. Acoustic echo rejection still needs device acceptance; use confirmation mode if untrusted audio can reach the microphone. Request routing is deliberately conservative and is not a general semantic authorization classifier.
- Rendered model/document text uses textContent, not HTML. Uploads have request and file limits, PDF page limits and DOCX expansion limits. Audio conversion uses private temporary files, a protocol whitelist and a deadline.
- Runtime model paths come from a registered pinned catalog. Provisioning is explicit and hashes are checked. Inference sets offline mode. Arbitrary Python model code is not enabled.
- Routine logs must not contain conversation text or credentials. Benchmark output contains only the public benchmark prompts and model outputs, under the user's private report directory.

## Dependency risk

Piper is GPL-3.0; model and voice licenses are separate. The model catalog records upstream terms and voice model cards. Do not assume a repository's top-level license covers a voice's training data.

The Chroma runtime dependency was removed. Document retrieval now uses
SQLite-authoritative source records and a versioned local exact vector index,
which removes Chroma's server/configuration attack surface and its large
transitive dependency graph. A final resolved dependency audit and reachability
report remain release gates.

## Reporting and verification

Do not include service keys, session cookies, private documents or recordings in bug reports. Report affected versions, route, expected/actual behavior and redacted logs. Authentication, origin, approval and parser regressions must fail the supported test suite. Successful startup or a localhost address is not evidence of authorization correctness.

Kokoro weights are Apache-2.0; the ONNX wrapper and bundled espeak-ng phonemizer have their own licenses. The registered model and voice pack record upstream release/revision, filenames, sizes and SHA-256 values. Runtime voice IDs resolve only through the registry. Review all dependency licenses before redistributing an app bundle.

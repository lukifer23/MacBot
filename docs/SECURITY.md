# Security boundary

MacBot runs for one local macOS user. Loopback is a transport restriction, not authentication. It does not protect secrets from another process already running as that user or from an administrator.

- Every loopback HTTP control/data request needs a valid browser session or the
  credential for its target service. Service credentials are rejected when an
  Origin header is present. Query-string credentials are never accepted.
- Native command, event, and audio connections authenticate over owner-only
  Unix sockets with one ephemeral per-launch token. The token file is consumed
  at startup; the in-memory token authenticates each connection for that launch.
- Browser login exchanges a one-use, short-lived token for an HttpOnly, SameSite=Strict cookie and separate CSRF token. Cookie authentication alone cannot mutate state. HTTP on loopback is intentional; do not expose it through a tunnel or reverse proxy.
- Host, Origin and cross-site browser requests are checked. The developer diagnostics page is authenticated, read-only, and does not receive conversation events. Wildcard CORS is not enabled.
- Conversation can select only deterministic read-only enrichment from the current user text. Model output, history, and retrieved documents cannot grant authority.
- Task mode persists the proposed plan and exact capability manifest before authorization. Every step uses a single-use receipt bound to its task, normalized arguments, expiry, and safety class. Material scope changes require a new proposal and authorization.
- Release Tasks expose only `rag_search`, `web_search`, and `web_fetch`.
  `web_fetch` rejects credentials, fragments, non-HTTP schemes, nonstandard
  ports, hostnames that resolve to private/loopback/link-local/reserved
  destinations at validation time, unsafe redirects, unsupported content, and
  oversized bodies. Evidence records retain canonical identity, retrieval
  time, excerpt, provenance, and body hash. The current resolver and HTTP
  connection perform separate DNS resolutions; eliminating that rebinding
  window by connecting to a validated address is an open release-security gate.
- App/URL opening, screenshots, shell, arbitrary file creation/deletion,
  scheduling, messaging, MCP, delegation, and self-modifying skills are not
  exposed.
- Automatic execution trusts recognized speech as the user's request. It cannot distinguish the operator from another nearby speaker or a recording. Acoustic echo rejection still needs device acceptance; stop hands-free mode when untrusted audio can reach the microphone. Request routing is deliberately conservative and is not a general semantic authorization classifier.
- The encrypted-history key is retained in macOS Keychain. The native app passes the 32-byte key through an inherited private pipe to the CLI, which re-pipes it to the supervisor; the supervisor creates a fresh pipe for each assistant start or restart. The service never reads the key from arguments, environment values, configuration, URLs, logs, or files. Keychain access blocked during macOS dark wake delays service startup until the Mac wakes.
- Rendered model/document text uses textContent, not HTML. Uploads have request
  and file limits, PDF page limits, and DOCX expansion limits. Swift owns the
  released audio transport; no Python helper accepts audio commands.
- Runtime model paths come from a registered pinned catalog. Provisioning is explicit and hashes are checked. Inference sets offline mode. Arbitrary Python model code is not enabled.
- Routine logs must not contain conversation text or credentials. Benchmark output contains only the public benchmark prompts and model outputs, under the user's private report directory.

## Dependency risk

Model and voice licenses are separate. The typed production manifest identifies
the single release artifact for each role, while the broader catalog records
lab candidates and upstream terms. Do not assume a repository's top-level
license covers a voice's training data.

The Chroma runtime dependency was removed. Document retrieval now uses
SQLite-authoritative source records and a versioned local exact vector index,
which removes Chroma's server/configuration attack surface and its large
transitive dependency graph. A final resolved dependency audit and reachability
report remain release gates.

## Reporting and verification

Do not include service keys, session cookies, private documents or recordings in bug reports. Report affected versions, route, expected/actual behavior and redacted logs. Authentication, origin, approval and parser regressions must fail the supported test suite. Successful startup or a localhost address is not evidence of authorization correctness.

The registered model and voice artifacts record upstream release/revision,
filenames, sizes, and SHA-256 values. Runtime voice IDs resolve only through the
registry. Review every dependency and model license before redistributing an app
bundle.

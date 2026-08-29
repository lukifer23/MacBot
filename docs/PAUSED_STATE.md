# Active native rebuild checkpoint — 2026-08-29

The preserved baseline is `67781d56e2e651ded0fafc2593ef6886384cf6df` and
the first native foundation checkpoint is
`a6745513a408fa82ab3d8ddd3e6656296f74c83d`. Work continues directly on
`main`; no feature branch is used.

The current slice adds semantic history compaction, app-owned duplex audio,
playback completion/interrupt acknowledgements, native settings and diagnostics,
browser fallback isolation, exact llama.cpp b10509 provenance, clean versioned
wheel installation, and safe recovery of missing derived retrieval indexes.

Current local evidence: 124 non-device tests and 4 native Swift tests pass;
Ruff, formatting, mypy, JavaScript syntax, Swift release build, wheel inspection, dependency audit,
installed-runtime readiness, and owned-process shutdown pass. The selected
Qwen3.5-2B official-source Q4 passes the 95% screening threshold and the real
runtime regression suite. Qwen3-TTS 0.6B and 1.7B are integrated, benchmarked,
and selectable; the 1.7B Aiden voice is configured only for owner audition.
Real interim transcription is implemented. Services are stopped after
verification.

The current native recovery slice also keeps the encrypted-history key on
inherited private pipes across supervised restarts, waits through Keychain dark
wake, reconnects after service loss, reopens the main window from the menu bar,
and orders transcript, response and action cards from the shared event journal.
The Mac was in dark wake during the attempted visual/accessibility review, so
that gate remains unrun.

The clean installed build and offline installed-runtime gates pass. The next
controlled step is native visual/accessibility and owner device testing. Device
listening, acoustic latency, echo/overlap behavior, the
30-minute soak, native visual/accessibility review, and user voice acceptance
remain release blockers.

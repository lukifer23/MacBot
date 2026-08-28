# Dependency audit — 2026-08-28

`pip-audit` was run against the complete exported, pinned runtime graph (including MLX). It reported four advisories, all in Chroma 1.5.9, with no fixed version listed. The raw result is retained in the private verification report directory. This is **not a clean vulnerability scan**.

| Advisory | Reported prerequisite | MacBot exposure assessment |
| --- | --- | --- |
| CVE-2026-45829 / PYSEC-2026-311 | Chroma collection-create HTTP endpoint accepts a remote model with trust_remote_code | Chroma HTTP server is not started. Collection creation is internal, with embedding_function=None and explicitly supplied embeddings. No model/embedding configuration is accepted from an upload or API request. |
| CVE-2026-45833 | Chroma collection-update HTTP endpoint and UPDATE_COLLECTION permission | That endpoint is not exposed by MacBot. MacBot does not expose collection configuration updates. |
| CVE-2026-45830 | Chroma server cross-tenant authorization | MacBot has one OS-user-local embedded store, not a tenant-facing Chroma server. MacBot's own service authentication is applied independently. |
| CVE-2026-45831 | Chroma SimpleRBACAuthorizationProvider cross-tenant resource checks | This provider is not configured or used. There is no multi-tenant Chroma API. |

The assessment is scoped to this architecture, not a claim that the dependency is patched. `DocumentStore` supplies embeddings for every add/query and passes embedding_function=None on creation and retrieval, including legacy inspection. Inspection of the installed 1.5.9 client confirms that this path does not instantiate a persisted embedding function on get_collection. Legacy databases and native model weights are trusted local artifacts, not accepted uploads.

Do not enable Chroma's standalone server, add remote embedding configuration, or expose its collection APIs without a new security review. Re-audit before release and on dependency updates. The advisory findings and this mitigation require explicit consideration in release acceptance; they must not be hidden with blanket ignore flags.

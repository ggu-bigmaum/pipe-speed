# Portable match display cache implementation plan

Goal: reuse audited tile flags across Windows/Linux and PBF/MBTiles without recomputing match decisions. Preserve every match rule and counterpart visibility guard.

Approved approach: content identity (display rule, verified comparison package version, pair/layer/XYZ, decompressed tile SHA and counterpart SHA), independent of path/mtime. File signatures are bounded in-process memoization hints only. Existing known-rule rows can migrate on access only after matching current content identities and validating payload. Cache hits must not wait behind expensive rebuilds. HTTP responses use ETag with mandatory revalidation; errors remain no-store. No new geometry comparison or combined tiles.

## Steps
- [x] Add failing portability, legacy migration, stale peer/package/content, cache-hit concurrency and conditional HTTP tests.
- [x] Implement bounded decompression/hash-only reads, content-key validation and migration, bypass rebuild lock on hits.
- [x] Add conditional HTTP caching and verify frontend behavior with regression suite.
- [x] Validate existing real cache payloads/read-only originals, measure cold-service cache reuse and repeat reads. Report runtime SHA cost separately from drawing.
- [ ] Document deployment and results, commit code, apply locally and push; no remote service restart without user execution. No data deletion.

Risks: MBTiles uses the existing application's XYZ row convention, not assumed standard TMS. Missing peer and changed peer must invalidate suppression. Reject old KICT cache rules without counterpart proof. Migration cannot prove the entire deployed dataset matches local files; each first access must verify real tile content. Mutable SQLite cache is derived; authoritative source packages remain read-only. Browser page-render performance is separate from API response time.

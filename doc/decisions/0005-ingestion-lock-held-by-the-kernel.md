# ADR 0005: The ingestion lock is held by the kernel

- Status: accepted (owner approval on issue #78, decisions D1 to D9)
- Date: 2026-11-01
- Issue: #78

## Context

`ingestion.lock` proved a run by the existence of a file, so a killed run blocked all later runs. PIDs can be reused and runs have no maximum length outside the Admin page.

## Decision

Every writer of the ingestion stores takes the lock only through `src/ingest_lock.py`: an exclusive byte-range lock held open for the whole run. The kernel releases it when the process ends. File content is informational and is never used for a decision. An existing file with no holder is stale and is taken over without being deleted.

## Consequences

No age limit and no PID check. All holders must use the helper, because a holder without the kernel lock looks stale. Windows only; other platforms keep exclusive create with no recovery.

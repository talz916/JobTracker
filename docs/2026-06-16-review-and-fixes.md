# JobHunter — Code Review & Fixes Retrospective

**Date:** 2026-06-16
**Scope:** End-to-end review of JobHunter (FastAPI + SQLite + Gmail/Calendar + Claude Haiku classifier, ~1,800 LOC), followed by fixes, a test suite, and a consolidated merge to `main`.

---

## TL;DR

Reviewed the whole app, shipped **9 fixes** (a crash, data-integrity, security, performance) plus the project's **first test suite (0 → 82 tests)**. Two changes regressed in real-world use; both were caught, one feature was reverted by design, and everything was consolidated into a single tested PR (#12), now on `main`. **No data was lost** — every risky step ran against database copies with backups taken first.

---

## What shipped (merged to `main` via PR #12)

| # | Area | Fix |
|---|------|-----|
| 1 | **Crash** | "Reset Email Cache" → next sync died on a stale placeholder ID (foreign-key violation; `INSERT OR IGNORE` does not suppress FK errors). Moved the lookup into the DB layer and verify existence per call. |
| 2 | **Concurrency** | Double-clicking Sync could start two runs (TOCTOU between the "running?" check and the "start" write). Atomic test-and-set under one lock. |
| 3 | **Data** | Three timestamp formats (`CURRENT_TIMESTAMP`, naive `isoformat()`, tz-offset email dates) compared as strings → unreliable "ghosted" detection and event ordering. Unified to one naive-UTC format + idempotent startup migration. |
| 4 | **Security** | Server-side validation of `stage`/`source`/`job_url`; escaped every dashboard-rendered field; blocked `javascript:` links. |
| 5 | **Reliability** | DB path anchored to the project root (a CWD-relative default was silently creating empty second databases). |
| 6 | **UX** | OAuth callback no longer returns error pages on cancel/failure and no longer leaves the one-time auth code in the browser URL. |
| 7 | **Performance** | Parallel email sync, prompt caching wired into the classifier, SQLite WAL + busy timeout. |
| 8 | **Maintainability** | De-duplicated a 4×-copied "best stage" helper, fixed a calendar `source` mislabel (was skewing source stats), removed dead code. |
| 9 | **Testing** | 82-test regression suite + settings guardrails (e.g. `ghosted_days ≤ 0` would otherwise flag every application as ghosted). |

**Process:** built as 10 isolated branches → 10 PRs for review → consolidated into one bundle (#12) once cross-feature interactions were understood.

---

## Two regressions introduced — and caught

### 1. SSL crash in the parallel sync (`[SSL: WRONG_VERSION_NUMBER]`)
The parallel sync shared **one** Gmail client across 6 worker threads. `httplib2` (under `google-api-python-client`) is **not thread-safe**, so concurrent requests corrupted the shared TLS stream and sync died at 0/253 threads.

- **Why tests missed it:** they used a fake client with no real sockets — passed green while real Gmail broke. A mock-vs-reality gap for I/O concurrency.
- **Fix:** per-thread (thread-local) Gmail clients + a regression test that fails without it.

### 2. Duplicate entries after a normal sync
A feature added during this work — **classify in-thread replies** — made sync read follow-up emails the old code skipped. Good in principle, but the matcher *ignores archived/terminal entries*, so a rejection reply for a deleted or already-rejected application was routed to a **new** entry. Result: duplicates of exactly the records the user had curated (merged / deleted / rejected).

- **Recovery:** restored from a pre-incident backup (zero data loss).
- **Decision:** **removed the in-thread reply feature entirely.** In-thread rejections are rare; not worth the duplication risk against a manual-curation workflow. Reverted to "first message per thread, skip already-seen threads."

**What caught these:** integration-testing the branches *together* against a copy of the real DB surfaced both a `NameError` (a helper removed in one branch was still called in another) and the behavioral bugs above — none visible in any single branch.

---

## How data was de-risked

- Every migration/boot was tested against a **copy** of the production DB, never the live file.
- **Backups taken before every destructive step** (restore, branch switch, merge).
- Verified row counts identical before/after each migration; booted the app and diffed served data.

---

## Known limitations / proposed follow-ups

1. **Matcher ignores archived/terminal entries.** A brand-new thread from a deleted/rejected company can still create a duplicate (now rare, since replies aren't reprocessed). *Proposed:* matcher respects deletions and matches terminal-stage entries. (~half-day)
2. **Role matching is exact-string.** Fuzzy role text from the classifier can occasionally split one application into two. (lower priority)
3. **No real network/concurrency test.** The SSL bug's root cause is uncovered by mocks; worth a thin integration test against a sandboxed transport.
4. **Housekeeping:** Python 3.9 is EOL (dependency warnings); `main` has branch protection requiring admin override to merge.

---

## Takeaways

- Mock-based unit tests are necessary but **not sufficient for I/O and concurrency** — the SSL bug shipped green.
- A behavior change (reply reading) collided with an existing workflow (manual curation) because the matcher's handling of archived/terminal records wasn't traced first. **Cross-feature interaction review beats per-feature review.**
- Stacked/parallel branches read cleanly in isolation but hid integration bugs — the consolidated dry-run against real data is what made the merge safe.

---

## Behavior notes for future maintainers

- **Sync is first-message-only.** Once a thread is recorded it is never re-processed; a rejection/update sent as an in-thread reply will **not** advance the stage automatically — set it manually. This is intentional (see regression #2).
- **Per-thread Gmail clients are load-bearing.** Do not refactor the parallel sync to share one `service` across workers (see regression #1).
- **Timestamps are stored as naive UTC ISO-8601.** Write through the `utc_now_iso()` / `to_utc_iso()` helpers; do not insert raw `datetime.utcnow()` or DB `CURRENT_TIMESTAMP` strings.

# ArcumAI - Current-State Code Analysis and Action Plan

**Analyst review date:** 2026-06-10
**Subject:** `ArcumAI-dev-features` (snapshot dated 2026-05-18)
**Scope:** Python backend (`src/`, `ingest.py`, `watcher.py`, entry point), C# VSTO Outlook add-in, supporting scripts and docs.

---

## 0. How to read this report (important)

The archive already contains a substantial internal analysis at `doc/ARCUMAI_FULL_ANALYSIS.md` dated 2026-03-08, plus phase fix summaries (`PHASE1..4`, `CODE_REVIEW_FIXES_V1.1`, the VSTO loopback phase docs). I read all of them before writing this.

That earlier document is now partly **stale**: several items it lists as open have since been fixed in the code you sent (conversation persistence, the admin document-management page, rate-limiter cleanup, the WebSocket API-key plus per-IP throttle, path-traversal guards in `find_relative_path` and the admin re-ingest path). Conversely, a number of items it marked CRITICAL or HIGH are **still present unchanged** in this snapshot.

So this report does three things the old one cannot:

1. It reflects the **code as it actually is today**, not as it was in March.
2. It separates "still open from the old review" from "**not previously flagged**" so you can see what is genuinely new.
3. Every issue maps to exactly one file in `prompts/`, written as a self-contained brief you can hand to Claude Code without re-explaining context.

A note on method: this is a static read of the source. I did not run the stack (no Ollama, no ChromaDB, no Outlook host here), so runtime-only behaviour is reasoned about rather than observed. Where I am inferring rather than certain, I say so.

---

## 1. What ArcumAI is

A privacy-first assistant for Swiss legal and fiduciary offices. Two clients (a NiceGUI web chat and a C# VSTO Outlook add-in) talk to one Python backend. The backend runs a hybrid RAG pipeline (ChromaDB vectors plus a persisted BM25 index, fused with `QueryFusionRetriever`) over ingested documents, generates answers with a local Ollama model by default, and can optionally route to Gemini with Presidio-based PII masking. The Outlook integration ("virtual loopback") intercepts mail addressed to `assistant@arcumai.ch`, processes it server-side, and injects the reply back into the inbox.

The data layer is, as you have it documented elsewhere, ChromaDB plus BM25 plus `users.json` and per-user JSON conversation files. There is no PostgreSQL, consistent with your stated stack.

### Architecture, as built

```
            Web browser ───HTTP──┐                 ┌── Ollama (local LLM)
                                 ▼                 │
  Outlook VSTO add-in ──WS/JSON-RPC──►  Python backend (NiceGUI + FastAPI) ─┼── Gemini (cloud, optional, NER-masked)
   (ItemSend intercept)                  │  UserSession: RAG / Simple / Cloud / Agent
                                         │  OutlookBridgeManager: per-user priority queues,
                                         │     AI semaphore, offline pending-result store
                                         ▼
                          ChromaDB (vectors) + BM25 (keywords) + users.json + storage/conversations/*.json
                                         ▲
                          watcher.py ──► ingest.py (batch embed + index)
```

This is a competent design for a one-developer sovereign-AI product. The bridge in particular (priority queues that survive disconnect, an offline result store with TTL and a `.delivering` claim step to avoid double-delivery, deduplication keyed on `conversation_id`) is more carefully engineered than most code at this stage. The refactor into `src/ai`, `src/bridge`, `src/ui` is clean and the import-smoke test in `tests/` confirms the package wiring holds.

The honest summary: **the architecture is sound and the happy path works. The gaps are in production hardening, a handful of correctness bugs, and the absence of real (non-import) tests.**

---

## 2. Issues, ordered by criticality

Severity reflects likelihood times blast radius for *this* deployment context (a small regulated office, local-first, low user count, sensitive data). Each ID maps to a file in `prompts/`.

### CRITICAL

| ID | Title | File | Status vs old review |
|----|-------|------|----------------------|
| C1 | Predictable default `STORAGE_SECRET` fallback enables session forgery | `main_nice.py` | Still open (was SEC-2) |
| C2 | `/documents` serves the entire archive to any authenticated user, no per-user authorization | `main_nice.py` | Still open (was SEC-4) |
| C3 | No brute-force protection on the web `/login` | `main_nice.py`, `src/auth.py` | Still open (was SEC-9); note the WS endpoint *is* now throttled, login is not |

### HIGH

| ID | Title | File | Status |
|----|-------|------|--------|
| H1 | No transport encryption (HTTP/WS in the clear) and the server enforces nothing | `main_nice.py` | Still open (was SEC-3) |
| H2 | WebSocket identity is a guessable path segment; `WS_API_KEY` is opt-in and empty by default | `main_nice.py`, `src/config.py` | Partial: key support added but disabled by default (was SEC-6/SEC-11) |
| H3 | PII / email bodies written to logs at DEBUG; `.env.example` ships `LOG_LEVEL=INFO` but optimizer logs full bodies at INFO | `src/ai/prompt_optimizer.py`, `src/bridge/loopback_processor.py` | Worse than old SEC-10: confirmed INFO-level body logging, not just DEBUG |
| H4 | `requirements.txt` is UTF-16; `pip install -r` fails as-is | `requirements.txt` | Still open (was BUG-1) |
| H5 | `users.json` written non-atomically; concurrent writes can corrupt the user DB | `src/auth.py` | Still open (was BUG-6) |

### MEDIUM

| ID | Title | File | Status |
|----|-------|------|--------|
| M1 | `COMMON_WORDS` set has a missing comma: `"chf" "le"` silently becomes `"chfle"`, degrading the OCR language heuristic | `src/readers.py` | **New, not previously flagged** |
| M2 | Conversation history is injected twice (as engine memory *and* as prepended text), wasting context and risking incoherence | `src/ai/session.py` | **New, not previously flagged** |
| M3 | MD5 used for dedup; also double-hashed per file during ingestion (wasted I/O) | `src/utils.py`, `ingest.py` | Still open (SEC-8 + BUG-10) |
| M4 | BM25 index fully rebuilt from the whole corpus after every batch | `ingest.py` | Still open (was PERF-1) |
| M5 | A fresh `UserSession` (loads `users.json`, builds tools, instantiates engines) is created per loopback email | `src/bridge/loopback_processor.py` | Still open (was PERF-3) |
| M6 | Upload silently truncated to 10k chars with no warning to the user | `src/ui/footer.py` | Still open (was BUG-11) |
| M7 | Hardcoded Gemini model string in two places | `src/ai/engines.py`, `src/ai/prompt_optimizer.py` | Still open (was QA-7) |
| M8 | Agent `.run()` result stringified with `str(response_obj)`; for the workflow agent this may not yield clean text | `src/ai/session.py` | **New, not previously flagged** |
| M9 | Ingestion lock uses `open(..,'x')`, not atomic on SMB/NFS shares (your watched drop zone may be a network share) | `ingest.py` | Still open (was BUG-3) |

### LOW

| ID | Title | File | Status |
|----|-------|------|--------|
| L1 | Bare `except` / `except Exception: pass` swallow errors without logging in several spots | multiple | Still open (was QA-1) |
| L2 | Temp upload file `temp_ghost_upload.pdf` leaks on read failure; also a fixed filename collides under concurrency | `src/ui/footer.py` | Still open (was BUG-4), with an added concurrency angle |
| L3 | Mixed Italian/English identifiers and prompts reduce maintainability | throughout | Still open (was QA-6) |
| L4 | No `pyproject.toml`; deps unpinned by intent (direct vs transitive not separated) | repo root | Still open (was QA-8) |

---

## 3. The issues that matter most, explained

I am expanding only the ones where the *why* or the *fix* is non-obvious. The rest are adequately specified by their prompt files.

### C1 - Default session secret

`main_nice.py` ends with:

```python
storage_secret = os.getenv('STORAGE_SECRET', 'CHIAVE_SEGRETA_ARCUM_AI_V2_DEV_DEFAULT')
```

NiceGUI signs the session cookie with this secret. If the env var is ever unset in production (a forgotten `.env`, a misconfigured systemd unit), every install falls back to the same public string, and anyone who has seen this source can forge an authenticated session cookie. The fix is one line of philosophy: a security secret must never have a usable default. Fail to start instead. The prompt for C1 spells out the fail-fast pattern and how to keep local dev ergonomic.

### C2 - Archive served wholesale

```python
app.add_static_files('/documents', str(ARCHIVE_DIR))
```

Every authenticated user can fetch `/documents/<anything under the archive>`. For a general tool that is fine; for a fiduciary office where one client's matter must not be visible to another user, it is a confidentiality breach waiting to happen. The RAG "Sources" links in `footer.py` build exactly these URLs, so the path structure is also easy to enumerate. The real fix is an authenticated, authorization-checked download route rather than a static mount; the prompt describes a minimal version (auth gate plus path containment) and notes the larger multi-tenant direction so you do not paint yourself into a corner.

### C3 / H2 - The two authentication surfaces are inconsistent

You hardened the **WebSocket** endpoint well: per-IP failure throttling (`ws_auth_is_rate_limited`), optional shared key, `outlook_id` uniqueness check. But the **web login** in `main_nice.py` has none of that. An attacker can hammer `/login` at full speed. The asymmetry suggests the login throttle simply has not been done yet. The cleanest fix reuses the same per-IP limiter you already wrote for the WS path; the C3 prompt points at that existing function so you are not inventing a second mechanism.

Separately (H2): `WS_API_KEY` defaults to empty, which disables the key check entirely. For a product whose whole pitch is sovereignty and privacy, shipping with the auth control off by default is the wrong default. Make it required, or at minimum log a loud warning at startup when it is empty.

### H3 - Logging defeats the privacy design

This is the one I would treat as effectively higher than its "HIGH" label for *your* positioning. The entire NER-masking apparatus exists so that email content never reaches the cloud unmasked. Yet `loopback_processor.py` logs the full incoming body at INFO:

```python
log.info(f"... Incoming email | Subject='{subject}' | Body ({len(body)} chars):\n{body}")
```

and the optimizer logs masked-but-still-sensitive text at DEBUG. The logs rotate to disk for 30 days. So the privacy guarantee is undermined not by the cloud path but by your own log files. The fix is to log lengths and hashes, never content, and to route any content-bearing diagnostic through a separate, default-off channel. The H3 prompt enumerates every offending call site I found.

### M1 - The silent dictionary corruption (new)

In `readers.py`, the German block of `COMMON_WORDS` ends:

```python
        "strasse", "tel", "herr", "frau", "total", "chf"
        # French
        "le", "la", "les", ...
```

There is no comma after `"chf"`. Python concatenates adjacent string literals, so the set actually contains `"chfle"` instead of `"chf"` and `"le"`. Two real consequences: the token `chf` is no longer recognized as a common word, and a phantom token `chfle` is. Since this dictionary drives the "is this native text or do we need OCR?" ratio, the bug quietly biases the heuristic. It is invisible because the code runs without error. I verified the concatenation by reproducing the literal. Trivial to fix, worth fixing because it is exactly the kind of bug that erodes trust in retrieval quality without ever announcing itself.

### M2 - History fed twice (new)

In `run_chat_action`:

```python
if hasattr(engine, 'memory') and engine.memory:
    engine.memory.chat_history = [m for m in self.global_history]
...
history_str = self._format_history_as_text()      # also builds the same history as text
final_input = (f"{history_str}\nDOMANDA UTENTE: {clean_query}")
```

For chat engines that carry memory (`ContextChatEngine`, `SimpleChatEngine`), the same conversation is now present both in the engine's memory buffer and pasted verbatim into the prompt. On a 4096-token local model this is expensive and can confuse the model into treating its own past turns as new instructions. I would pick one channel. The M2 prompt lays out the decision (prefer engine memory for memoryful engines; keep the text-injection path only for the stateless cloud branch, if at all) and flags that this needs a human call because it touches working logic, per your preference.

### M8 - Agent text extraction (new)

The workflow `ReActAgent` path does `response_text = str(response_obj)`. Depending on the installed LlamaIndex version, the workflow result is not always a plain string under `str()`; it can stringify to a repr or an object wrapper. Because this path only triggers on email/calendar agent intents, it may have gone untested. I flag it as needs-verification rather than a confirmed defect, since I could not run the agent here. The prompt asks Claude Code to confirm the actual return type against the pinned version before changing anything.

---

## 4. Cross-cutting observations (not single-file issues)

- **Testing is import-only.** `tests/test_imports.py` proves modules load; nothing exercises behaviour. The highest-value targets for real tests are `decide_engine` routing, the rate limiter window math, `mask_pii` / `unmask_pii` round-tripping, and the pending-results claim/deliver dance. This is captured as enhancement E7.
- **`decide_engine` mixes three strategies** (explicit `@` tags, keyword triggers, then an LLM fallback classify). It works but is hard to reason about and untested. Worth a focused refactor *with tests first* so the refactor is safe. See E8.
- **Single shared document collection.** Fine today, but multi-tenant isolation (a `tenant_id` metadata filter on retrieval) is the natural next step for a fiduciary product and is cheap to add now, expensive to retrofit later. See E2.
- **No audit trail.** Regulated Swiss contexts often expect access logging. An append-only audit of "who asked what, which documents were retrieved" is both a compliance asset and a debugging aid. See E5.

---

## 5. Proposed enhancements

Technical and functional, each with its own prompt file. I have deliberately kept these separate from bug fixes so you can schedule them independently.

| ID | Enhancement | Type | Why it earns its place |
|----|-------------|------|------------------------|
| E1 | Streaming responses in the web UI (`astream_chat`) | Technical/UX | Removes the 30s spinner; you already use streaming in `rag_query.py`, so the pattern exists |
| E2 | Multi-tenant document isolation via `tenant_id` metadata filter | Functional | Core to a fiduciary product; cheap now, costly later |
| E3 | Email thread context in loopback (use `conversation_id` to include prior turns) | Functional | The processor treats each email standalone today; replies lose context |
| E4 | Incremental BM25 updates instead of full rebuild | Technical/Perf | Removes the O(n) ingestion bottleneck (ties to M4) |
| E5 | Append-only audit log of access and retrieval | Functional/Compliance | Regulatory fit for Swiss fiduciary use |
| E6 | Session cache / pool for loopback `UserSession` | Technical/Perf | Removes per-email re-init (ties to M5) |
| E7 | Real behavioural test suite + CI | Technical | Converts "it imports" into "it works"; protects every future change |
| E8 | Refactor `decide_engine` into an explicit, tested router | Technical | Makes the most fragile routing logic legible and safe to evolve |
| E9 | Pluggable LLM provider abstraction (incl. Apertus / Claude / others) | Technical | You already announced Apertus support; an abstraction makes that real |
| E10 | Health check that probes Ollama, ChromaDB, and disk | Operational | `/health` returns a static "ok" today; make it mean something |

---

## 6. Suggested sequencing

A pragmatic order that front-loads risk reduction without blocking on the big refactors:

1. **Week 1 - stop the bleeding (CRITICAL + the cheapest HIGH):** C1, C3, H4, H3. These are small, high-leverage, and H4 unblocks anyone trying to install the project.
2. **Week 2 - close the confidentiality gaps:** C2, H2, H1 (the last likely a reverse-proxy/ops task rather than code).
3. **Week 3 - correctness:** M1, M2, M8, H5, M6. Small bugs, real quality impact. M2 and M8 touch working logic, so they go through review before merge.
4. **Then, as capacity allows:** the performance items (M4/E4, M5/E6), the test suite (E7) which should ideally come *before* E8, and the functional enhancements (E2, E3, E5) driven by product priority.

E7 before E8 is deliberate: do not refactor the routing logic until tests exist to prove the refactor preserved behaviour.

---

## 7. Index of prompt files

Each file in `prompts/` is a standalone brief for Claude Code: it states the problem, points at exact files and lines, gives acceptance criteria, and flags anything that needs a human decision before code changes (consistent with not modifying working logic unprompted).

```
prompts/
  C1_storage_secret_failfast.md
  C2_document_access_control.md
  C3_login_bruteforce_protection.md
  H1_transport_encryption.md
  H2_websocket_auth_hardening.md
  H3_pii_logging.md
  H4_requirements_encoding.md
  H5_atomic_users_json.md
  M1_common_words_comma_bug.md
  M2_double_history_injection.md
  M3_hashing_md5_and_double_hash.md
  M4_bm25_incremental_index.md
  M5_loopback_session_reuse.md
  M6_upload_truncation_warning.md
  M7_gemini_model_config.md
  M8_agent_response_extraction.md
  M9_ingestion_lock_atomicity.md
  L1_bare_except_logging.md
  L2_temp_upload_file_handling.md
  L3_language_consistency.md
  L4_packaging_pyproject.md
  E1_streaming_web_responses.md
  E2_multitenant_isolation.md
  E3_email_thread_context.md
  E4_incremental_bm25.md
  E5_audit_log.md
  E6_session_pool.md
  E7_test_suite_ci.md
  E8_decide_engine_refactor.md
  E9_pluggable_llm_providers.md
  E10_meaningful_health_check.md
```

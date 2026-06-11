# ArcumAI — Automated Test Suite

**134 tests · 3 tiers · ~25 seconds**
Last updated: 2026-06-11

## How to run

```powershell
# All tests
.venv/Scripts/python.exe -m pytest tests/ -v

# One tier at a time
.venv/Scripts/python.exe -m pytest tests/test_imports.py tests/test_auth.py tests/test_rate_limiter.py tests/test_ner_masking.py tests/test_loopback_queue.py tests/test_pending_results.py -v   # Tier 1

.venv/Scripts/python.exe -m pytest tests/test_prompt_optimizer.py tests/test_readers.py tests/test_config.py tests/test_conversations.py -v   # Tier 2

.venv/Scripts/python.exe -m pytest tests/test_bridge_manager.py tests/test_ingest_pipeline.py -v   # Tier 3
```

---

## Tier 1 — Fast, no external dependencies

No LLM, no DB, no network. Suitable as a pre-commit gate (~5–10 s).

---

### `test_imports.py` — Module smoke tests (16 tests)

Verifies every public module can be imported without errors. Covers the full
refactoring from Phases 1–4.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_config` | `src.config` imports cleanly |
| 2 | `test_auth` | `src.auth` imports cleanly |
| 3 | `test_logger` | `src.logger` imports cleanly |
| 4 | `test_readers` | `src.readers` imports cleanly |
| 5 | `test_utils` | `src.utils` imports cleanly |
| 6 | `test_bridge` | `src.bridge.bridge_manager` imports cleanly |
| 7 | `test_engine` | `src.engine.UserSession` re-export works |
| 8 | `test_ai_package` | `src.ai` package imports cleanly |
| 9 | `test_ner_masking` | `mask_pii`, `unmask_pii`, `is_presidio_available` importable |
| 10 | `test_engines_module` | `load_rag_engine`, `load_simple_local_engine`, `load_cloud_engine` importable |
| 11 | `test_prompt_optimizer_module` | `optimize_prompt_for_rag` importable |
| 12 | `test_bridge_package` | `src.bridge` package + bridge_manager re-export works |
| 13 | `test_pending_results_module` | `PendingResultStore` importable |
| 14 | `test_loopback_queue_module` | `_EmailTask`, `_UserQueue` importable |
| 15 | `test_loopback_processor_module` | `LoopbackProcessor` importable |
| 16 | `test_ai_session_module` | `UserSession` importable from both `src.ai.session` and `src.engine` |

---

### `test_auth.py` — Authentication & WebSocket rate limiting (12 tests)

Covers `src/auth.py`: bcrypt hashing, password verification, user persistence,
and per-IP WebSocket brute-force protection.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_hash_password_produces_bcrypt_hash` | Output starts with `$2b$` (bcrypt format) |
| 2 | `test_hash_password_produces_different_hashes_for_same_input` | Each call uses a fresh random salt |
| 3 | `test_verify_password_correct` | Returns `True` for correct password |
| 4 | `test_verify_password_wrong` | Returns `False` for wrong password |
| 5 | `test_verify_password_handles_garbage` | Returns `False` for a non-bcrypt hash string |
| 6 | `test_load_users_returns_empty_dict_for_missing_file` | Graceful empty-DB fallback |
| 7 | `test_load_users_reads_json` | Parses a well-formed `users.json` correctly |
| 8 | `test_add_user_persists_and_verifies` | Writes user to disk; password roundtrip succeeds |
| 9 | `test_add_user_rejects_weak_password` | Passwords failing the policy return `False` |
| 10 | `test_ws_auth_not_rate_limited_for_fresh_ip` | New IP is not blocked |
| 11 | `test_ws_auth_rate_limited_after_max_attempts` | IP blocked after `WS_AUTH_MAX_ATTEMPTS` failures |
| 12 | `test_ws_auth_resets_after_window_expires` | Stale timestamps outside the window are ignored |

---

### `test_rate_limiter.py` — Input sanitization & per-user rate limiting (9 tests)

Covers `src/ui/rate_limiter.py`: control-character stripping, length capping,
and per-user message-rate enforcement.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_sanitize_input_strips_control_chars` | `\x00`, `\x1b`, `\x08` are removed |
| 2 | `test_sanitize_input_preserves_newline_and_tab` | `\n`, `\t`, `\r` are allowed |
| 3 | `test_sanitize_input_truncates_long_input` | Output capped at `MAX_INPUT_LENGTH` |
| 4 | `test_sanitize_input_strips_leading_trailing_whitespace` | Leading/trailing spaces stripped |
| 5 | `test_sanitize_input_normal_text_unchanged` | Ordinary text passes through unmodified |
| 6 | `test_check_rate_limit_allows_within_limit` | First message from a user is allowed |
| 7 | `test_check_rate_limit_blocks_when_limit_reached` | Returns `False` after `RATE_LIMIT_MESSAGES` calls |
| 8 | `test_check_rate_limit_independent_per_user` | Blocking one user does not block another |
| 9 | `test_check_rate_limit_resets_after_window_expires` | Stale timestamps are purged; user can send again |

---

### `test_ner_masking.py` — PII masking & de-anonymization (8 tests)

Covers `src/ai/ner_masking.py`: Presidio-based masking, placeholder reversal,
and graceful degradation when Presidio is not installed.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_is_presidio_available_returns_bool` | Function returns a bool without raising |
| 2 | `test_mask_pii_empty_string` | Empty input returns `("", {})` |
| 3 | `test_mask_pii_returns_text_and_dict` | Output types are always `(str, dict)` |
| 4 | `test_unmask_pii_empty_metadata_returns_original` | Empty metadata → text unchanged |
| 5 | `test_unmask_pii_restores_placeholder` | Single `__PII_xxxxxxxx__` token is replaced with original value |
| 6 | `test_unmask_pii_restores_multiple_placeholders` | Multiple tokens all restored correctly |
| 7 | `test_unmask_pii_missing_placeholder_left_unchanged` | Tokens absent from the text are skipped gracefully |
| 8 | `test_mask_unmask_roundtrip` | If Presidio available: `unmask(mask(text)) == text`; otherwise text passes through unchanged |

---

### `test_loopback_queue.py` — Priority queue ordering (7 tests)

Covers `src/bridge/loopback_queue.py`: `_EmailTask` comparison semantics and
`_UserQueue` async enqueue/dequeue behaviour.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_email_task_high_priority_less_than_normal` | Priority 0 (High) < Priority 1 (Normal) |
| 2 | `test_email_task_fifo_tiebreaker_for_equal_priority` | Lower sequence number wins on tie |
| 3 | `test_email_task_high_priority_beats_earlier_sequence` | Priority takes precedence over sequence |
| 4 | `test_user_queue_initializes_empty` | New queue is empty, worker is `None`, sequence is 0 |
| 5 | `test_user_queue_enqueue_dequeue` | A single enqueued task is dequeued with correct payload |
| 6 | `test_user_queue_respects_priority_order` | High-priority task dequeued before Normal-priority task |
| 7 | `test_user_queue_fifo_within_same_priority` | Three equal-priority tasks dequeued in insertion order |

---

### `test_pending_results.py` — Offline result persistence (8 tests)

Covers `src/bridge/pending_results.py`: disk-based store/retrieve/delete lifecycle
and multi-user isolation.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_save_creates_file` | `save()` writes one JSON file to the temp dir |
| 2 | `test_find_returns_matching_result` | `find()` retrieves the correct payload by user + conversation ID |
| 3 | `test_find_returns_none_for_unknown_user` | `find()` returns `None` when user has no files |
| 4 | `test_find_returns_none_for_unknown_conversation` | `find()` returns `None` when conversation ID doesn't match |
| 5 | `test_delete_removes_file` | `delete()` removes the file from disk |
| 6 | `test_find_returns_none_after_delete` | File no longer findable after deletion |
| 7 | `test_save_different_users_no_collision` | Two users' results don't interfere; cross-user lookup returns `None` |
| 8 | `test_save_payload_includes_required_fields` | Saved JSON contains `user_id`, `request_id`, `conversation_id`, `created_at`, `response` |

---

## Tier 2 — Unit tests with mocking

No live LLM, no real DB. Dependencies are mocked via `monkeypatch`. (~15–20 s)

---

### `test_prompt_optimizer.py` — RAG prompt optimization pipeline (8 tests)

Covers `src/ai/prompt_optimizer.py`: mode routing, fallback behaviour, and LLM
call delegation.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_mode_off_returns_raw_email_format` | `mode="off"` returns `"Email Subject: …\n\n…"` verbatim |
| 2 | `test_mode_off_empty_body` | Empty body handled without error |
| 3 | `test_raises_on_oversized_input` | Input > `_MAX_INPUT_CHARS` raises `ValueError` |
| 4 | `test_mode_local_calls_llm` | `mode="local"` calls `Settings.llm.acomplete` exactly once |
| 5 | `test_mode_local_falls_back_to_raw_on_llm_error` | Ollama error → raw email returned, no exception |
| 6 | `test_unknown_mode_falls_back_to_local` | Unknown mode string routes to local LLM |
| 7 | `test_mode_gemini_calls_gemini` | `mode="gemini"` calls the Gemini client's `acomplete` |
| 8 | `test_mode_gemini_falls_back_to_raw_on_api_error` | Gemini API error → raw email returned, no exception |

---

### `test_readers.py` — File hashing, PDF reader, and upload validation (13 tests)

Covers `src/utils.py` (`calcola_hash_file`), `src/readers.py` (`SmartPDFReader`),
and `src/ui/footer.py` (`_validate_file_content`).

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_hash_same_content_produces_same_hash` | Hashing the same file twice gives the same MD5 |
| 2 | `test_hash_is_32_char_md5_hex` | Output is a 32-character lowercase hex string |
| 3 | `test_hash_different_content_different_hash` | Different file content produces a different hash |
| 4 | `test_hash_missing_file_returns_error_sentinel` | Non-existent file returns `"hash_error"` |
| 5 | `test_smart_pdf_reader_missing_file_returns_empty` | Non-existent PDF → `[]` (no exception) |
| 6 | `test_is_text_meaningful_accepts_rich_italian_text` | Italian business text scores above 10% threshold |
| 7 | `test_is_text_meaningful_rejects_short_text` | Fewer than 10 words → `False` |
| 8 | `test_is_text_meaningful_rejects_garbage` | Random token soup scores 0% → `False` |
| 9 | `test_validate_accepts_valid_pdf_magic_bytes` | Bytes starting with `%PDF` pass `.pdf` check |
| 10 | `test_validate_rejects_wrong_bytes_as_pdf` | Non-PDF bytes fail `.pdf` check |
| 11 | `test_validate_accepts_valid_utf8_txt` | Valid UTF-8 bytes pass `.txt` check |
| 12 | `test_validate_accepts_valid_utf8_md` | Valid UTF-8 bytes pass `.md` check |
| 13 | `test_validate_rejects_binary_as_txt` | Non-UTF-8 bytes fail `.txt` check |

---

### `test_config.py` — Configuration constants (10 tests)

Covers `src/config.py`: type safety and sanity checks for all critical constants.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_base_dir_is_path_and_exists` | `BASE_DIR` is a `Path` pointing to an existing directory |
| 2 | `test_key_path_constants_are_path_objects` | `CHROMA_PATH`, `BM25_PATH`, `INBOX_DIR`, `ARCHIVE_DIR`, `LOG_DIR` are all `Path` objects |
| 3 | `test_rate_limit_constants_are_positive_ints` | All four rate-limit constants are positive integers |
| 4 | `test_ws_auth_constants_are_positive` | `WS_AUTH_MAX_ATTEMPTS` and `WS_AUTH_WINDOW` are positive |
| 5 | `test_chunk_size_greater_than_overlap` | `CHUNK_SIZE > CHUNK_OVERLAP > 0` |
| 6 | `test_valid_roles_contains_expected_roles` | `ADMIN`, `LEGAL`, `EXECUTIVE`, `DEFAULT` all present in `VALID_ROLES` |
| 7 | `test_default_system_prompt_is_nonempty_string` | System prompt is a non-empty string longer than 50 chars |
| 8 | `test_model_name_constants_are_nonempty_strings` | `LLM_MODEL_NAME` and `EMBED_MODEL_NAME` are non-empty strings |
| 9 | `test_vsto_email_looks_like_email` | `VSTO_ARCUMAI_EMAIL` contains `@` |
| 10 | `test_pending_result_ttl_is_positive` | `PENDING_RESULT_TTL_HOURS` is a positive integer |

---

### `test_conversations.py` — Conversation persistence (14 tests)

Covers `src/conversations.py`: full CRUD lifecycle, auto-titling, multi-message
accumulation, user isolation, and empty-conversation cleanup.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_create_conversation_returns_string_id` | `create_conversation()` returns a non-empty string |
| 2 | `test_load_conversation_returns_empty_messages` | Newly created conversation has `messages == []` |
| 3 | `test_load_conversation_returns_none_for_missing` | Non-existent ID returns `None` |
| 4 | `test_append_message_persists_to_disk` | Message survives a `load_conversation` roundtrip |
| 5 | `test_append_message_auto_titles_from_first_user_message` | First user message becomes the conversation title |
| 6 | `test_append_message_title_not_overwritten_by_assistant` | Assistant reply does not overwrite the title |
| 7 | `test_multiple_messages_accumulate` | Four alternating messages all persist |
| 8 | `test_list_conversations_returns_summaries` | Returns list with `id`, `title`, `created_at`, `message_count` |
| 9 | `test_list_conversations_empty_for_unknown_user` | Unknown user returns `[]` |
| 10 | `test_delete_conversation_removes_it` | `delete_conversation()` returns `True`; `load` returns `None` after |
| 11 | `test_delete_nonexistent_conversation_returns_false` | Deleting a ghost ID returns `False` |
| 12 | `test_cleanup_empty_removes_zero_message_conversations` | Empty conversations are deleted; count returned |
| 13 | `test_cleanup_empty_preserves_conversations_with_messages` | Non-empty conversations survive cleanup |
| 14 | `test_users_are_isolated` | User A's conversations invisible to User B |

---

## Tier 3 — Integration tests

Uses real async event loops and real ChromaDB. LLM and embedding models are
mocked to keep the suite offline-capable. (~15 s)

---

### `test_bridge_manager.py` — WebSocket bridge manager (18 tests)

Covers `src/bridge/manager.py`: connection lifecycle, message routing, future
resolution, client config, and log-injection safety. Uses mock WebSockets
(`AsyncMock`).

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_connect_adds_to_active_connections` | `connect()` stores the WebSocket in `active_connections` |
| 2 | `test_connect_calls_websocket_accept` | `connect()` calls `ws.accept()` exactly once |
| 3 | `test_disconnect_removes_from_active_connections` | `disconnect()` removes the user entry |
| 4 | `test_disconnect_clears_client_type` | `disconnect()` removes the user from `client_types` |
| 5 | `test_disconnect_cancels_pending_futures` | Pending MCP futures are resolved and removed on disconnect |
| 6 | `test_disconnect_unknown_user_does_not_raise` | Calling `disconnect()` for a user that never connected is safe |
| 7 | `test_handle_heartbeat_does_not_raise` | Heartbeat messages are silently consumed |
| 8 | `test_handle_tool_response_resolves_future` | Tool-call response resolves the matching future with the correct result |
| 9 | `test_handle_tool_error_response_resolves_future` | Error response still resolves the future (no hang) |
| 10 | `test_handle_invalid_json_does_not_raise` | Malformed JSON is logged and swallowed, no crash |
| 11 | `test_handle_client_identify_updates_client_type` | `client/identify` message sets `client_types[user_id]` |
| 12 | `test_handle_client_identify_sends_config_response` | `client/identify` triggers a `send_text` response to the client |
| 13 | `test_send_mcp_request_no_connection_returns_warning` | Requesting a tool with no active WS returns a warning string |
| 14 | `test_build_client_config_vsto_outlook_returns_required_keys` | Config block for `vsto_outlook` contains all required keys |
| 15 | `test_build_client_config_unknown_type_returns_empty_dict` | Unknown client type returns `{}` |
| 16 | `test_safe_uid_strips_newline` | `\n` in a user ID is escaped (log injection prevention) |
| 17 | `test_safe_uid_strips_carriage_return` | `\r` in a user ID is escaped |
| 18 | `test_safe_uid_preserves_normal_username` | Normal `user@example.com` string is unchanged |

---

### `test_ingest_pipeline.py` — Batch ingestion pipeline (11 tests)

Covers `ingest.py`: file-system lock, `read_and_chunk_file` for all relevant
cases, and ChromaDB collection wiring. `init_settings()` is mocked at import
time to avoid loading ML models; `VectorStoreIndex` is mocked in DB tests for
the same reason.

| # | Test | What it checks |
|---|------|----------------|
| 1 | `test_acquire_lock_succeeds_when_no_lock_exists` | First `acquire_lock()` call returns `True` |
| 2 | `test_acquire_lock_fails_when_already_locked` | Second `acquire_lock()` call returns `False` |
| 3 | `test_release_lock_removes_lock_file` | `release_lock()` deletes the lock file |
| 4 | `test_release_lock_is_safe_when_no_lock_exists` | `release_lock()` with no lock file does not raise |
| 5 | `test_read_and_chunk_txt_file_returns_nodes` | A `.txt` file produces at least one `TextNode` |
| 6 | `test_read_and_chunk_txt_nodes_carry_file_metadata` | Each node has `file_hash` and `filename` in its metadata |
| 7 | `test_read_and_chunk_empty_txt_returns_empty` | Whitespace-only `.txt` → `(None, "EMPTY")` |
| 8 | `test_read_and_chunk_unsupported_extension_returns_skip` | `.exe` file → `(None, "SKIP_EXT")` |
| 9 | `test_read_and_chunk_nonexistent_file_returns_error` | Missing file → `(None, "ERROR")` |
| 10 | `test_get_db_components_creates_chroma_collection` | ChromaDB client + collection created; `VectorStoreIndex.from_vector_store` called |
| 11 | `test_get_db_components_collection_starts_empty` | Freshly created collection has `count() == 0` |

---

## Known limitations / non-goals

- **No UI tests** — NiceGUI components (`create_footer`, `create_header`, etc.) require a running browser; correctness is verified by manual smoke test.
- **No `main_nice.py` startup test** — The `STORAGE_SECRET` fail-fast check lives inside `if __name__ == "__main__"` and is verified by the security review + manual startup.
- **No Ollama / Gemini live calls** — LLM calls in Tier 2 & 3 are mocked; actual model quality is validated by the smoke-test protocol (start server + send one virtual loopback email).
- **C# VSTO plugin** — Not covered; plugin is tested via Visual Studio's build + manual Outlook testing.

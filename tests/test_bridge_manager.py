# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock


def _make_manager():
    """Fresh OutlookBridgeManager for each test — not the global singleton."""
    from src.bridge.manager import OutlookBridgeManager
    return OutlookBridgeManager()


def _mock_ws():
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# --- Connection lifecycle ---

def test_connect_adds_to_active_connections():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        return "alice" in mgr.active_connections
    assert asyncio.run(run())


def test_connect_calls_websocket_accept():
    async def run():
        mgr = _make_manager()
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        ws.accept.assert_called_once()
    asyncio.run(run())


def test_disconnect_removes_from_active_connections():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        mgr.disconnect("alice")
        return "alice" in mgr.active_connections
    assert not asyncio.run(run())


def test_disconnect_clears_client_type():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        mgr.client_types["alice"] = "vsto_outlook"
        mgr.disconnect("alice")
        return "alice" in mgr.client_types
    assert not asyncio.run(run())


def test_disconnect_cancels_pending_futures():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        future = asyncio.get_running_loop().create_future()
        mgr.pending_requests["req-001"] = future
        mgr.disconnect("alice")
        return future.done() and "req-001" not in mgr.pending_requests
    assert asyncio.run(run())


def test_disconnect_unknown_user_does_not_raise():
    mgr = _make_manager()
    mgr.disconnect("nobody")  # must not raise


# --- handle_incoming_message ---

def test_handle_heartbeat_does_not_raise():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        await mgr.handle_incoming_message("alice", json.dumps({"method": "heartbeat"}))
    asyncio.run(run())


def test_handle_heartbeat_sends_ack():
    async def run():
        mgr = _make_manager()
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await mgr.handle_incoming_message("alice", json.dumps({"method": "heartbeat"}))
        ws.send_text.assert_called_once_with(json.dumps({"method": "heartbeat/ack"}))
    asyncio.run(run())


def test_handle_tool_response_resolves_future():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        future = asyncio.get_running_loop().create_future()
        mgr.pending_requests["req-002"] = future
        msg = json.dumps({"id": "req-002", "result": {"emails": []}})
        await mgr.handle_incoming_message("alice", msg)
        return future.done() and future.result() == {"emails": []}
    assert asyncio.run(run())


def test_handle_tool_error_response_resolves_future():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        future = asyncio.get_running_loop().create_future()
        mgr.pending_requests["req-003"] = future
        msg = json.dumps({"id": "req-003", "error": {"message": "Not found"}})
        await mgr.handle_incoming_message("alice", msg)
        return future.done()
    assert asyncio.run(run())


def test_handle_invalid_json_does_not_raise():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        await mgr.handle_incoming_message("alice", "{{ not valid json")
    asyncio.run(run())


def test_handle_client_identify_updates_client_type():
    async def run():
        mgr = _make_manager()
        await mgr.connect(_mock_ws(), "alice")
        msg = json.dumps({
            "method": "client/identify",
            "id": "req-004",
            "params": {"client_type": "vsto_outlook", "client_version": "1.2"}
        })
        await mgr.handle_incoming_message("alice", msg)
        await asyncio.sleep(0)  # let the deliver task schedule
        return mgr.client_types.get("alice")
    assert asyncio.run(run()) == "vsto_outlook"


def test_handle_client_identify_sends_config_response():
    async def run():
        mgr = _make_manager()
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        msg = json.dumps({
            "method": "client/identify",
            "id": "req-005",
            "params": {"client_type": "vsto_outlook", "client_version": "1.0"}
        })
        await mgr.handle_incoming_message("alice", msg)
        return ws.send_text.called
    assert asyncio.run(run())


# --- send_mcp_request without active connection ---

def test_send_mcp_request_no_connection_returns_warning():
    async def run():
        mgr = _make_manager()
        return await mgr.send_mcp_request("nobody", "search_emails", {"query": "test"})
    result = asyncio.run(run())
    assert isinstance(result, str) and len(result) > 0


# --- _build_client_config ---

def test_build_client_config_vsto_outlook_returns_required_keys():
    mgr = _make_manager()
    cfg = mgr._build_client_config("vsto_outlook")
    for key in ("arcumai_email", "max_attachment_size_mb", "enable_virtual_loopback",
                "loopback_timeout_ms", "show_processing_notification"):
        assert key in cfg, f"Missing key: {key}"


def test_build_client_config_unknown_type_returns_empty_dict():
    mgr = _make_manager()
    assert mgr._build_client_config("mystery_client") == {}


# --- log-injection safety ---

def test_safe_uid_strips_newline():
    from src.bridge.manager import _safe_uid
    sanitized = _safe_uid("user\ninjected_line")
    assert "\n" not in sanitized


def test_safe_uid_strips_carriage_return():
    from src.bridge.manager import _safe_uid
    sanitized = _safe_uid("user\rinjected")
    assert "\r" not in sanitized


def test_safe_uid_preserves_normal_username():
    from src.bridge.manager import _safe_uid
    assert _safe_uid("alice@example.com") == "alice@example.com"


# ---------------------------------------------------------------------------
# Issue #54: per-user limit on emails waiting in the loopback queue.
#
# Approved design (D1-D5): setting LOOPBACK_MAX_QUEUED_PER_USER (src/config.py,
# default 10, range 1-1000), read at call time in _enqueue_email. The limit
# counts emails WAITING in the user's queue; the email held by the worker is
# not counted. Above the limit the email is refused with an immediate
# virtual_loopback/response push using the existing fields. The duplicate
# check runs first. If the socket is gone, the busy reply is dropped and
# logged, never written to disk. The refusal log contains no email content.
#
# All data is synthetic. The real AI processing is replaced by a fake that
# blocks on an asyncio.Event so the worker holds exactly one email.
# ---------------------------------------------------------------------------

import base64
import logging

_LOOPBACK_RESPONSE_FIELDS = {
    "request_id", "subject", "conversation_id", "original_message_id",
    "response_text", "response_html",
}
_SYNTHETIC_ATTACHMENT = base64.b64encode(b"synthetic attachment bytes 0123456789\n" * 64).decode("ascii")


class _BlockingProcessor:
    """Replaces OutlookBridgeManager._process_loopback_email. Records every call
    and blocks until ``release`` is set, so the worker holds one email."""

    def __init__(self):
        self.calls = []
        self.release = asyncio.Event()

    async def __call__(self, user_id, request_id, params):
        self.calls.append((user_id, request_id, params))
        await self.release.wait()

    def request_ids(self):
        return [c[1] for c in self.calls]


def _set_limit(monkeypatch, limit):
    """Set LOOPBACK_MAX_QUEUED_PER_USER where the implementation may read it.
    raising=False: the setting does not exist before the fix."""
    import src.config
    import src.bridge.manager
    monkeypatch.setattr(src.config, "LOOPBACK_MAX_QUEUED_PER_USER", limit, raising=False)
    monkeypatch.setattr(src.bridge.manager, "LOOPBACK_MAX_QUEUED_PER_USER", limit, raising=False)


def _queue_manager(monkeypatch, tmp_path, limit):
    """Manager with an isolated pending-results directory and a blocking processor."""
    import src.config
    monkeypatch.setattr(src.config, "PENDING_RESULTS_DIR", str(tmp_path / "pending"))
    _set_limit(monkeypatch, limit)
    mgr = _make_manager()
    proc = _BlockingProcessor()
    mgr._process_loopback_email = proc
    return mgr, proc


def _email_params(n, importance=1, conversation_id=None, subject=None):
    return {
        "subject": subject or f"Synthetic subject {n}",
        "body": f"Synthetic body {n}. No real data.",
        "conversation_id": conversation_id or f"synthetic-conv-{n}",
        "original_message_id": f"<synthetic-{n}@example.invalid>",
        "importance": importance,
        "has_attachments": True,
        "attachments": [{"file_name": f"synthetic_{n}.txt", "content_base64": _SYNTHETIC_ATTACHMENT}],
        "cc_recipients": [],
        "skipped_attachments": [],
    }


async def _send_email(mgr, user_id, request_id, params):
    msg = {"jsonrpc": "2.0", "id": request_id, "method": "virtual_loopback/send_email", "params": params}
    await mgr.handle_incoming_message(user_id, json.dumps(msg))


async def _settle(rounds=50):
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _wait_until(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


def _sent(ws):
    """All JSON messages sent on a mock websocket, in order."""
    out = []
    for call in ws.send_text.call_args_list:
        try:
            out.append(json.loads(call.args[0]))
        except Exception:
            pass
    return out


def _responses_for(ws, request_id):
    return [m for m in _sent(ws)
            if m.get("method") == "virtual_loopback/response"
            and m.get("params", {}).get("request_id") == request_id]


def _acks_for(ws, request_id):
    return [m for m in _sent(ws) if m.get("id") == request_id and "result" in m]


def _is_busy_reply(msg):
    text = (msg.get("params", {}).get("response_text") or "").lower()
    return "busy" in text and "not processed" in text


async def _fill_queue(mgr, proc, user_id, limit, prefix="r"):
    """Send one email that the worker takes and holds, then ``limit`` more that wait.
    Returns the request ids of the waiting emails."""
    await _send_email(mgr, user_id, f"{prefix}-held", _email_params(f"{prefix}-held"))
    assert await _wait_until(lambda: f"{prefix}-held" in proc.request_ids()), "worker never took the first email"
    waiting = []
    for i in range(limit):
        rid = f"{prefix}-wait-{i}"
        await _send_email(mgr, user_id, rid, _email_params(rid))
        waiting.append(rid)
    await _settle()
    return waiting


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [r.getMessage() for r in self.records]


# --- Criterion 1: over the limit -> busy reply within 10 s, email not kept ---

def test_ac1_email_over_limit_gets_busy_reply_within_10_seconds(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=2)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 2)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await _send_email(mgr, "alice", "r-over", _email_params("r-over"))
        got = await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0, timeout=10.0)
        elapsed = loop.time() - start

        assert got, "no virtual_loopback/response for the email over the limit within 10 s"
        assert elapsed < 10.0
        replies = _responses_for(ws, "r-over")
        assert len(replies) == 1, f"expected exactly one reply, got {len(replies)}"
        assert _is_busy_reply(replies[0]), (
            f"reply does not say busy / not processed: {replies[0]['params'].get('response_text')!r}")
    asyncio.run(run())


def test_ac1_busy_reply_uses_existing_response_fields_only(monkeypatch, tmp_path):
    """Option A: no protocol change; same push shape as the size-limit reply."""
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        params = _email_params("r-over")
        await _send_email(mgr, "alice", "r-over", params)
        assert await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0)
        msg = _responses_for(ws, "r-over")[0]
        assert set(msg.keys()) == {"jsonrpc", "method", "params"}
        assert msg["jsonrpc"] == "2.0"
        assert set(msg["params"].keys()) == _LOOPBACK_RESPONSE_FIELDS
        assert msg["params"]["subject"] == params["subject"]
        assert msg["params"]["conversation_id"] == params["conversation_id"]
        assert msg["params"]["original_message_id"] == params["original_message_id"]
        assert msg["params"]["response_html"], "response_html must not be empty"
    asyncio.run(run())


def test_ac1_busy_reply_states_the_configured_limit(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=3)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 3)
        await _send_email(mgr, "alice", "r-over", _email_params("r-over"))
        assert await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0)
        text = _responses_for(ws, "r-over")[0]["params"]["response_text"]
        assert "3" in text, f"busy text should state the limit (3): {text!r}"
    asyncio.run(run())


def test_ac1_refused_email_is_not_kept_in_queue(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=2)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 2)
        uq = mgr._user_queues["alice"]
        assert uq.queue.qsize() == 2

        await _send_email(mgr, "alice", "r-over", _email_params("r-over"))
        await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0)
        await _settle()

        assert uq.queue.qsize() == 2, "queue depth changed: refused email was enqueued"
        queued_ids = [t.request_id for t in list(uq.queue._queue)]
        assert "r-over" not in queued_ids
    asyncio.run(run())


def test_ac1_refused_email_is_never_processed(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        await _send_email(mgr, "alice", "r-over", _email_params("r-over"))
        assert await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0), "email was not refused"
        proc.release.set()
        await _wait_until(lambda: len(proc.calls) >= 2)
        await asyncio.sleep(0.05)
        assert "r-over" not in proc.request_ids()
    asyncio.run(run())


def test_ac1_email_held_by_worker_is_not_counted(monkeypatch, tmp_path):
    """Limit L counts waiting emails: the worker holds one more, so L+1 are accepted."""
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=2)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        waiting = await _fill_queue(mgr, proc, "alice", 2)
        for rid in ["r-held"] + waiting:
            assert not _responses_for(ws, rid), f"{rid} was refused"
        assert mgr._user_queues["alice"].queue.qsize() == 2
        # ...and the next one is refused, so the boundary is exactly L waiting.
        await _send_email(mgr, "alice", "r-over", _email_params("r-over"))
        assert await _wait_until(lambda: len(_responses_for(ws, "r-over")) > 0), "L+2nd email was not refused"
    asyncio.run(run())


def test_ac1_concurrent_burst_never_exceeds_limit_and_loses_nothing(monkeypatch, tmp_path):
    """Burst of messages handled back to back: queue depth never exceeds the limit,
    and every email is either processed exactly once or refused, never both."""
    async def run():
        limit = 3
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=limit)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        ids = [f"burst-{i}" for i in range(12)]
        for rid in ids:
            await _send_email(mgr, "alice", rid, _email_params(rid))
        max_depth = 0
        for _ in range(200):
            uq = mgr._user_queues.get("alice")
            if uq is not None:
                max_depth = max(max_depth, uq.queue.qsize())
            await asyncio.sleep(0)
        assert max_depth <= limit, f"queue depth reached {max_depth} > limit {limit}"

        proc.release.set()
        await _wait_until(lambda: all(
            rid in proc.request_ids() or _responses_for(ws, rid) for rid in ids))
        await asyncio.sleep(0.05)
        for rid in ids:
            processed = proc.request_ids().count(rid)
            refused = sum(1 for m in _responses_for(ws, rid) if _is_busy_reply(m))
            assert processed + refused == 1, f"{rid}: processed={processed} refused={refused}"
        assert len(proc.calls) >= limit
    asyncio.run(run())


# --- Criterion 2: queued emails processed exactly once, in priority order ---

def test_ac2_queued_emails_processed_exactly_once_in_priority_then_fifo_order(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=4)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _send_email(mgr, "alice", "held", _email_params("held", importance=1))
        assert await _wait_until(lambda: "held" in proc.request_ids())
        sent = {}
        for rid, importance in (("low", 0), ("normal-a", 1), ("high", 2), ("normal-b", 1)):
            sent[rid] = _email_params(rid, importance=importance)
            await _send_email(mgr, "alice", rid, sent[rid])
        await _settle()
        await _send_email(mgr, "alice", "over", _email_params("over", importance=2))
        assert await _wait_until(lambda: len(_responses_for(ws, "over")) > 0), "fifth waiting email was not refused"

        proc.release.set()
        assert await _wait_until(lambda: len(proc.calls) >= 5)
        await asyncio.sleep(0.05)

        assert proc.request_ids() == ["held", "high", "normal-a", "normal-b", "low"]
        for user_id, rid, params in proc.calls:
            assert user_id == "alice"
            if rid in sent:
                assert params == sent[rid], f"{rid}: params changed on the way to the processor"
            assert not _responses_for(ws, rid), f"{rid} got an unexpected reply from the queue layer"
    asyncio.run(run())


def test_ac2_queue_accepts_again_after_it_drains(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        await _send_email(mgr, "alice", "over", _email_params("over"))
        assert await _wait_until(lambda: len(_responses_for(ws, "over")) > 0), "email was not refused"
        proc.release.set()
        assert await _wait_until(lambda: len(proc.calls) >= 2)
        assert await _wait_until(lambda: mgr._user_queues["alice"].queue.qsize() == 0)
        await _settle()
        await _send_email(mgr, "alice", "later", _email_params("later"))
        assert await _wait_until(lambda: "later" in proc.request_ids())
        assert not _responses_for(ws, "later")
    asyncio.run(run())


# --- Criterion 3: per-user limit (design decision D2 a) ---

def test_ac3_limit_is_per_user_other_user_is_accepted(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws_a, ws_b = _mock_ws(), _mock_ws()
        await mgr.connect(ws_a, "alice")
        await mgr.connect(ws_b, "bob")
        await _fill_queue(mgr, proc, "alice", 1, prefix="a")
        await _send_email(mgr, "alice", "a-over", _email_params("a-over"))
        assert await _wait_until(lambda: len(_responses_for(ws_a, "a-over")) > 0), "alice was not refused"

        await _send_email(mgr, "bob", "b-1", _email_params("b-1"))
        assert await _wait_until(lambda: "b-1" in proc.request_ids())
        await _send_email(mgr, "bob", "b-2", _email_params("b-2"))
        await _settle()

        assert not _responses_for(ws_b, "b-1")
        assert not _responses_for(ws_b, "b-2")
        assert mgr._user_queues["bob"].queue.qsize() == 1
        assert _acks_for(ws_b, "b-1") and _acks_for(ws_b, "b-2")
        # Nothing for bob leaked onto alice's socket and vice versa
        assert not any(str(m.get("params", {}).get("request_id", "")).startswith("b-") for m in _sent(ws_a))
        assert not any(str(m.get("params", {}).get("request_id", "")).startswith("a-") for m in _sent(ws_b))
    asyncio.run(run())


# --- Criterion 4: below the limit, behaviour unchanged from main ---

def test_ac4_below_limit_ack_is_unchanged(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=10)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        for rid in ("k-1", "k-2", "k-3"):
            await _send_email(mgr, "alice", rid, _email_params(rid))
        await _settle()
        for rid in ("k-1", "k-2", "k-3"):
            acks = _acks_for(ws, rid)
            assert acks == [{
                "jsonrpc": "2.0", "id": rid,
                "result": {"status": "processing", "message": "Email received, processing..."},
            }]
            assert not _responses_for(ws, rid)
    asyncio.run(run())


def test_ac4_ack_is_sent_before_the_busy_reply(monkeypatch, tmp_path):
    """Design: the ack stays exactly as today, even for an email that is then refused."""
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        await _send_email(mgr, "alice", "over", _email_params("over"))
        assert await _wait_until(lambda: len(_responses_for(ws, "over")) > 0), "email was not refused"
        assert len(_acks_for(ws, "over")) == 1
        msgs = _sent(ws)
        ack_pos = next(i for i, m in enumerate(msgs) if m.get("id") == "over")
        busy_pos = next(i for i, m in enumerate(msgs)
                        if m.get("params", {}).get("request_id") == "over")
        assert ack_pos < busy_pos
    asyncio.run(run())


def test_ac4_below_limit_priority_order_unchanged(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=10)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _send_email(mgr, "alice", "held", _email_params("held"))
        assert await _wait_until(lambda: "held" in proc.request_ids())
        for rid, imp in (("l1", 0), ("n1", 1), ("h1", 2), ("l2", 0), ("h2", 2), ("n2", 1)):
            await _send_email(mgr, "alice", rid, _email_params(rid, importance=imp))
        await _settle()
        proc.release.set()
        assert await _wait_until(lambda: len(proc.calls) >= 7)
        assert proc.request_ids() == ["held", "h1", "h2", "n1", "n2", "l1", "l2"]
        assert not any(_responses_for(ws, rid) for rid in proc.request_ids())
    asyncio.run(run())


async def _store_cached_result(mgr, user_id, conversation_id, request_id="cached-req"):
    response = {
        "request_id": request_id,
        "subject": "Synthetic cached subject",
        "conversation_id": conversation_id,
        "original_message_id": "<synthetic-cached@example.invalid>",
        "response_text": "Synthetic cached answer.",
        "response_html": "<p>Synthetic cached answer.</p>",
    }
    await mgr._pending.save(user_id, request_id, conversation_id, response)
    return response


def test_ac4_duplicate_below_limit_delivers_cached_result(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=10)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        cached = await _store_cached_result(mgr, "alice", "synthetic-dup-conv")
        await _send_email(mgr, "alice", "dup", _email_params("dup", conversation_id="synthetic-dup-conv"))
        assert await _wait_until(lambda: len(_responses_for(ws, "cached-req")) > 0)
        await _settle()
        assert _responses_for(ws, "cached-req")[0]["params"] == cached
        assert "dup" not in proc.request_ids()
        assert mgr._pending.find("alice", "synthetic-dup-conv") is None
    asyncio.run(run())


def test_ac4_duplicate_with_cached_result_is_delivered_even_when_queue_is_full(monkeypatch, tmp_path):
    """Design: the duplicate check runs before the limit check."""
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        cached = await _store_cached_result(mgr, "alice", "synthetic-dup-conv")
        await _send_email(mgr, "alice", "dup", _email_params("dup", conversation_id="synthetic-dup-conv"))
        assert await _wait_until(lambda: len(_responses_for(ws, "cached-req")) > 0)
        await _settle()
        assert _responses_for(ws, "cached-req")[0]["params"] == cached
        assert not _responses_for(ws, "dup"), "duplicate got a busy reply instead of the cached result"
        assert mgr._user_queues["alice"].queue.qsize() == 1
    asyncio.run(run())


def test_ac4_result_for_disconnected_client_is_still_saved_for_later_delivery(monkeypatch, tmp_path):
    """Offline path through the real LoopbackProcessor, AI routing stubbed."""
    async def run():
        import src.config
        pending_dir = tmp_path / "pending"
        monkeypatch.setattr(src.config, "PENDING_RESULTS_DIR", str(pending_dir))
        _set_limit(monkeypatch, 10)
        mgr = _make_manager()
        mgr._processor._route_to_ai_engine = AsyncMock(return_value="Synthetic answer.")
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        mgr.disconnect("alice")
        params = _email_params("off-1")
        params["has_attachments"] = False
        params["attachments"] = []
        await mgr._enqueue_email("alice", "off-1", params, 1)
        target = pending_dir / "arcumai_pending_alice_off-1.json"
        assert await _wait_until(target.exists), "offline result was not stored for later delivery"
        data = json.loads(target.read_text())
        assert data["response"]["request_id"] == "off-1"
        assert data["conversation_id"] == params["conversation_id"]
    asyncio.run(run())


# --- Design D4: refusal while the connection is closed -> drop and log, no disk ---

def test_refusal_without_connection_writes_nothing_to_disk(monkeypatch, tmp_path):
    async def run():
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        mgr.disconnect("alice")
        save_spy = AsyncMock(side_effect=mgr._pending.save)
        mgr._pending.save = save_spy

        await mgr._enqueue_email("alice", "gone", _email_params("gone"), 1)
        await _settle()

        save_spy.assert_not_called()
        pending_dir = tmp_path / "pending"
        files = list(pending_dir.glob("*")) if pending_dir.exists() else []
        assert files == [], f"refused email written to disk: {[f.name for f in files]}"
        assert mgr._user_queues["alice"].queue.qsize() == 1, "refused email was enqueued"
        assert not any(m.get("params", {}).get("request_id") == "gone" for m in _sent(ws))
    asyncio.run(run())


def test_refusal_without_connection_is_logged(monkeypatch, tmp_path):
    async def run():
        import src.bridge.manager as manager_module
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=1)
        await mgr.connect(_mock_ws(), "alice")
        await _fill_queue(mgr, proc, "alice", 1)
        mgr.disconnect("alice")
        handler = _ListHandler()
        manager_module.log.addHandler(handler)
        try:
            await mgr._enqueue_email("alice", "gone-log", _email_params("gone-log"), 1)
            await _settle()
        finally:
            manager_module.log.removeHandler(handler)
        assert mgr._user_queues["alice"].queue.qsize() == 1, "refused email was enqueued"
        assert any("gone-log" in m for m in handler.messages()), (
            f"no log line names the dropped request id: {handler.messages()}")
    asyncio.run(run())


# --- Privacy (issue notes, #39): refusal log has ids, depth, limit; no content ---

def test_refusal_log_has_user_request_depth_limit_and_no_email_content(monkeypatch, tmp_path):
    async def run():
        import src.bridge.manager as manager_module
        limit = 3
        mgr, proc = _queue_manager(monkeypatch, tmp_path, limit=limit)
        ws = _mock_ws()
        await mgr.connect(ws, "alice")
        await _fill_queue(mgr, proc, "alice", limit)

        params = _email_params("canary", subject="CanarySubjectZq81 synthetic")
        params["body"] = "CanaryBodyXv42 synthetic body"
        params["attachments"] = [{"file_name": "CanaryFileWm73.pdf", "content_base64": _SYNTHETIC_ATTACHMENT}]

        handler = _ListHandler()
        manager_module.log.addHandler(handler)
        try:
            await mgr._enqueue_email("alice", "req-canary-x", params, 1)
            await _settle()
        finally:
            manager_module.log.removeHandler(handler)

        assert _responses_for(ws, "req-canary-x"), "email was not refused"
        msgs = handler.messages()
        joined = "\n".join(msgs)
        for canary in ("CanarySubjectZq81", "CanaryBodyXv42", "CanaryFileWm73", _SYNTHETIC_ATTACHMENT[:40]):
            assert canary not in joined, f"refusal log leaks email content ({canary!r}): {msgs}"
        refusal = [m for m in msgs if "req-canary-x" in m]
        assert refusal, f"no refusal log line with the request id: {msgs}"
        line = refusal[0]
        assert "alice" in line, f"refusal log lacks the user id: {line!r}"
        assert str(limit) in line, f"refusal log lacks depth/limit {limit}: {line!r}"
    asyncio.run(run())

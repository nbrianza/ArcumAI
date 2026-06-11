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

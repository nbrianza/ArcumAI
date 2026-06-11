# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
from src.bridge.pending_results import PendingResultStore


def test_save_creates_file(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req1", "conv1", {"text": "hello"}))
    assert len(list(tmp_path.glob("arcumai_pending_alice_*.json"))) == 1


def test_find_returns_matching_result(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req2", "conv2", {"text": "result"}))
    found = store.find("alice", "conv2")
    assert found is not None
    assert found["response"] == {"text": "result"}
    assert found["user_id"] == "alice"
    assert found["request_id"] == "req2"


def test_find_returns_none_for_unknown_user(tmp_path):
    store = PendingResultStore(tmp_path)
    assert store.find("nobody", "conv-x") is None


def test_find_returns_none_for_unknown_conversation(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req3", "conv3", {"text": "data"}))
    assert store.find("alice", "conv-nonexistent") is None


def test_delete_removes_file(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req4", "conv4", {"text": "bye"}))
    store.delete("alice", "conv4")
    assert len(list(tmp_path.glob("arcumai_pending_alice_*.json"))) == 0


def test_find_returns_none_after_delete(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req5", "conv5", {"text": "temp"}))
    store.delete("alice", "conv5")
    assert store.find("alice", "conv5") is None


def test_save_different_users_no_collision(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("alice", "req6", "conv6", {"text": "alice-data"}))
    asyncio.run(store.save("bob",   "req7", "conv7", {"text": "bob-data"}))
    assert store.find("alice", "conv6")["response"]["text"] == "alice-data"
    assert store.find("bob",   "conv7")["response"]["text"] == "bob-data"
    # Cross-user lookup should be None
    assert store.find("alice", "conv7") is None
    assert store.find("bob",   "conv6") is None


def test_save_payload_includes_required_fields(tmp_path):
    store = PendingResultStore(tmp_path)
    asyncio.run(store.save("carol", "req8", "conv8", {"answer": 42}))
    found = store.find("carol", "conv8")
    assert "user_id"         in found
    assert "request_id"      in found
    assert "conversation_id" in found
    assert "created_at"      in found
    assert "response"        in found

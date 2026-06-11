# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.


def test_create_conversation_returns_string_id(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("alice")
    assert isinstance(conv_id, str) and conv_id


def test_load_conversation_returns_empty_messages(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("alice")
    data = store.load_conversation("alice", conv_id)
    assert data is not None
    assert data["id"] == conv_id
    assert data["messages"] == []


def test_load_conversation_returns_none_for_missing(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    assert store.load_conversation("alice", "nonexistent-id") is None


def test_append_message_persists_to_disk(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("bob")
    store.append_message("bob", conv_id, "user", "Hello!")
    data = store.load_conversation("bob", conv_id)
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello!"
    assert "timestamp" in data["messages"][0]


def test_append_message_auto_titles_from_first_user_message(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("bob")
    store.append_message("bob", conv_id, "user", "What is the contract deadline?")
    assert store.load_conversation("bob", conv_id)["title"] == "What is the contract deadline?"


def test_append_message_title_not_overwritten_by_assistant(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("bob")
    store.append_message("bob", conv_id, "user", "First user message")
    store.append_message("bob", conv_id, "assistant", "Here is my answer")
    assert store.load_conversation("bob", conv_id)["title"] == "First user message"


def test_multiple_messages_accumulate(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    conv_id = store.create_conversation("carol")
    for i in range(4):
        role = "user" if i % 2 == 0 else "assistant"
        store.append_message("carol", conv_id, role, f"Message {i}")
    data = store.load_conversation("carol", conv_id)
    assert len(data["messages"]) == 4


def test_list_conversations_returns_summaries(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("dave")
    store.append_message("dave", cid, "user", "Hi")
    convs = store.list_conversations("dave")
    assert len(convs) == 1
    assert convs[0]["id"] == cid
    assert convs[0]["message_count"] == 1
    assert "title" in convs[0] and "created_at" in convs[0]


def test_list_conversations_empty_for_unknown_user(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    assert store.list_conversations("nobody") == []


def test_delete_conversation_removes_it(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("eve")
    assert store.delete_conversation("eve", cid) is True
    assert store.load_conversation("eve", cid) is None


def test_delete_nonexistent_conversation_returns_false(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    assert store.delete_conversation("eve", "ghost-id") is False


def test_cleanup_empty_removes_zero_message_conversations(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("frank")
    removed = store.cleanup_empty("frank")
    assert removed == 1
    assert store.load_conversation("frank", cid) is None


def test_cleanup_empty_preserves_conversations_with_messages(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("grace")
    store.append_message("grace", cid, "user", "Keep me")
    removed = store.cleanup_empty("grace")
    assert removed == 0
    assert store.load_conversation("grace", cid) is not None


def test_users_are_isolated(tmp_path):
    from src.conversations import ConversationStore
    store = ConversationStore(tmp_path)
    cid_a = store.create_conversation("user_a")
    store.append_message("user_a", cid_a, "user", "user A message")
    assert store.list_conversations("user_b") == []
    assert store.load_conversation("user_b", cid_a) is None

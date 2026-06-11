# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src.logger import server_log as log


class ConversationStore:
    """Persists chat conversations per user as JSON files.

    Layout:
        storage/conversations/<username>/2026-03-25_10-00-00.json

    Each file represents one conversation:
        {
            "id": "2026-03-25_10-00-00",
            "created_at": "2026-03-25T10:00:00+00:00",
            "title": "first user message preview...",
            "messages": [
                {"role": "user",      "content": "...", "timestamp": "..."},
                {"role": "assistant", "content": "...", "timestamp": "..."},
            ]
        }
    """

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            from src.config import BASE_DIR
            base_dir = BASE_DIR / "storage" / "conversations"
        self._base_dir = base_dir

    # --- helpers ---

    def _user_dir(self, username: str) -> Path:
        safe_name = username.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self._base_dir / safe_name

    @staticmethod
    def _new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")

    def _conv_path(self, username: str, conv_id: str) -> Path:
        return self._user_dir(username) / f"{conv_id}.json"

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not read conversation {path.name}: {e}")
            return None

    def _write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # atomic-ish replace (safe on Windows NTFS)
        if path.exists():
            path.unlink()
        tmp.rename(path)

    # --- public API ---

    def list_conversations(self, username: str) -> list[dict]:
        """Return a list of conversation summaries sorted newest-first.

        Each item: {"id": ..., "created_at": ..., "title": ..., "message_count": ...}
        """
        user_dir = self._user_dir(username)
        if not user_dir.exists():
            return []

        convs = []
        for p in sorted(user_dir.glob("*.json"), reverse=True):
            data = self._read_json(p)
            if data:
                convs.append({
                    "id": data.get("id", p.stem),
                    "created_at": data.get("created_at", ""),
                    "title": data.get("title", "(no title)"),
                    "message_count": len(data.get("messages", [])),
                })
        return convs

    def create_conversation(self, username: str) -> str:
        """Create a new empty conversation. Returns the conv_id."""
        conv_id = self._new_id()
        data = {
            "id": conv_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "title": "",
            "messages": [],
        }
        self._write_json(self._conv_path(username, conv_id), data)
        log.info(f"[{username}] New conversation created: {conv_id}")
        return conv_id

    def load_conversation(self, username: str, conv_id: str) -> dict | None:
        """Load a full conversation (with messages)."""
        path = self._conv_path(username, conv_id)
        if not path.exists():
            return None
        return self._read_json(path)

    def append_message(self, username: str, conv_id: str,
                       role: str, content: str) -> None:
        """Append a single message to a conversation and save."""
        path = self._conv_path(username, conv_id)
        data = self._read_json(path)
        if data is None:
            log.warning(f"[{username}] Cannot append to missing conversation {conv_id}")
            return

        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data["messages"].append(msg)

        # Auto-title from first user message
        if not data["title"] and role == "user":
            data["title"] = content[:80]

        self._write_json(path, data)

    def cleanup_empty(self, username: str | None = None) -> int:
        """Delete conversation files that have zero messages.

        If username is given, clean only that user's folder.
        If None, clean all users.  Returns the number of files removed.
        """
        removed = 0
        if username:
            dirs = [self._user_dir(username)]
        else:
            if not self._base_dir.exists():
                return 0
            dirs = [d for d in self._base_dir.iterdir() if d.is_dir()]

        for user_dir in dirs:
            if not user_dir.exists():
                continue
            for p in list(user_dir.glob("*.json")):
                data = self._read_json(p)
                if data and not data.get("messages"):
                    p.unlink()
                    removed += 1
        if removed:
            log.info(f"Conversation cleanup: removed {removed} empty file(s)"
                     + (f" for '{username}'" if username else ""))
        return removed

    def delete_conversation(self, username: str, conv_id: str) -> bool:
        """Delete a conversation file."""
        path = self._conv_path(username, conv_id)
        if path.exists():
            path.unlink()
            log.info(f"[{username}] Conversation deleted: {conv_id}")
            return True
        return False

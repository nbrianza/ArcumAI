from __future__ import annotations

import glob
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from src.logger import server_log as log


class PendingResultStore:
    """Persists completed AI results to disk for delivery when a client reconnects."""

    def __init__(self, temp_dir: Path):
        self._temp_dir = temp_dir

    async def save(self, user_id: str, request_id: str,
                   conversation_id: str, response: dict):
        """Store a completed result to disk for delivery on next reconnect."""
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            path = self._temp_dir / f"arcumai_pending_{user_id}_{request_id}.json"
            payload = {
                "user_id":         user_id,
                "request_id":      request_id,
                "conversation_id": conversation_id,
                "created_at":      datetime.now(timezone.utc).isoformat(),
                "response":        response,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            log.info(f"Pending result saved for '{user_id}': {path.name}")
        except Exception as e:
            log.error(f"Failed to save pending result for '{user_id}': {e}")

    def find(self, user_id: str, conversation_id: str) -> dict | None:
        """Look up a cached result by user + conversation_id."""
        pattern = str(self._temp_dir / f"arcumai_pending_{user_id}_*.json")
        for p in glob.glob(pattern):
            try:
                data = json.loads(Path(p).read_text())
                if data.get("conversation_id") == conversation_id:
                    return data
            except Exception:
                pass
        return None

    def delete(self, user_id: str, conversation_id: str):
        """Delete a temp file after it has been delivered."""
        pattern = str(self._temp_dir / f"arcumai_pending_{user_id}_*.json")
        for p in glob.glob(pattern):
            try:
                data = json.loads(Path(p).read_text())
                if data.get("conversation_id") == conversation_id:
                    Path(p).unlink()
            except Exception:
                pass

    async def deliver(self, user_id: str, active_connections: dict):
        """Called after a client reconnects and completes the identify handshake.
        Delivers stored results in order, skipping those older than the TTL.

        Race-condition safety (Issue 5): before each await, the .json file is atomically
        renamed to .delivering so _enqueue_email's dedup scan (which only globs *.json)
        cannot find it and deliver it a second time.

        Stale-ws safety (Issue 8): ws is re-fetched on every iteration, so a disconnect
        mid-loop is detected immediately and remaining files are left on disk for next reconnect.
        """
        from src.config import PENDING_RESULT_TTL_HOURS

        # Recover any .delivering files left by a previous interrupted delivery
        # (e.g. server crashed between rename and unlink).
        for p in glob.glob(str(self._temp_dir / f"arcumai_pending_{user_id}_*.delivering")):
            try:
                Path(p).rename(Path(p).with_suffix(".json"))
            except Exception:
                pass

        pattern = str(self._temp_dir / f"arcumai_pending_{user_id}_*.json")
        files = sorted(glob.glob(pattern))
        if not files:
            return

        delivered = expired = 0
        for p in files:
            try:
                data = json.loads(Path(p).read_text())
                age_h = (datetime.now(timezone.utc) -
                         datetime.fromisoformat(data["created_at"])).total_seconds() / 3600
                if age_h > PENDING_RESULT_TTL_HOURS:
                    Path(p).unlink()
                    expired += 1
                    log.warning(f"Pending result expired ({age_h:.1f}h > {PENDING_RESULT_TTL_HOURS}h): {Path(p).name}")
                    continue

                # Atomically claim the file before awaiting to prevent race with _enqueue_email
                delivering_path = Path(p).with_suffix(".delivering")
                try:
                    Path(p).rename(delivering_path)
                except (FileNotFoundError, OSError):
                    continue  # already claimed or deleted by another coroutine

                # Re-fetch ws on every iteration (Issue 8: avoid stale reference after await)
                ws = active_connections.get(user_id)
                if ws:
                    push = {"jsonrpc": "2.0", "method": "virtual_loopback/response",
                            "params": data["response"]}
                    await ws.send_text(json.dumps(push))
                    try:
                        delivering_path.unlink()
                    except FileNotFoundError:
                        pass
                    delivered += 1
                else:
                    # Client disconnected mid-delivery: rename back for next reconnect attempt
                    try:
                        delivering_path.rename(Path(p))
                    except Exception:
                        pass
                    log.info(f"Pending delivery for '{user_id}' paused: client disconnected")
                    break
            except Exception as e:
                log.error(f"Failed to deliver pending result {p}: {e}")
        if delivered or expired:
            log.info(f"Pending results for '{user_id}': {delivered} delivered, {expired} expired/deleted")

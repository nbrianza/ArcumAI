# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Issue #54: a stopped queue worker must be restarted even when the queue is full.

Before the fix, the refusal ran before the worker restart check, so a full queue
whose worker had ended refused every new email and was never drained.
All data is synthetic.
"""
import asyncio
import json
from unittest.mock import AsyncMock


class _BlockingProcessor:
    def __init__(self):
        self.calls = []
        self.release = asyncio.Event()

    async def __call__(self, user_id, request_id, params):
        self.calls.append(request_id)
        await self.release.wait()


def _params(n):
    return {
        "subject": f"Synthetic subject {n}",
        "body": f"Synthetic body {n}.",
        "conversation_id": f"synthetic-conv-{n}",
        "original_message_id": f"<synthetic-{n}@example.invalid>",
        "importance": 1,
        "has_attachments": False,
        "attachments": [],
    }


async def _wait_until(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


def test_stopped_worker_is_restarted_when_queue_is_full(monkeypatch, tmp_path):
    async def run():
        import src.config
        monkeypatch.setattr(src.config, "PENDING_RESULTS_DIR", str(tmp_path / "pending"))
        monkeypatch.setattr(src.config, "LOOPBACK_MAX_QUEUED_PER_USER", 2)
        from src.bridge.manager import OutlookBridgeManager
        mgr = OutlookBridgeManager()
        proc = _BlockingProcessor()
        mgr._process_loopback_email = proc
        ws = AsyncMock()
        await mgr.connect(ws, "alice")

        await mgr._enqueue_email("alice", "r0", _params("r0"), 1)
        assert await _wait_until(lambda: "r0" in proc.calls)
        for rid in ("r1", "r2", "r3"):
            await mgr._enqueue_email("alice", rid, _params(rid), 1)
        uq = mgr._user_queues["alice"]
        assert uq.queue.qsize() == 2  # r3 refused

        # The worker ends unexpectedly while the queue is full.
        uq.worker_task.cancel()
        await _wait_until(lambda: uq.worker_task.done())
        proc.release.set()

        # The next email must bring the worker back, even if it is refused itself.
        await mgr._enqueue_email("alice", "r4", _params("r4"), 1)
        assert not uq.worker_task.done(), "worker was not restarted while the queue was full"
        assert await _wait_until(lambda: "r1" in proc.calls and "r2" in proc.calls), (
            f"waiting emails were never processed: {proc.calls}")
        assert await _wait_until(lambda: uq.queue.qsize() == 0)

        for rid in ("r5", "r6"):
            await mgr._enqueue_email("alice", rid, _params(rid), 1)
        assert await _wait_until(lambda: "r5" in proc.calls and "r6" in proc.calls)

        sent = [json.loads(c.args[0]) for c in ws.send_text.call_args_list]
        refused = {m["params"]["request_id"] for m in sent
                   if m.get("method") == "virtual_loopback/response"}
        assert "r5" not in refused and "r6" not in refused
        assert proc.calls.count("r1") == 1 and proc.calls.count("r2") == 1
    asyncio.run(run())

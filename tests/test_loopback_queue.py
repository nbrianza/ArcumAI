# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import asyncio
from src.bridge.loopback_queue import _EmailTask, _UserQueue


def test_email_task_high_priority_less_than_normal():
    high   = _EmailTask(priority=0, sequence=0, user_id="u", request_id="r1", params={})
    normal = _EmailTask(priority=1, sequence=0, user_id="u", request_id="r2", params={})
    assert high < normal


def test_email_task_fifo_tiebreaker_for_equal_priority():
    first  = _EmailTask(priority=1, sequence=0, user_id="u", request_id="r1", params={})
    second = _EmailTask(priority=1, sequence=1, user_id="u", request_id="r2", params={})
    assert first < second


def test_email_task_high_priority_beats_earlier_sequence():
    high_late  = _EmailTask(priority=0, sequence=99, user_id="u", request_id="r1", params={})
    low_early  = _EmailTask(priority=1, sequence=0,  user_id="u", request_id="r2", params={})
    assert high_late < low_early


def test_user_queue_initializes_empty():
    q = _UserQueue("alice")
    assert q.queue.empty()
    assert q.worker_task is None
    assert q.sequence == 0
    assert q.user_id == "alice"


def test_user_queue_enqueue_dequeue():
    async def run():
        q = _UserQueue("bob")
        task = _EmailTask(priority=1, sequence=0, user_id="bob", request_id="req1", params={"k": "v"})
        await q.queue.put(task)
        return await q.queue.get()

    result = asyncio.run(run())
    assert result.request_id == "req1"
    assert result.params == {"k": "v"}


def test_user_queue_respects_priority_order():
    async def run():
        q = _UserQueue("carol")
        normal = _EmailTask(priority=1, sequence=1, user_id="carol", request_id="r_normal", params={})
        high   = _EmailTask(priority=0, sequence=2, user_id="carol", request_id="r_high",   params={})
        await q.queue.put(normal)
        await q.queue.put(high)
        first  = await q.queue.get()
        second = await q.queue.get()
        return first.request_id, second.request_id

    first_id, second_id = asyncio.run(run())
    assert first_id == "r_high"
    assert second_id == "r_normal"


def test_user_queue_fifo_within_same_priority():
    async def run():
        q = _UserQueue("dave")
        t1 = _EmailTask(priority=1, sequence=0, user_id="dave", request_id="first",  params={})
        t2 = _EmailTask(priority=1, sequence=1, user_id="dave", request_id="second", params={})
        t3 = _EmailTask(priority=1, sequence=2, user_id="dave", request_id="third",  params={})
        for t in (t1, t2, t3):
            await q.queue.put(t)
        ids = [( await q.queue.get()).request_id for _ in range(3)]
        return ids

    ids = asyncio.run(run())
    assert ids == ["first", "second", "third"]

# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass(order=True)
class _EmailTask:
    priority:   int          # 0=High, 1=Normal, 2=Low (lower = processed first)
    sequence:   int          # FIFO tiebreaker for equal priority
    user_id:    str  = field(compare=False)
    request_id: str  = field(compare=False)
    params:     dict = field(compare=False)


class _UserQueue:
    def __init__(self, user_id: str):
        self.user_id     = user_id
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.sequence: int = 0
        self.worker_task: asyncio.Task | None = None

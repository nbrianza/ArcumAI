# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""The ingestion lock: proof of life is a lock held by the kernel (ADR 0005, #78).

The holder keeps the lock file open with an exclusive byte-range lock for the
whole run, and the kernel drops that lock when the process ends for any reason.
So a file with no holder is stale, whatever it contains, and is taken over
without being deleted. The content is informational and is never parsed.

Other platforms keep exclusive create with no recovery (D6).

Standard library only and no logging: callers write the log lines, and child
processes that only need the lock start fast.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None

# Past the content, so the PID text stays readable while the byte is locked.
_LOCK_OFFSET = 1024 * 1024
_ACQUIRE_ATTEMPTS = 3
_ACQUIRE_DELAY_SEC = 0.2
_REMOVE_ATTEMPTS = 3
_REMOVE_DELAY_SEC = 0.1
# O_NOINHERIT on purpose: a child of the holder (for example tesseract) that
# outlives it must not keep the lock file open.
_OPEN_FLAGS = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)


@dataclass
class HeldLock:
    fd: int
    path: Path
    stale_age_sec: int | None  # None: the file did not exist before


def _try_once(path: Path) -> HeldLock | None:
    """One attempt. None means the lock is taken; OSError means try again or give up."""
    existed = False
    try:
        fd = os.open(path, _OPEN_FLAGS | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if msvcrt is None:
            return None
        existed = True
        fd = os.open(path, _OPEN_FLAGS)

    locked = False
    try:
        if msvcrt is not None:
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                return None
        locked = True
        age = max(0, int(time.time() - os.fstat(fd).st_mtime)) if existed else None
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"LOCKED by PID {os.getpid()}".encode("ascii"))
        except OSError:
            pass  # the content is informational; the kernel lock is the proof
        return HeldLock(fd=fd, path=path, stale_age_sec=age)
    finally:
        if not locked:
            os.close(fd)


def try_acquire(path: Path) -> HeldLock | None:
    """Take the lock, recovering a file with no holder. None means refused.

    Retries because the kernel can take a moment to drop the locks of an ended
    process, and so that a momentary is_held probe or scanner cannot refuse a run.
    A real refusal costs about 0.4 s. Fails closed on any OSError.
    """
    for attempt in range(_ACQUIRE_ATTEMPTS):
        if attempt:
            time.sleep(_ACQUIRE_DELAY_SEC)
        try:
            held = _try_once(path)
        except (FileNotFoundError, PermissionError):
            continue  # the owner just finished, a delete is pending, or a scanner
        except OSError:
            return None
        if held is not None:
            return held
        if msvcrt is None:
            return None
    return None


def is_held(path: Path) -> bool:
    """Advisory probe; never creates or removes anything.

    False when it cannot decide, because the authoritative decision is made by
    try_acquire in the process that runs the ingestion.
    """
    if msvcrt is None:
        return path.exists()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0))
    except OSError:
        return False
    try:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return False
    finally:
        os.close(fd)


def release(held: HeldLock) -> str | None:
    """Drop the lock and remove the file. Returns the exception class name if not removed.

    The own handle must be closed before the removal. If another run takes the lock
    in that gap, its open handle makes the removal fail and that run keeps its lock.
    """
    try:
        if msvcrt is not None:
            os.lseek(held.fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(held.fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass  # closing the handle below drops the lock as well
    finally:
        os.close(held.fd)

    for attempt in range(_REMOVE_ATTEMPTS):
        if attempt:
            time.sleep(_REMOVE_DELAY_SEC)
        try:
            os.unlink(held.path)
            return None
        except FileNotFoundError:
            return None
        except PermissionError as e:
            reason = type(e).__name__
        except OSError as e:
            return type(e).__name__
    return reason

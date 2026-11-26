"""过滤器变更与事件入队共用的互斥闸。

进程内用 :class:`threading.RLock` 消除同进程争用；进程外用 ``fcntl.flock``
钉在当前 SQLite 数据目录的锁文件上。进入闸后再开 ``BEGIN IMMEDIATE``，
因此两路并发脚本/线程不可能在同一指纹版本上各说各话。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from app import db

try:  # Linux 交付/验收环境支持文件锁；非 POSIX 仅保留进程内锁。
    import fcntl
except ImportError:  # pragma: no cover - Windows 兜底
    fcntl = None


_THREAD_LOCK = threading.RLock()


@contextmanager
def 过滤入队互斥():
    _THREAD_LOCK.acquire()
    handle = None
    try:
        lock_path = Path(db.库路径).resolve().parent / ".hbg_filt_enqueue.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            handle = lock_path.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if handle is not None:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
        finally:
            _THREAD_LOCK.release()

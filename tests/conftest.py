"""Redirect pytest's tmp_path base dir to tmpfs (/dev/shm) instead of real
disk (/tmp is ext4 on this VM, confirmed via `df -T /tmp`). This repo's
test suite creates real sqlite3 files under tmp_path across dozens of
test files (trader_db.py, bankroll.py, executor.py, discovery_db.py,
...) -- real disk I/O contention on this shared VM (other OpenClaw agents
+ periodic backups) has repeatedly made full-suite runs take 3-7+ minutes
this session. /dev/shm is RAM-backed, no disk I/O at all.

Falls back silently to pytest's default if /dev/shm doesn't exist or
isn't writable (e.g. a different host/CI environment) -- never breaks the
suite, only speeds it up when the fast path is available.
"""
import os
from pathlib import Path


def pytest_configure(config):
    shm = Path("/dev/shm")
    if shm.is_dir() and os.access(shm, os.W_OK):
        config.option.basetemp = shm / "pytest-trader-stonks"

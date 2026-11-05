"""Windows lock contention must have the same waiting semantics as flock."""
from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hooks.evidence_kernel import FileLock


class FileLockTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "run.lock"

    @contextmanager
    def windows(self, effects):
        crt = SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0, locking=Mock(side_effect=effects))
        with patch.dict(sys.modules, {"msvcrt": crt}), \
                patch("hooks.evidence_kernel.os", SimpleNamespace(name="nt", SEEK_END=os.SEEK_END)), \
                patch("hooks.evidence_kernel.time.sleep") as sleep:
            yield crt, sleep

    def test_default_wait_survives_repeated_windows_contention(self):
        effects = [OSError(errno.EACCES, "locked")] * 12 + [None, None]
        with self.windows(effects) as (crt, sleep):
            lock = FileLock(self.path)
            with lock:
                self.assertIsNotNone(lock.handle)
            self.assertIsNone(lock.handle)
        self.assertEqual([2] * 13 + [0], [call.args[1] for call in crt.locking.call_args_list])
        self.assertEqual(12, sleep.call_count)

    def test_windows_deadlock_error_is_retryable_contention(self):
        with self.windows([OSError(errno.EDEADLK, "Resource deadlock avoided"), None, None]) as (crt, sleep):
            with FileLock(self.path):
                pass
        self.assertEqual(3, crt.locking.call_count)
        sleep.assert_called_once_with(0.05)

    def test_bounded_wait_can_acquire_after_contention(self):
        with self.windows([OSError(errno.EACCES, "locked"), None, None]):
            with FileLock(self.path, timeout=1):
                pass

    def test_deadline_returns_timeout_and_closes_the_handle(self):
        with self.windows([OSError(errno.EACCES, "locked")]) as (crt, sleep), \
                patch("hooks.evidence_kernel.time.monotonic", side_effect=[0, 2]):
            lock = FileLock(self.path, timeout=1)
            with self.assertRaisesRegex(TimeoutError, "file_lock_timeout:"):
                lock.__enter__()
            self.assertIsNone(lock.handle)
            sleep.assert_not_called()
        self.assertEqual(1, crt.locking.call_count)

    def test_nonblocking_wait_does_not_sleep(self):
        with self.windows([OSError(errno.EACCES, "locked")]) as (_, sleep):
            with self.assertRaises(TimeoutError):
                FileLock(self.path, timeout=0).__enter__()
            sleep.assert_not_called()

    def test_file_errors_are_never_retried(self):
        for timeout in (None, 1):
            for error_number in (errno.EBADF, errno.EINVAL, errno.EIO):
                with self.subTest(timeout=timeout, error_number=error_number), \
                        self.windows([OSError(error_number, "file error")]) as (crt, sleep):
                    lock = FileLock(self.path, timeout=timeout)
                    with self.assertRaises(OSError) as error:
                        lock.__enter__()
                    self.assertEqual(error_number, error.exception.errno)
                    self.assertIsNone(lock.handle)
                    self.assertEqual(1, crt.locking.call_count)
                    sleep.assert_not_called()

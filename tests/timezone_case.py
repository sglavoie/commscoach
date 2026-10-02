"""Temporarily select the system timezone, restoring it even on failure."""

import time
from contextlib import contextmanager
from unittest import mock


@contextmanager
def local_timezone(name: str):
    try:
        with mock.patch.dict("os.environ", {"TZ": name}):
            time.tzset()
            yield
    finally:
        time.tzset()

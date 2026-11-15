"""Режим фонового запуска не даёт разрешений на интерактивные действия."""

from contextlib import contextmanager
from contextvars import ContextVar

_unattended = ContextVar("patcher_unattended", default=False)


def is_unattended():
    return _unattended.get()


@contextmanager
def unattended():
    token = _unattended.set(True)
    try:
        yield
    finally:
        _unattended.reset(token)

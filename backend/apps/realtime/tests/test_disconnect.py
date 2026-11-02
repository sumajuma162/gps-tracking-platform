"""A browser closing the diagnostics socket mid-send ends the watcher quietly (seen in the live browser run as
'Task exception was never retrieved ... ClientDisconnected')."""
from __future__ import annotations

import asyncio

import pytest

from apps.realtime.consumers import DeviceDiagnosticsConsumer


class ClientDisconnected(OSError):  # same name as uvicorn's
    pass


@pytest.mark.parametrize("exc", [ClientDisconnected(), ConnectionResetError(), asyncio.CancelledError()])
def test_watcher_ends_quietly_on_disconnect(exc):
    consumer = DeviceDiagnosticsConsumer()

    async def boom(include_raw: bool) -> None:
        raise exc
    consumer._watch_loop = boom  # type: ignore[method-assign]
    assert asyncio.run(consumer._watch(False)) is None


def test_real_errors_still_surface():
    consumer = DeviceDiagnosticsConsumer()

    async def boom(include_raw: bool) -> None:
        raise ValueError("bug")
    consumer._watch_loop = boom  # type: ignore[method-assign]
    with pytest.raises(ValueError):
        asyncio.run(consumer._watch(False))

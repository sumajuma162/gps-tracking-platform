"""Gateway process entrypoint."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from gpsgw.app import Gateway, StartupError
from gpsgw.config import GatewayConfig

log = logging.getLogger("gpsgw")


async def run(cfg: GatewayConfig) -> None:
    gateway = Gateway(cfg)
    await gateway.start()
    log.info("gateway ready", extra=gateway.snapshot())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    await stop.wait()
    await gateway.stop()


def main() -> None:
    from gpsgw.jsonlog import configure

    configure()
    if os.environ.get("SENTRY_DSN"):  # spec A15: optional error reporting; no personal data
        import sentry_sdk

        sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                        send_default_pii=False)
    try:
        asyncio.run(run(GatewayConfig.from_env()))
    except StartupError as exc:
        log.critical(str(exc))
        sys.exit(2)


if __name__ == "__main__":
    main()

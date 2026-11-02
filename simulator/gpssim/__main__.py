"""Command line: python -m gpssim --scenario normal_day [--host gateway --port 5027 --speedup 10]."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from gpssim.scenarios import SCENARIOS, run_fleet


def main() -> None:
    p = argparse.ArgumentParser(prog="gpssim", description="Simulated Teltonika trackers (real protocol over the "
                                "network). By default drives the three demo trackers created by seed_demo.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5027)
    p.add_argument("--transport", choices=["tcp", "udp"], default="tcp")
    p.add_argument("--protocol", choices=["teltonika", "gt06", "http"], default="teltonika",
                   help="gt06: GT06 over TCP (port 5023); http: generic JSON over HTTP (port 5055)")
    p.add_argument("--token", default="", help="HTTP trackers: the device token from the tracker's page")
    p.add_argument("--path", default="/fleet-json", help="HTTP trackers: the format's path")
    p.add_argument("--scenario", choices=SCENARIOS, default="normal_day")
    p.add_argument("--imei", action="append", help="tracker IMEI (repeat for several); default: demo fleet")
    p.add_argument("--steps", type=int, default=120, help="records per tracker")
    p.add_argument("--interval", type=float, default=10.0, help="seconds of device time between records")
    p.add_argument("--speedup", type=float, default=1.0,
                   help="time acceleration; 0 sends as fast as possible (backfill)")
    p.add_argument("--backfill-hours", type=float, default=0.0,
                   help="start this many hours in the past and send as fast as possible")
    p.add_argument("--gap-minutes", type=float, default=120.0, help="coverage gap length for coverage_gap_backlog")
    p.add_argument("--hold", type=float, default=0.0,
                   help="keep the connection open this many seconds afterwards, answering Codec 12 commands")
    a = p.parse_args()
    start = None
    speedup = a.speedup
    if a.backfill_hours:
        start = datetime.now(UTC) - timedelta(hours=a.backfill_hours)
        a.steps = int(a.backfill_hours * 3600 / a.interval)
        speedup = 0.0
    results = asyncio.run(run_fleet(a.host, a.port, a.scenario, a.imei, steps=a.steps, interval_s=a.interval,
                                    speedup=speedup, start=start, transport=a.transport,
                                    gap_minutes=a.gap_minutes, hold_seconds=a.hold, protocol=a.protocol,
                                    token=a.token, path=a.path))
    print(json.dumps([r.as_dict() for r in results], indent=2))


if __name__ == "__main__":
    main()

"""Blocking Teltonika tracker client for browser tests (exact IO values, long-held connections), plus a runner
for the real simulator CLI. Uses the gateway's own encoder, so packets are byte-identical to the protocol."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "gateway"), str(ROOT / "simulator")]

from gpsgw.protocols.teltonika.encoder import (  # noqa: E402
    AvlRecordSpec,
    encode_imei,
    encode_tcp_packet,
)

HOST = os.environ.get("E2E_TRACKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("E2E_TRACKER_PORT", "5027"))


class Tracker:
    def __init__(self, imei: str, timeout: float = 10.0) -> None:
        self.sock = socket.create_connection((HOST, PORT), timeout)
        self.sock.sendall(encode_imei(imei))
        self.accepted = self.sock.recv(1) == b"\x01"

    def send(self, io: dict[int, tuple[int, int]] | None = None, satellites: int = 11, speed: int = 35,
             latitude: float = -6.8161, longitude: float = 39.2804, when: datetime | None = None) -> int:
        rec = AvlRecordSpec(time=when or datetime.now(UTC) - timedelta(seconds=1), latitude=latitude,
                            longitude=longitude, satellites=satellites, speed_kmh=speed, heading=90,
                            io=io or {239: (1, 1), 240: (1, 1), 66: (2, 27650), 21: (1, 4)})
        self.sock.sendall(encode_tcp_packet([rec]))
        return int.from_bytes(self.sock.recv(4), "big")

    def closed_by_server(self, within: float) -> bool:
        self.sock.settimeout(within)
        try:
            return self.sock.recv(1) == b""
        except TimeoutError:
            return False

    def close(self) -> None:
        self.sock.close()


def run_simulator(*args: str) -> list[dict[str, object]]:
    """Run ``python -m gpssim`` (the real simulator) and return its JSON summary."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "simulator"), str(ROOT / "gateway")])}
    out = subprocess.run([sys.executable, "-m", "gpssim", "--host", HOST, "--port", str(PORT), "--speedup", "0", *args],
                         capture_output=True, text=True, timeout=120, env=env, check=True)
    return json.loads(out.stdout)  # type: ignore[no-any-return]

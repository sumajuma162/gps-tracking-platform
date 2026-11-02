"""Gateway configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote


def _database_url_from_env() -> str:
    if url := os.environ.get("DATABASE_URL", ""):
        return url
    user = os.environ.get("DATABASE_USER", "")
    password = os.environ.get("DATABASE_PASSWORD", "")
    host = os.environ.get("DATABASE_HOST", "postgres")
    port = os.environ.get("DATABASE_PORT", "5432")
    name = os.environ.get("DATABASE_NAME", "")
    if not user or not name:
        return ""
    return f"postgresql://{quote(user)}:{quote(password)}@{host}:{port}/{quote(name)}"


def _redis_url_from_env() -> str:
    if url := os.environ.get("REDIS_URL", ""):
        return url
    password = os.environ.get("REDIS_PASSWORD", "")
    host = os.environ.get("REDIS_HOST", "redis")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")
    auth = f":{quote(password)}@" if password else ""
    return f"redis://{auth}{host}:{port}/{db}"


@dataclass(frozen=True)
class GatewayConfig:
    teltonika_tcp_port: int
    teltonika_udp_port: int
    health_port: int
    storage: str
    allow_memory_storage: bool
    dev_devices: tuple[str, ...]
    database_url: str = ""
    redis_url: str = ""
    manual_ingest_token: str = ""
    listen_host: str = "0.0.0.0"
    reconcile_interval: float = 10.0
    max_connections_per_ip: int = 50
    max_connections: int = 10_000

    @classmethod
    def from_env(cls) -> GatewayConfig:
        return cls(
            teltonika_tcp_port=int(os.environ.get("TELTONIKA_TCP_PORT", "5027")),
            teltonika_udp_port=int(os.environ.get("TELTONIKA_UDP_PORT", "5027")),
            health_port=int(os.environ.get("GATEWAY_HEALTH_PORT", "8081")),
            storage=os.environ.get("GATEWAY_STORAGE", "postgres"),
            allow_memory_storage=os.environ.get("GATEWAY_ALLOW_MEMORY_STORAGE", "0") == "1",
            dev_devices=tuple(i.strip() for i in os.environ.get("GATEWAY_DEV_DEVICES", "").split(",") if i.strip()),
            database_url=_database_url_from_env(),
            redis_url=_redis_url_from_env(),
            manual_ingest_token=os.environ.get("MANUAL_INGEST_TOKEN", ""),
            listen_host=os.environ.get("GATEWAY_LISTEN_HOST", "0.0.0.0"),
            reconcile_interval=float(os.environ.get("GATEWAY_RECONCILE_SECONDS", "10")),
            max_connections_per_ip=int(os.environ.get("GATEWAY_MAX_CONNECTIONS_PER_IP", "50")),
            max_connections=int(os.environ.get("GATEWAY_MAX_CONNECTIONS", "10000")),
        )

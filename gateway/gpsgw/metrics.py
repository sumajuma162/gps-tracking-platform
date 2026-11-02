"""Prometheus metrics for the gateway (spec A15), on a private registry so tests can create many."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

LAG_BUCKETS = (1, 5, 15, 30, 60, 300, 900, 3600, 6 * 3600, 24 * 3600, 7 * 24 * 3600)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.connections = Gauge("gpsgw_connections", "Open device connections", ["protocol"], registry=r)
        self.packets = Counter("gpsgw_packets", "Data packets received", ["protocol", "transport"], registry=r)
        self.records = Counter("gpsgw_records", "Records by outcome", ["protocol", "outcome"], registry=r)
        self.protocol_errors = Counter("gpsgw_protocol_errors", "Malformed or unsupported input",
                                       ["protocol", "code"], registry=r)
        self.unknown_devices = Counter("gpsgw_unknown_devices", "Unregistered identifiers refused", ["protocol"],
                                       registry=r)
        self.persist_failures = Counter("gpsgw_persist_failures", "Batches not stored (no ACK sent)", registry=r)
        self.ingest_lag = Histogram("gpsgw_ingest_lag_seconds", "Server time minus device time", ["protocol"],
                                    buckets=LAG_BUCKETS, registry=r)
        self.listeners = Gauge("gpsgw_listeners", "Active listeners", ["protocol", "transport"], registry=r)

    def render(self) -> bytes:
        return generate_latest(self.registry)


NULL_METRICS = Metrics()

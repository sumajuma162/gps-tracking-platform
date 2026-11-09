"""Ingest pipeline: clock fix -> normalize -> annotate -> persist -> (ACK) -> publish.

Enforces spec A4: the ACK count is returned only after persistence succeeds. Records rejected by validation
are counted as handled so the device clears them from flash; they are logged and counted, never stored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from gpsgw.metrics import NULL_METRICS, Metrics
from gpsgw.normalization.io_mapper import normalize
from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.normalization.validators import InvalidRecord, ValidationConfig
from gpsgw.protocols.base import RawRecord
from gpsgw.services.annotate import PositionAnnotator
from gpsgw.services.ports import DeviceInfo, Persistence, Publisher

log = logging.getLogger("gpsgw.pipeline")


@dataclass
class Stats:
    packets: int = 0
    records_in: int = 0
    records_stored: int = 0
    duplicates: int = 0
    late: int = 0
    outliers: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    protocol_errors: dict[str, int] = field(default_factory=dict)
    persist_failures: int = 0
    unknown_devices: int = 0

    def bump(self, bucket: dict[str, int], code: str) -> None:
        bucket[code] = bucket.get(code, 0) + 1


class PersistenceFailed(Exception):
    """Raised when records could not be stored; the caller must not ACK."""


class IngestPipeline:
    def __init__(self, persistence: Persistence, publisher: Publisher,
                 validation: ValidationConfig | None = None, stats: Stats | None = None,
                 metrics: Metrics | None = None, annotator: PositionAnnotator | None = None) -> None:
        self.persistence = persistence
        self.publisher = publisher
        self.validation = validation or ValidationConfig()
        self.stats = stats or Stats()
        self.metrics = metrics or NULL_METRICS
        self.annotator = annotator or PositionAnnotator(persistence.last_fix)

    def _normalize(self, device: DeviceInfo, raws: list[RawRecord], now: datetime) -> list[NormalizedLocation]:
        out: list[NormalizedLocation] = []
        offset = timedelta(minutes=device.time_offset_minutes)
        for raw in raws:
            if offset and not raw.extra.get("manual_position"):
                raw = replace(raw, device_time=raw.device_time - offset)
            try:
                out.append(normalize(raw, identifier=device.identifier, protocol=device.protocol,
                                     profile=device.profile, server_time=now, device_id=device.device_id,
                                     validation=self.validation))
            except InvalidRecord as exc:
                self.stats.bump(self.stats.rejected, exc.code)
                self.metrics.records.labels(device.protocol, "rejected").inc()
                log.warning("record rejected", extra={"device_id": device.device_id, "code": exc.code})
        return out

    async def ingest(self, device: DeviceInfo, raws: list[RawRecord]) -> int:
        """Store records and return the count to acknowledge."""
        now = datetime.now(UTC)
        self.stats.packets += 1
        self.stats.records_in += len(raws)
        normalized = self._normalize(device, raws, now)
        if normalized:
            annotated, track = await self.annotator.annotate(device.device_id, normalized)
            try:
                inserted = await self.persistence.save(device, annotated)
            except Exception as exc:  # any storage failure means: do not ACK
                self.stats.persist_failures += 1
                self.metrics.persist_failures.inc()
                log.error("persistence failed", extra={"device_id": device.device_id, "error": str(exc)})
                raise PersistenceFailed(str(exc)) from exc
            self.annotator.commit(device.device_id, track)
            duplicates = len(annotated) - inserted
            late = sum(r.is_late for r in annotated)
            outliers = sum(r.is_outlier for r in annotated)
            self.stats.records_stored += inserted
            self.stats.duplicates += duplicates
            self.stats.late += late
            self.stats.outliers += outliers
            m = self.metrics.records
            m.labels(device.protocol, "stored").inc(inserted)
            m.labels(device.protocol, "duplicate").inc(duplicates)
            m.labels(device.protocol, "late").inc(late)
            m.labels(device.protocol, "outlier").inc(outliers)
            for rec in annotated:
                self.metrics.ingest_lag.labels(device.protocol).observe(
                    max(0.0, (rec.server_time - rec.device_time).total_seconds()))
            try:
                await self.publisher.publish(device, annotated)
            except Exception as exc:  # DB is the source of truth; the processor catches up from it
                log.warning("publish failed", extra={"device_id": device.device_id, "error": str(exc)})
        return len(raws)

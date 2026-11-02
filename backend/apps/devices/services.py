"""Tracker assignment, enable/disable and CSV import (spec A7)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import record
from apps.device_catalog.models import DeviceModel
from apps.devices.events import device_changed
from apps.devices.identifiers import identifier_error, normalize_identifier
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import SimCard, Vehicle
from common.exceptions import ApiError
from common.rls import rls_bypass


def close_device_assignments(**filters: Any) -> list[int]:
    rows = DeviceAssignment.objects.filter(unassigned_at__isnull=True, **filters)
    device_ids = list(rows.values_list("device_id", flat=True))
    rows.update(unassigned_at=timezone.now())
    return device_ids


@transaction.atomic
def assign_device(device: Device, vehicle: Vehicle, actor: Any, ip: str | None) -> DeviceAssignment:
    """Install ``device`` in ``vehicle``. Replaces the vehicle's previous tracker; history is kept."""
    device = Device.objects.select_for_update().get(pk=device.pk)
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if not device.is_active:
        raise ApiError("device_disabled", "Enable this tracker before assigning it.")
    if vehicle.status == "inactive":
        raise ApiError("vehicle_inactive", "This vehicle is inactive.")
    replaced = close_device_assignments(vehicle=vehicle)
    moved_from = list(DeviceAssignment.objects.filter(device=device, unassigned_at__isnull=True)
                      .values_list("vehicle__registration_number", flat=True))
    close_device_assignments(device=device)
    assignment = DeviceAssignment.objects.create(organization_id=device.organization_id, device=device,
                                                 vehicle=vehicle, assigned_at=timezone.now(), assigned_by=actor)
    device.inventory_status = "installed"
    device.installation_date = device.installation_date or timezone.localdate()
    device.save(update_fields=["inventory_status", "installation_date", "updated_at"])
    record("devices.assigned", user=actor, organization=device.organization, resource=assignment, ip=ip,
           metadata={"device": device.identifier, "vehicle": vehicle.registration_number,
                     "replaced_device_ids": replaced, "moved_from": moved_from})
    device_changed(device, "assigned")
    return assignment


@transaction.atomic
def unassign_device(device: Device, actor: Any, ip: str | None) -> None:
    Device.objects.select_for_update().get(pk=device.pk)
    if close_device_assignments(device=device):
        record("devices.unassigned", user=actor, organization=device.organization, resource=device, ip=ip,
               metadata={"device": device.identifier})
        device_changed(device, "unassigned")


def set_active(device: Device, active: bool, actor: Any, ip: str | None) -> None:
    device.is_active = active
    device.save(update_fields=["is_active", "updated_at"])
    record("devices.enabled" if active else "devices.disabled", user=actor, organization=device.organization,
           resource=device, ip=ip, metadata={"device": device.identifier})
    device_changed(device, "enabled" if active else "disabled")


def identifier_taken(identifier: str) -> bool:
    """Platform-wide check (crosses tenants on purpose; reveals only yes/no, never the owner)."""
    with rls_bypass():
        return Device.unscoped.filter(identifier=identifier, deleted_at__isnull=True).exists()


# --- CSV import ---------------------------------------------------------------------------------------

IMPORT_COLUMNS = ["model", "identifier", "name", "serial_number", "sim_phone", "vehicle_registration"]
MAX_IMPORT_ROWS = 1000


@dataclass
class ImportRow:
    line: int
    values: dict[str, str]
    errors: list[str] = field(default_factory=list)
    device_model: DeviceModel | None = None
    sim: SimCard | None = None
    vehicle: Vehicle | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "identifier": self.values.get("identifier", ""),
                "name": self.values.get("name", ""), "ok": not self.errors, "errors": self.errors}


def parse_import(text: str) -> list[ImportRow]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = {"model", "identifier", "name"} - set(header)
    if missing:
        raise ApiError("import_header", f"The first line must contain the columns: {', '.join(IMPORT_COLUMNS)}. "
                                        f"Missing: {', '.join(sorted(missing))}.")
    reader.fieldnames = header
    rows: list[ImportRow] = []
    for i, raw in enumerate(reader, start=2):
        if i - 1 > MAX_IMPORT_ROWS:
            raise ApiError("import_too_large", f"Import at most {MAX_IMPORT_ROWS} trackers at a time.")
        values = {k: (v or "").strip() for k, v in raw.items() if k}
        if not any(values.values()):
            continue
        rows.append(ImportRow(i, values))
    if not rows:
        raise ApiError("import_empty", "The file has no tracker rows.")
    return rows


def validate_import(rows: list[ImportRow]) -> None:
    models = {m.slug: m for m in DeviceModel.objects.filter(status="active")}
    seen: set[str] = set()
    for row in rows:
        v = row.values
        model = models.get(v.get("model", ""))
        if model is None:
            row.errors.append(f"Unknown or deprecated model '{v.get('model', '')}'. Use the model's slug.")
        identifier = normalize_identifier(v.get("identifier", ""))
        v["identifier"] = identifier
        if model and (problem := identifier_error(identifier, model.identifier_type, model.identifier_pattern)):
            row.errors.append(problem)
        elif identifier in seen:
            row.errors.append("This identifier appears twice in the file.")
        elif identifier and identifier_taken(identifier):
            row.errors.append("This identifier is already registered.")
        seen.add(identifier)
        if not v.get("name"):
            row.errors.append("Name is required.")
        elif len(v["name"]) > 100:
            row.errors.append("Name is longer than 100 characters.")
        if v.get("sim_phone"):
            row.sim = SimCard.objects.filter(phone_number=v["sim_phone"], deleted_at__isnull=True).first()
            if row.sim is None:
                row.errors.append(f"No SIM card with phone number {v['sim_phone']}. Add it under SIM cards first.")
            elif row.sim.devices.filter(deleted_at__isnull=True).exists():
                row.errors.append("That SIM card is already in another tracker.")
        if v.get("vehicle_registration"):
            reg = " ".join(v["vehicle_registration"].upper().split())
            row.vehicle = Vehicle.objects.filter(registration_number=reg, deleted_at__isnull=True).first()
            if row.vehicle is None:
                row.errors.append(f"No vehicle with registration {reg}.")
        row.device_model = model


def commit_import(rows: list[ImportRow], organization: Any, actor: Any, ip: str | None) -> int:
    """Create every valid row in one transaction and one audit entry; invalid rows are skipped."""
    created = 0
    with transaction.atomic():
        for row in rows:
            if row.errors:
                continue
            try:
                with transaction.atomic():
                    device = Device.objects.create(
                        organization=organization, name=row.values["name"], identifier=row.values["identifier"],
                        device_model=row.device_model, serial_number=row.values.get("serial_number", ""),
                        sim=row.sim)
            except IntegrityError:
                row.errors.append("This identifier was registered while the import was running.")
                continue
            created += 1
            device_changed(device, "created")
            if row.vehicle:
                assign_device(device, row.vehicle, actor, ip)
        record("devices.imported", user=actor, organization=organization, ip=ip,
               metadata={"created": created, "skipped": sum(1 for r in rows if r.errors)})
    return created

"""Driver assignment rules (spec A9: assignment history is kept, never overwritten)."""
from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.fleet.models import Driver, DriverAssignment, Vehicle
from common.exceptions import ApiError


@transaction.atomic
def assign_driver(driver: Driver, vehicle: Vehicle, actor: Any, ip: str | None) -> DriverAssignment:
    driver = Driver.objects.select_for_update().get(pk=driver.pk)
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if driver.status != "active":
        raise ApiError("driver_inactive", "Activate this driver before assigning a vehicle.")
    if vehicle.status == "inactive":
        raise ApiError("vehicle_inactive", "This vehicle is inactive.")
    now = timezone.now()
    open_rows = DriverAssignment.objects.filter(unassigned_at__isnull=True).filter(driver=driver) | \
        DriverAssignment.objects.filter(unassigned_at__isnull=True, vehicle=vehicle)
    ended = [(a.driver_id, a.vehicle_id) for a in open_rows]
    open_rows.update(unassigned_at=now)
    assignment = DriverAssignment.objects.create(organization_id=driver.organization_id, driver=driver,
                                                 vehicle=vehicle, assigned_at=now, assigned_by=actor)
    record("drivers.assigned", user=actor, organization=driver.organization, resource=assignment, ip=ip,
           metadata={"driver": driver.full_name, "vehicle": vehicle.registration_number, "ended": ended})
    return assignment


@transaction.atomic
def unassign_driver(vehicle: Vehicle, actor: Any, ip: str | None) -> int:
    rows = DriverAssignment.objects.select_for_update().filter(vehicle=vehicle, unassigned_at__isnull=True)
    count = rows.update(unassigned_at=timezone.now())
    if count:
        record("drivers.unassigned", user=actor, organization=vehicle.organization, resource=vehicle, ip=ip,
               metadata={"vehicle": vehicle.registration_number})
    return count


def close_driver_assignments(**filters: Any) -> None:
    DriverAssignment.objects.filter(unassigned_at__isnull=True, **filters).update(unassigned_at=timezone.now())

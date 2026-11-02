"""Vehicles, vehicle groups, drivers, SIM cards and driver assignments (spec A9). All tenant-owned."""
from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from common.tenancy import TenantModel

phone_validator = RegexValidator(r"^\+?[0-9 ()-]{6,20}$", "Enter a phone number like +255 712 345 678.")


class SoftDeleteMixin(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        abstract = True


class VehicleGroup(TenantModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "vehicle_groups"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_group_name")]

    def __str__(self) -> str:
        return self.name


class Vehicle(SoftDeleteMixin, TenantModel):
    STATUS = [("active", "Active"), ("maintenance", "In maintenance"), ("inactive", "Inactive")]
    TYPES = [("car", "Car"), ("pickup", "Pickup"), ("van", "Van"), ("truck", "Truck"), ("bus", "Bus"),
             ("motorcycle", "Motorcycle"), ("trailer", "Trailer"), ("machinery", "Machinery"), ("other", "Other")]
    FUELS = [("diesel", "Diesel"), ("petrol", "Petrol"), ("electric", "Electric"), ("hybrid", "Hybrid"),
             ("lpg", "LPG"), ("other", "Other")]

    registration_number = models.CharField(max_length=20)
    vin = models.CharField(max_length=17, blank=True)
    make = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True,
                                            validators=[MinValueValidator(1950), MaxValueValidator(2100)])
    vehicle_type = models.CharField(max_length=12, choices=TYPES, default="truck")
    color = models.CharField(max_length=30, blank=True)
    fuel_type = models.CharField(max_length=10, choices=FUELS, default="diesel")
    fuel_capacity_l = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="active")
    odometer_km = models.DecimalField(max_digits=10, decimal_places=1, default=0)
    groups = models.ManyToManyField(VehicleGroup, blank=True, related_name="vehicles", db_table="vehicle_group_members")
    TRIP_MODES = [("", "Device model default"), ("ignition", "Ignition"), ("motion", "Motion"), ("speed", "Speed")]
    trip_detection_mode = models.CharField(max_length=10, choices=TRIP_MODES, blank=True, default="",
                                           help_text="Overrides the device model's trip detection mode (spec A10).")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "vehicles"
        ordering = ["registration_number"]
        constraints = [models.UniqueConstraint(fields=["organization", "registration_number"],
                                               condition=Q(deleted_at__isnull=True), name="uniq_vehicle_registration")]

    def __str__(self) -> str:
        return self.registration_number


class Driver(SoftDeleteMixin, TenantModel):
    STATUS = [("active", "Active"), ("inactive", "Inactive")]

    first_name = models.CharField(max_length=60)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60)
    phone = models.CharField(max_length=24, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    employee_number = models.CharField(max_length=30, blank=True)
    license_number = models.CharField(max_length=30, blank=True)
    license_class = models.CharField(max_length=20, blank=True)
    license_expiry = models.DateField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=24, blank=True, validators=[phone_validator])
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="driver_profiles", help_text="Login for the optional driver portal.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "drivers"
        ordering = ["last_name", "first_name"]
        constraints = [models.UniqueConstraint(fields=["organization", "employee_number"],
                                               condition=Q(deleted_at__isnull=True) & ~Q(employee_number=""),
                                               name="uniq_driver_employee_number")]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)


class DriverAssignment(TenantModel):
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="assignments")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="driver_assignments")
    assigned_at = models.DateTimeField()
    unassigned_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta(TenantModel.Meta):
        db_table = "driver_assignments"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(fields=["driver"], condition=Q(unassigned_at__isnull=True),
                                    name="one_open_assignment_per_driver"),
            models.UniqueConstraint(fields=["vehicle"], condition=Q(unassigned_at__isnull=True),
                                    name="one_open_driver_per_vehicle"),
        ]

    def __str__(self) -> str:
        return f"{self.driver_id}->{self.vehicle_id}"


class SimCard(SoftDeleteMixin, TenantModel):
    STATUS = [("active", "Active"), ("suspended", "Suspended"), ("cancelled", "Cancelled")]

    phone_number = models.CharField(max_length=24, validators=[phone_validator])
    iccid = models.CharField(max_length=22, blank=True,
                             validators=[RegexValidator(r"^[0-9]{18,22}$", "ICCID has 18-22 digits.")])
    operator = models.CharField(max_length=60, blank=True)
    apn = models.CharField(max_length=100, blank=True)
    apn_username = models.CharField(max_length=60, blank=True)
    apn_password = models.CharField(max_length=60, blank=True)
    data_plan = models.CharField(max_length=100, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    #: The expiry date an alert was raised for (renewing the SIM re-arms the alert).
    expiry_notified_for = models.DateField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(TenantModel.Meta):
        db_table = "sim_cards"
        ordering = ["phone_number"]
        constraints = [
            models.UniqueConstraint(fields=["iccid"], condition=Q(deleted_at__isnull=True) & ~Q(iccid=""),
                                    name="uniq_sim_iccid"),
            models.UniqueConstraint(fields=["organization", "phone_number"], condition=Q(deleted_at__isnull=True),
                                    name="uniq_sim_phone"),
        ]

    def __str__(self) -> str:
        return self.phone_number

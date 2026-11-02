"""Platform-wide device catalog: Manufacturer -> Device model -> (tenant) Device (spec A7)."""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

TRANSPORTS = [("tcp", "TCP"), ("udp", "UDP"), ("http", "HTTP")]
CAPABILITIES = ["ignition", "odometer", "engine_hours", "external_voltage", "battery", "gsm_signal", "commands",
                "harsh_driving", "crash", "jamming", "fuel_sensor", "temperature_sensor",
                "output_control"]   # output_control: the model may receive dangerous output commands (spec A13)


class DeviceManufacturer(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    website = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "device_manufacturers"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DeviceProtocol(models.Model):
    """One row per decoder plugin the gateway knows (synced from its registry in Phase 3)."""

    name = models.SlugField(max_length=40, unique=True, help_text="Registry name, e.g. 'teltonika'.")
    display_name = models.CharField(max_length=100)
    transports = models.JSONField(default=list)
    supports_commands = models.BooleanField(default=False)
    implemented = models.BooleanField(default=False, help_text="A decoder exists in the running gateway.")
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "device_protocols"
        ordering = ["display_name"]

    def __str__(self) -> str:
        return self.display_name


class IoMappingProfile(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=80, unique=True)
    protocol = models.ForeignKey(DeviceProtocol, on_delete=models.PROTECT, related_name="io_profiles")
    description = models.TextField(blank=True)
    verified = models.BooleanField(default=False, help_text="Checked against a real device and its firmware.")
    current_version = models.ForeignKey("IoMappingProfileVersion", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "io_mapping_profiles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class IoMappingProfileVersion(models.Model):
    """Immutable snapshot of a profile's mappings. Editing creates a new version; rollback re-points."""

    profile = models.ForeignKey(IoMappingProfile, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    mappings = models.JSONField()
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "io_mapping_profile_versions"
        ordering = ["profile", "-version"]
        constraints = [models.UniqueConstraint(fields=["profile", "version"], name="uniq_profile_version")]

    def __str__(self) -> str:
        return f"{self.profile.slug} v{self.version}"


class GenericFormat(models.Model):
    """A tracker message format defined in the UI (spec A7 "Generic configurable protocol"). ``definition`` is the
    gateway's ``GenericFormat`` schema; the gateway validates and parses it (Django never parses tracker data)."""

    KINDS = [("json", "JSON over HTTP"), ("delimited", "Delimited text over TCP/UDP")]

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True, help_text="HTTP trackers post to /<slug> on the HTTP port.")
    kind = models.CharField(max_length=10, choices=KINDS)
    definition = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "generic_formats"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DeviceModel(models.Model):
    STATUS = [("active", "Active"), ("deprecated", "Deprecated")]
    IDENTIFIER_TYPES = [("imei", "IMEI (15 digits, Luhn)"), ("serial", "Serial number"), ("custom", "Custom pattern")]
    TRIP_MODES = [("ignition", "Ignition"), ("motion", "Motion"), ("speed", "Speed")]

    manufacturer = models.ForeignKey(DeviceManufacturer, on_delete=models.PROTECT, related_name="models")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=80, unique=True)
    protocol = models.ForeignKey(DeviceProtocol, on_delete=models.PROTECT, related_name="device_models")
    transports = models.JSONField(default=list)
    default_port = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(65535)])
    identifier_type = models.CharField(max_length=10, choices=IDENTIFIER_TYPES, default="imei")
    identifier_pattern = models.CharField(max_length=200, blank=True,
                                          help_text="Regular expression; only for identifier_type=custom.")
    capabilities = models.JSONField(default=dict)
    io_profile = models.ForeignKey(IoMappingProfile, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="device_models")
    time_offset_minutes = models.SmallIntegerField(default=0, validators=[MinValueValidator(-840),
                                                                          MaxValueValidator(840)])
    trip_detection_mode = models.CharField(max_length=10, choices=TRIP_MODES, default="ignition")
    generic_format = models.ForeignKey(GenericFormat, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="device_models",
                                       help_text="For generic protocols: the format this model's trackers send.")
    setup_instructions = models.TextField(blank=True, help_text="Markdown with {server_host}, {port}, {apn}.")
    status = models.CharField(max_length=12, choices=STATUS, default="active")
    verified = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True, help_text="Listed on the public landing page.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "device_models"
        ordering = ["manufacturer__name", "name"]

    def __str__(self) -> str:
        return f"{self.manufacturer.name} {self.name}"

    def clean(self) -> None:
        bad = set(self.transports) - {t for t, _ in TRANSPORTS}
        if bad:
            raise ValidationError({"transports": f"Unknown transports: {sorted(bad)}"})
        unknown = set(self.capabilities) - set(CAPABILITIES)
        if unknown:
            raise ValidationError({"capabilities": f"Unknown capabilities: {sorted(unknown)}"})
        if self.identifier_type == "custom" and not self.identifier_pattern:
            raise ValidationError({"identifier_pattern": "Custom identifiers need a pattern."})


class ListenerPort(models.Model):
    port = models.PositiveIntegerField(validators=[MinValueValidator(1024), MaxValueValidator(65535)])
    transport = models.CharField(max_length=4, choices=TRANSPORTS)
    protocol = models.ForeignKey(DeviceProtocol, on_delete=models.PROTECT, related_name="listener_ports")
    generic_format = models.ForeignKey(GenericFormat, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="listener_ports",
                                       help_text="Delimited-text ports: the format every message on this port uses.")
    is_enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "listener_ports"
        ordering = ["port", "transport"]
        constraints = [models.UniqueConstraint(fields=["port", "transport"], name="uniq_port_transport")]

    def __str__(self) -> str:
        return f"{self.port}/{self.transport}"


class CommandTemplate(models.Model):
    device_model = models.ForeignKey(DeviceModel, on_delete=models.CASCADE, related_name="command_templates")
    name = models.CharField(max_length=80)
    command_text = models.CharField(max_length=500)
    description = models.CharField(max_length=300, blank=True)
    is_verified = models.BooleanField(default=False, help_text="Tested on a real device of this model.")
    is_dangerous = models.BooleanField(default=False, help_text="Changes outputs or configuration.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "command_templates"
        ordering = ["device_model", "name"]
        constraints = [models.UniqueConstraint(fields=["device_model", "name"], name="uniq_model_command")]

    def __str__(self) -> str:
        return f"{self.device_model.slug}:{self.name}"

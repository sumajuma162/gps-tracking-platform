"""Settings shared by every environment. Secrets come only from the environment."""
from __future__ import annotations

from pathlib import Path

from config.settings.env import env, env_bool, env_int, env_list

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "channels",
    "apps.core",
    "apps.accounts",
    "apps.organizations",
    "apps.rbac",
    "apps.audit",
    "apps.device_catalog",
    "apps.fleet",
    "apps.devices",
    "apps.tracking",
    "apps.analysis",
    "apps.geofences",
    "apps.alerts",
    "apps.commands",
    "apps.reports",
    "apps.sharing",
    "apps.settings_app",
    "apps.integrations",
    "apps.maintenance",
    "apps.analytics",
    "apps.realtime",
    "apps.health",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "common.metrics.MetricsMiddleware",
    "common.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

# PostgreSQL 16 + PostGIS + TimescaleDB. Phase 2 switches ENGINE to the PostGIS backend.
DATABASES = {
    "default": {
        "ENGINE": env("DATABASE_ENGINE", "django.db.backends.postgresql"),
        "NAME": env("DATABASE_NAME", "gps"),
        "USER": env("DATABASE_USER", "gps"),
        "PASSWORD": env("DATABASE_PASSWORD", ""),
        "HOST": env("DATABASE_HOST", "postgres"),
        "PORT": env("DATABASE_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DATABASE_CONN_MAX_AGE", 60),
        # Each request is one transaction, so the RLS context (set_config ..., true) cannot leak.
        "ATOMIC_REQUESTS": True,
    }
}
#: Restricted PostgreSQL role used for web requests so Row-Level Security applies (empty = disabled).
DB_RUNTIME_ROLE = env("DB_RUNTIME_ROLE", "")

REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")

CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL,
                      "KEY_PREFIX": "gps"}}

CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
                              "CONFIG": {"hosts": [REDIS_URL]}}}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = "UTC"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("sw", "Kiswahili")]
TIME_ZONE = "UTC"  # stored in UTC; displayed per organization (spec A9)
DEFAULT_TIMEZONE = env("DEFAULT_TIMEZONE", "Africa/Dar_es_Salaam")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardPagination",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle",
                                 "rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "user": env("THROTTLE_USER", "1200/min"),
        "login": env("THROTTLE_LOGIN", "10/min"),
        "refresh": env("THROTTLE_REFRESH", "30/min"),
        "password": env("THROTTLE_PASSWORD", "5/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Fleet Tracking Platform API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Shared choice sets get one stable name each in the schema (and in generated clients).
    "ENUM_NAME_OVERRIDES": {
        "AlertTypeEnum": "apps.alerts.models.ALERT_TYPES",
        "AlertSeverityEnum": "apps.alerts.models.SEVERITIES",
        "NotificationChannelEnum": "apps.alerts.models.CHANNELS",
        "SpeedEventSeverityEnum": "apps.analysis.models.SpeedEvent.SEVERITY",
        "GeofenceKindEnum": "apps.geofences.models.Geofence.KINDS",
        "ReportFormatEnum": "apps.reports.models.FORMATS",
        "PermissionCodenameEnum": "apps.rbac.catalog.PERMISSION_CHOICES",
    },
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- Authentication (spec A8) ---------------------------------------------
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY", SECRET_KEY)
JWT_ISSUER = "gps-platform"
JWT_ACCESS_MINUTES = env_int("JWT_ACCESS_MINUTES", 10)
JWT_REFRESH_DAYS = env_int("JWT_REFRESH_DAYS", 7)
REFRESH_COOKIE_NAME = "gps_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth/"
REFRESH_COOKIE_SECURE = env_bool("REFRESH_COOKIE_SECURE", True)
CSRF_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_SAMESITE = "Strict"
LOCKOUT_THRESHOLD = env_int("LOCKOUT_THRESHOLD", 5)
LOCKOUT_WINDOW_MINUTES = env_int("LOCKOUT_WINDOW_MINUTES", 15)
LOCKOUT_MINUTES = env_int("LOCKOUT_MINUTES", 15)
PASSWORD_RESET_TIMEOUT = env_int("PASSWORD_RESET_TIMEOUT_SECONDS", 24 * 3600)
BREAK_GLASS_HOURS = env_int("BREAK_GLASS_HOURS", 2)
FRONTEND_URL = env("FRONTEND_URL", "http://localhost").rstrip("/")

# --- Tracking (spec A7, A9) -------------------------------------------------
#: Public hostname trackers connect to (shown in setup instructions).
TRACKER_SERVER_HOST = env("TRACKER_SERVER_HOST", "gps.example.com")
#: Ports Docker publishes for the gateway; UI-assigned listeners must fall inside these.
LISTENER_PUBLISHED_PORTS = [int(p) for p in env_list("LISTENER_PUBLISHED_PORTS", "5023,5027,5055")]
LISTENER_RESERVED_RANGE = tuple(int(p) for p in env("LISTENER_RESERVED_RANGE", "5100-5109").split("-"))
#: Platform maximum for location history (TimescaleDB retention policy, spec A9).
POSITIONS_MAX_RETENTION_DAYS = env_int("POSITIONS_MAX_RETENTION_DAYS", 730)
#: Raw tracker packets kept for troubleshooting (spec A4 rule 10).
RAW_PACKET_RETENTION_DAYS = env_int("RAW_PACKET_RETENTION_DAYS", 14)
#: Fail migrations when TimescaleDB is missing (set 0 only for quick local tests on plain PostgreSQL).
REQUIRE_TIMESCALEDB = env_bool("REQUIRE_TIMESCALEDB", True)
#: Map tiles (spec A12). The public OpenStreetMap servers are NOT for production fleet use: set a provider or
#: self-hosted tiles. Empty = no basemap (markers on a plain background), useful offline and in tests.
MAP_TILE_URL = env("MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
MAP_TILE_ATTRIBUTION = env("MAP_TILE_ATTRIBUTION", "© OpenStreetMap contributors")
MAP_DEFAULT_CENTER = tuple(float(v) for v in env("MAP_DEFAULT_CENTER", "-6.7924,39.2083").split(","))
MAP_DEFAULT_ZOOM = env_int("MAP_DEFAULT_ZOOM", 12)
#: System health page: internal health endpoints of the gateway and processor.
GATEWAY_HEALTH_URL = env("GATEWAY_HEALTH_URL", "http://gateway:8081/health")
GATEWAY_MANUAL_INGEST_URL = env("GATEWAY_MANUAL_INGEST_URL", "http://gateway:8081/manual-position")
#: The gateway's internal HTTP port (health, metrics, generic-format test parse). Never published.
GATEWAY_INTERNAL_URL = env("GATEWAY_INTERNAL_URL", GATEWAY_HEALTH_URL.rsplit("/health", 1)[0]).rstrip("/")
PROCESSOR_HEALTH_URL = env("PROCESSOR_HEALTH_URL", "http://processor:8082/health")
#: Reports (spec A13): exports above these limits run as background jobs; download links expire.
REPORT_SYNC_MAX_ROWS = env_int("REPORT_SYNC_MAX_ROWS", 10_000)
REPORT_SYNC_MAX_DAYS = env_int("REPORT_SYNC_MAX_DAYS", 31)
REPORT_LINK_HOURS = env_int("REPORT_LINK_HOURS", 24)
#: Commands not delivered within this time (the tracker never connected) time out.
COMMAND_EXPIRY_HOURS = env_int("COMMAND_EXPIRY_HOURS", 24)
#: Celery beat (spec A11: queued email notifications are delivered with retries).
CELERY_BEAT_SCHEDULE = {
    "deliver-queued-notifications": {"task": "alerts.deliver_queued_notifications", "schedule": 15.0},
    "run-scheduled-reports": {"task": "reports.run_scheduled", "schedule": 300.0},
    "purge-expired-report-files": {"task": "reports.purge_expired", "schedule": 3600.0},
    "dispatch-webhooks": {"task": "integrations.dispatch_webhooks", "schedule": 5.0},
    "maintenance-and-sim-checks": {"task": "maintenance.check_due", "schedule": 3600.0},
    "apply-retention": {"task": "settings.apply_retention", "schedule": 86400.0},
}
#: Prometheus scraping of /metrics (backend container only; Nginx doesn't route it). Bearer token when set.
METRICS_TOKEN = env("METRICS_TOKEN", "")
#: Sentry (spec A15): enabled only when SENTRY_DSN is set. No personal data is sent.
SENTRY_DSN = env("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=SENTRY_DSN, environment=env("SENTRY_ENVIRONMENT", "production"), send_default_pii=False,
                    traces_sample_rate=float(env("SENTRY_TRACES_SAMPLE_RATE", "0")))
#: Webhooks (spec A13): request timeout, and SSRF guards (production: https only, public addresses only).
WEBHOOK_TIMEOUT = float(env("WEBHOOK_TIMEOUT", "10"))
WEBHOOK_ALLOW_HTTP = env_bool("WEBHOOK_ALLOW_HTTP", False)
WEBHOOK_ALLOW_PRIVATE = env_bool("WEBHOOK_ALLOW_PRIVATE", False)
#: Route history (spec A12 / A15): longest range per request, and the drawing point budget.
HISTORY_MAX_DAYS = env_int("HISTORY_MAX_DAYS", 31)
HISTORY_POINT_BUDGET = env_int("HISTORY_POINT_BUDGET", 5000)
#: Analysis defaults (spec A10); per-organization settings arrive with the Settings page (Phase 9).
STOP_SPEED_KMH = float(env("STOP_SPEED_KMH", "3"))
OVERSPEED_WARNING_KMH = float(env("OVERSPEED_WARNING_KMH", "80"))
OVERSPEED_MIN_SECONDS = env_int("OVERSPEED_MIN_SECONDS", 15)
#: Live map: updates are batched per browser and sent at most once per this many seconds (spec A12).
WS_TRACKING_FLUSH = float(env("WS_TRACKING_FLUSH", "1.0"))
#: Seconds between connection-test refreshes pushed over the WebSocket.
WS_DIAGNOSTICS_INTERVAL = float(env("WS_DIAGNOSTICS_INTERVAL", "1.0"))

# --- Email ------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
#: For django.core.mail.backends.filebased.EmailBackend (browser tests read invitations from here).
EMAIL_FILE_PATH = env("EMAIL_FILE_PATH", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Fleet Tracking <no-reply@localhost>")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"format": (
        '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

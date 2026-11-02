"""Test settings. CI uses PostgreSQL; set TEST_SQLITE=1 for a DB-less quick run."""
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-secret-0123456789abcdefghijklmnopqrstuvwxyz")
os.environ.setdefault("DATABASE_HOST", "127.0.0.1")
from config.settings.base import *  # noqa: E402,F403

if os.environ.get("TEST_SQLITE") == "1":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
# Close connections after each use so worker threads (WebSocket consumers) never block dropping the test database.
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REFRESH_COOKIE_SECURE = False
DB_RUNTIME_ROLE = ""
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {  # noqa: F405
    "user": "100000/min", "login": "1000/min", "refresh": "1000/min", "password": "1000/min"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
WEBHOOK_ALLOW_HTTP = True
WEBHOOK_ALLOW_PRIVATE = True   # tests receive webhooks on a local HTTP server
WEBHOOK_TIMEOUT = 3.0

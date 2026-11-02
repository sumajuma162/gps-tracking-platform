import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.realtime.auth import TicketAuthMiddleware  # noqa: E402
from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # Origin must match ALLOWED_HOSTS (blocks other websites from opening sockets with a user's ticket).
    "websocket": AllowedHostsOriginValidator(TicketAuthMiddleware(URLRouter(websocket_urlpatterns))),
})

from django.urls import path

from apps.realtime.consumers import DeviceDiagnosticsConsumer
from apps.realtime.notifications import NotificationConsumer
from apps.realtime.tracking import TrackingConsumer

websocket_urlpatterns = [
    path("ws/devices/<int:device_id>/diagnostics/", DeviceDiagnosticsConsumer.as_asgi()),
    path("ws/tracking/", TrackingConsumer.as_asgi()),
    path("ws/notifications/", NotificationConsumer.as_asgi()),
]

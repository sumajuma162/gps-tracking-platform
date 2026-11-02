"""/ws/notifications/: live in-app notifications for the signed-in user (spec A11 notification center)."""
from __future__ import annotations

import json
from typing import Any

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.realtime.consumers import CLOSE_UNAUTHENTICATED


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        self.group = f"notify.user.{user.pk}"           # only ever this user's own notifications
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        from common.metrics import ws_opened
        ws_opened("notifications")
        self._counted = True

    async def disconnect(self, code: int) -> None:
        if getattr(self, "_counted", False):
            from common.metrics import ws_closed
            ws_closed("notifications")
            self._counted = False
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def notify(self, event: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps({"type": "notification", "notification": event["notification"]}))

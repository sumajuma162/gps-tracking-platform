from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny, IsAuthenticated

from common.metrics import metrics_view

_docs_perm = [AllowAny] if settings.DEBUG else [IsAuthenticated]

urlpatterns = [
    path("", include("apps.health.urls")),
    path("api/v1/", include("config.api_urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(permission_classes=_docs_perm), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema", permission_classes=_docs_perm),
         name="api-docs"),
    path("admin/", admin.site.urls),
    path("metrics", metrics_view, name="metrics"),
]

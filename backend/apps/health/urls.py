from django.urls import path

from apps.health import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("readiness", views.readiness, name="readiness"),
]

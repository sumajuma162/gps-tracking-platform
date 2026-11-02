from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("me/", views.MeView.as_view(), name="auth-me"),
    path("password/change/", views.PasswordChangeView.as_view(), name="auth-password-change"),
    path("password/forgot/", views.PasswordForgotView.as_view(), name="auth-password-forgot"),
    path("password/reset/", views.PasswordResetView.as_view(), name="auth-password-reset"),
    path("sessions/", views.SessionListView.as_view(), name="auth-sessions"),
    path("sessions/revoke-others/", views.SessionRevokeOthersView.as_view(), name="auth-sessions-revoke-others"),
    path("sessions/<uuid:session_id>/", views.SessionRevokeView.as_view(), name="auth-session-revoke"),
    path("ws-ticket/", views.WebSocketTicketView.as_view(), name="auth-ws-ticket"),
]

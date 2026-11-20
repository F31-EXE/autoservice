from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.today, name="today"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("stations/", views.station_list, name="station_list"),
    path("stations/<int:pk>/", views.station_detail, name="station_detail"),
    path("stations/switch/", views.switch_station, name="switch_station"),
]

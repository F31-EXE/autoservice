from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.client_list, name="list"),
    path("<int:pk>/", views.client_detail, name="detail"),
    path("<int:pk>/action/", views.client_action, name="action"),
    path("vehicles/<int:pk>/sold/", views.vehicle_sold, name="vehicle_sold"),
]

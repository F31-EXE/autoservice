from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.results, name="results"),
    path("export.csv", views.results_csv, name="results_csv"),
]

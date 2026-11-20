from django.urls import path

from . import views

app_name = "workorders"

urlpatterns = [
    path("", views.import_list, name="import_list"),
    path("upload/", views.import_upload, name="import_upload"),
    path("<int:pk>/", views.import_detail, name="import_detail"),
    path("<int:pk>/mapping/", views.import_mapping, name="import_mapping"),
]

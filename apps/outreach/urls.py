from django.urls import path

from . import views

app_name = "outreach"

urlpatterns = [
    path("<int:pk>/approve/", views.approve, name="approve"),
    path("<int:pk>/call/", views.call, name="call"),
    path("<int:pk>/skip/", views.skip, name="skip"),
    path("<int:pk>/text/", views.edit_text, name="edit_text"),
    path("<int:pk>/row/", views.row, name="row"),
    path("bulk-approve/", views.bulk_approve, name="bulk_approve"),
    path("rebuild/", views.rebuild, name="rebuild"),
]

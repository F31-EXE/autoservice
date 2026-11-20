from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("max/webhook/", views.max_webhook, name="max_webhook"),
    path("sms/inbound/", views.sms_inbound, name="sms_inbound"),
    path("dialogs/", views.dialogs, name="dialogs"),
    path("dialogs/<int:client_id>/", views.conversation, name="conversation"),
    path("bookings/<int:pk>/", views.booking_action, name="booking_action"),
    path("calls/<int:pk>/", views.call_outcome, name="call_outcome"),
]

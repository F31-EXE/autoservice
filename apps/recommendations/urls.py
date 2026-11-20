from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("", views.recommendation_list, name="list"),
    path("review/", views.review_list, name="review"),
    path("<int:pk>/review/", views.review_action, name="review_action"),
]
